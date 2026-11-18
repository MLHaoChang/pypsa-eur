"""
Per-project report store (WP1).

Layout, under the project directory (`AuthorizedProject.directory`):

    reports/<report_id>/meta.json            ReportMeta
    reports/<report_id>/v1.json, v2.json …   ReportDocument, one per version
    reports/<report_id>/figures/<figure_id>.png

`reports/` is in `routers/projects._BUNDLE_DIRS`, so it travels with save-as,
copy, scenario fork, snapshot create + restore and bundle export + import
exactly as `uploads/` does.

Rules:

* A version file is NEVER overwritten. A regenerate is `v<N+1>.json`; the
  earlier versions stay readable. The next number is taken from the files on
  disk, not only from the meta, so a stray `v<N>.json` is stepped over rather
  than replaced.
* Every write goes through `services/atomic_io.py`, so a killed process
  leaves the previous file or the new one, never a prefix.
* Ids are validated HERE, at the service boundary, with an anchored regex —
  the same shape as `upload_service._FILE_ID_RE`. Both the router and the
  chat tools (WP6) reach this module, and the chat path carries a
  model-supplied string with no route converter in front of it, so this is
  the only place a check protects both callers (the lesson
  `tests/test_upload_traversal.py` records). A path is only ever joined from
  the project directory and a token that matched the regex.
* An unreadable `meta.json` is skipped with one warning, as
  `upload_service.list_uploads` skips a half-written upload, so one corrupt
  report does not hide the others.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import secrets
import shutil
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, UTC

from pydantic import ValidationError

from models.report import ReportDocument, ReportMeta, RevisedFrom
from services.atomic_io import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

REPORT_ID_RE = r"\A[0-9a-f]{16}\Z"
_REPORT_ID = re.compile(REPORT_ID_RE)
# Figure ids are slugs the job assigns (`fmea_pareto`, `frontier`), never
# user input; the charset excludes every path character all the same.
FIGURE_ID_RE = r"\A[A-Za-z0-9_-]{1,64}\Z"
_FIGURE_ID = re.compile(FIGURE_ID_RE)
_VERSION_FILE = re.compile(r"\Av(\d+)\.json\Z")

REPORTS_DIRNAME = "reports"


class ReportStoreError(ValueError):
    """Base of the store's refusals; each maps to one HTTP status in the router."""


class InvalidReportId(ReportStoreError):
    """The id is not 16 lowercase hex characters (→ 400 `invalid_report_id`)."""


class ReportNotFound(ReportStoreError):
    """No such report, or no such version of it (→ 404 `report_not_found`)."""


class ReportExists(ReportStoreError):
    """`create_report` was asked to create an id that already has a directory."""


class InvalidFigureId(ReportStoreError):
    """The figure id is not a slug (→ 404 `figure_not_found`: it cannot exist)."""


class FigureNotFound(ReportStoreError):
    """The report exists but has no such figure file (→ 404 `figure_not_found`)."""


# ── ids and paths ───────────────────────────────────────────────────────────

def new_report_id() -> str:
    return secrets.token_hex(8)


# Every character an id may contain. `_rebuilt` indexes THIS string, so the
# value that goes on to name a directory is made of characters this module
# owns, chosen by positions derived from the caller's string, never copied
# out of it. Same shape as `routers/snapshots._slug`: a regex allowlist is
# correct but is not a barrier CodeQL's `py/path-injection` models, and the
# eight alerts on PR #64 all ran through `validate_report_id`/`validate_figure_id`
# returning the caller's own string. Rebuilding makes the provenance legible
# to the scanner and to a reader, and `test_report_store.py` pins that the
# rebuilt id equals the input for every accepted id.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def _rebuilt(value: str) -> str:
    """`value` re-spelled from `_ID_ALPHABET`; only called after a fullmatch."""
    return "".join(_ID_ALPHABET[_ID_ALPHABET.index(ch)] for ch in value)


def validate_report_id(report_id: object) -> str:
    if not isinstance(report_id, str) or not _REPORT_ID.fullmatch(report_id):
        raise InvalidReportId(f"not a report id: {report_id!r}")
    return _rebuilt(report_id)


def validate_figure_id(figure_id: object) -> str:
    if not isinstance(figure_id, str) or not _FIGURE_ID.fullmatch(figure_id):
        raise InvalidFigureId(f"not a figure id: {figure_id!r}")
    return _rebuilt(figure_id)


def reports_dir(project_dir: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(project_dir) / REPORTS_DIRNAME


def report_dir(project_dir: pathlib.Path, report_id: str) -> pathlib.Path:
    """The report's directory — joined only after the id passed the regex."""
    return reports_dir(project_dir) / validate_report_id(report_id)


def _existing_report_dir(project_dir: pathlib.Path, report_id: str) -> pathlib.Path:
    rdir = report_dir(project_dir, report_id)
    if not (rdir / "meta.json").is_file():
        raise ReportNotFound(f"report {report_id} not found")
    return rdir


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# ── meta ────────────────────────────────────────────────────────────────────

# The meta fields that are NOT derived from the document: the job's
# `generation` (WP3/WP6) and the template binding + mapping plan (WP11). A
# meta rewrite from a document carries them over; `update_meta` sets them.
_CARRIED_META_FIELDS = ("generation", "template_mode", "template_language", "mapping_plan",
                        "roundtrip_file_id", "settings_unrecorded")
# What a report job may write after its save (`pending`, 07f-b).
_PENDING_FIELDS = frozenset(_CARRIED_META_FIELDS) - {"settings_unrecorded"}


def _check_pending(pending: tuple[str, ...]) -> None:
    unknown = sorted(set(pending) - _PENDING_FIELDS)
    if unknown:
        raise TypeError(f"not a pending meta field: {', '.join(unknown)}")


def read_mark(mark: object, fallback_version: int | None = None) -> dict | None:
    """The `settings_unrecorded` mark as `{version: int, fields: [names]}`, or
    None when nothing is unrecorded. A mark of any other shape (a hand-edited
    or damaged meta.json) reads as every pending field unrecorded, at
    `fallback_version` when its own version is not an int: readers then
    refuse or say so, never a default in its place (07f-b, GK B F2)."""
    if mark is None:
        return None
    if isinstance(mark, dict):
        version, fields = mark.get("version"), mark.get("fields")
        if (isinstance(version, int) and not isinstance(version, bool)
                and isinstance(fields, list)
                and all(isinstance(f, str) and f in _PENDING_FIELDS for f in fields)):
            return {"version": version, "fields": sorted(set(fields))} if fields else None
        if isinstance(version, int) and not isinstance(version, bool):
            fallback_version = version
    return {"version": fallback_version, "fields": sorted(_PENDING_FIELDS)}


def _moved_mark(mark: object, pending: tuple[str, ...], version: int) -> dict | None:
    """The `settings_unrecorded` mark after a save of `version` (07f-b, GK B
    E1). Its fields are the carried fields left null by a write that never
    landed: the ones still unrecorded, which stay null in the new version
    too, plus this save's `pending`. None when there are none."""
    fields = sorted({*((read_mark(mark) or {}).get("fields") or ()), *pending})
    return {"version": version, "fields": fields} if fields else None


def without_unrecorded(mark: object, written, fallback_version: int | None = None) -> dict | None:
    """The mark once `written` fields have landed; None when none is left."""
    mark = read_mark(mark, fallback_version)
    if mark is None:
        return None
    fields = sorted(set(mark["fields"]) - set(written))
    return {**mark, "fields": fields} if fields else None


def _meta_from_doc(doc: ReportDocument, *, latest_version: int,
                   created_at: str | None = None,
                   carried: ReportMeta | None = None) -> ReportMeta:
    extra = ({f: getattr(carried, f) for f in _CARRIED_META_FIELDS}
             if carried is not None else {})
    return ReportMeta(
        report_id=doc.report_id,
        title=doc.title,
        created_at=created_at or doc.created_at,
        updated_at=_now_iso(),
        latest_version=latest_version,
        mode=doc.mode,
        evidence_hash=doc.evidence_hash,
        profile_id=doc.profile_id,
        model=doc.model,
        **extra,
    )


def _read_meta(path: pathlib.Path) -> ReportMeta | None:
    """None (with one warning) for a missing, unparsable or invalid meta.json."""
    try:
        meta = ReportMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        logger.warning("reports: unreadable meta.json at %s — skipping: %s", path, exc)
        return None
    mark = read_mark(meta.settings_unrecorded, meta.latest_version)
    return meta if mark == meta.settings_unrecorded else meta.model_copy(
        update={"settings_unrecorded": mark})


def _write_meta(rdir: pathlib.Path, meta: ReportMeta) -> None:
    atomic_write_text(rdir / "meta.json", meta.model_dump_json(indent=2))


def load_meta(project_dir: pathlib.Path, report_id: str) -> ReportMeta:
    rdir = _existing_report_dir(project_dir, report_id)
    meta = _read_meta(rdir / "meta.json")
    if meta is None:
        raise ReportNotFound(f"report {report_id} has an unreadable meta.json")
    return meta


def update_meta(project_dir: pathlib.Path, report_id: str, **fields: object) -> ReportMeta:
    """
    Set one or more of the carried meta fields (`generation`, `template_mode`,
    `template_language`, `mapping_plan`) on an existing report and return the
    new meta. `None` is a value (it clears the field). Any other field name
    is a `TypeError`: the document-derived fields are rewritten from the
    document, never patched here.
    """
    unknown = sorted(set(fields) - _PENDING_FIELDS)
    if unknown:
        raise TypeError(f"update_meta: not a carried meta field: {', '.join(unknown)}")
    rdir = _existing_report_dir(project_dir, report_id)
    with report_lock(project_dir, report_id):
        meta = load_meta(project_dir, report_id)
        # A field written here is recorded again (07f-b): it leaves the mark.
        updated = meta.model_copy(update={
            **fields, "updated_at": _now_iso(),
            "settings_unrecorded": without_unrecorded(meta.settings_unrecorded, fields),
        })
        _write_meta(rdir, updated)
    return updated


# ── versions ────────────────────────────────────────────────────────────────

def _version_numbers(rdir: pathlib.Path) -> list[int]:
    out: list[int] = []
    try:
        entries = list(rdir.iterdir())
    except OSError:
        return out
    for p in entries:
        m = _VERSION_FILE.fullmatch(p.name)
        if m and p.is_file():
            out.append(int(m.group(1)))
    return sorted(out)


def _version_file(rdir: pathlib.Path, version: int) -> pathlib.Path | None:
    """
    The existing ``v<version>.json`` of a report, found by matching the real
    directory entries rather than by spelling a file name from the caller's
    number. The caller's ``version`` (a route's query parameter) is used only
    in an equality comparison; the path handed back is one ``iterdir()``
    yielded. Same shape as ``routers/snapshots._existing_snapshot_dir``, for
    the same reason: CodeQL's ``py/path-injection`` (PR #64) does not model
    an ``isinstance``/range check on an int as a barrier, and a reader should
    not have to re-derive that an int cannot traverse.
    """
    try:
        entries = list(rdir.iterdir())
    except OSError:
        return None
    for p in entries:
        m = _VERSION_FILE.fullmatch(p.name)
        if m and int(m.group(1)) == version and p.is_file():
            return p
    return None


def _write_version(rdir: pathlib.Path, doc: ReportDocument) -> None:
    path = rdir / f"v{doc.version}.json"
    # Never overwrite: `os.replace` would, so the check is explicit.
    if path.exists():
        raise ReportExists(f"{path.name} already exists for report {doc.report_id}")
    atomic_write_text(path, doc.model_dump_json(indent=2))


_REPORT_LOCKS: dict[str, threading.RLock] = {}
_REPORT_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()
LOCK_TIMEOUT_S = 30
# A bundle copy's bound for all its report locks together (07e, GK B L2).
COPY_LOCK_TIMEOUT_S = 3
_COPY_LOCK_ROUNDS = 5


class ReportBusy(ReportStoreError):
    """Another holder, a process or a thread of this one, kept the report's
    lock past the caller's bound (`LOCK_TIMEOUT_S`, or a copy's deadline)."""


class ReportProjectGone(ReportStoreError):
    """The project directory a writer was given no longer exists: it was
    deleted or renamed while the writer ran (study harness 07f)."""


class ReportVersionConflict(ReportStoreError):
    """`save_version(expected_base=n)` found a later version than `n`: another
    writer saved in between, so this one would silently supersede it."""


@contextmanager
def report_lock(project_dir: pathlib.Path, report_id: str, *, timeout: float | None = None,
                _closing_ok: bool = False):
    """The one lock for every writer of `reports/<report_id>/` (study harness
    07c, GK B R5): `create_report`, `save_version`, `update_meta`,
    `write_figure`, `delete_report` and the job's generation meta take it,
    and a comment revision holds it across its digest check, its save and
    its comment update.

    Cross-process: the file lock `reports/.<report_id>.lock`
    (`harness.durable.file_lock`, GK B's #1070 ruling), taken once per
    thread; a thread lock in front of it makes it re-entrant, so a holder
    may call `save_version`. `ReportBusy` when another holder, a process or
    a thread of this one, keeps it past `LOCK_TIMEOUT_S`, or past `timeout`
    (a bundle copy sharing one deadline over several reports)."""
    from harness.durable import file_lock

    rid = validate_report_id(report_id)
    root = reports_dir(project_dir)
    key = str((root / rid).resolve())
    with _REPORT_LOCKS_GUARD:
        lock = _REPORT_LOCKS.setdefault(key, threading.RLock())
    # One budget for the thread lock and the file lock: a bundle copy of this
    # process can hold a report for a whole save or restore (07e), so a writer
    # is refused after `LOCK_TIMEOUT_S` here too, not left waiting.
    budget = LOCK_TIMEOUT_S if timeout is None else max(0.0, timeout)
    started = time.monotonic()
    if not lock.acquire(timeout=budget):
        raise ReportBusy(f"report {rid} is being written")
    try:
        held = getattr(_HELD, "keys", None)
        if held is None:
            held = _HELD.keys = set()
        if key in held:
            yield
            return
        # Never the project directory itself (study harness 07f): a writer
        # that outlived a delete or a rename would make it again.
        try:
            root.mkdir(exist_ok=True)
        except FileNotFoundError as exc:
            raise ReportProjectGone(f"report {rid}: its project directory is gone") from exc
        left = max(0.0, budget - (time.monotonic() - started))
        try:
            with file_lock(str(root / f".{rid}.lock"), timeout=left,
                           busy=f"report {rid} is being written"):
                if not _closing_ok and _closing_active(root):
                    raise ReportBusy(f"report {rid}: its project is being deleted or moved")
                held.add(key)
                try:
                    yield
                finally:
                    held.discard(key)
        except TimeoutError as exc:
            raise ReportBusy(f"report {rid} is being written by another process") from exc
    finally:
        lock.release()


# ── bundle copies (study harness 07e) ───────────────────────────────────────

def _lock_file_id(name: str) -> str | None:
    """The report id a `reports/.<id>.lock` file name belongs to, else None."""
    if name.startswith(".") and name.endswith(".lock") and _REPORT_ID.fullmatch(name[1:-5]):
        return name[1:-5]
    return None


def _lockable_ids(project_dir: pathlib.Path | None) -> set[str]:
    """Every report id with a directory or a lock file under `reports/`. A
    lock file alone counts: `create_report` holds it before its directory
    exists."""
    if project_dir is None:
        return set()
    root = reports_dir(project_dir)
    if not root.is_dir():
        return set()
    ids = set()
    for entry in root.iterdir():
        rid = _lock_file_id(entry.name) or entry.name
        if _REPORT_ID.fullmatch(rid):
            ids.add(rid)
    return ids


def _reports_key(project_dir: pathlib.Path | None) -> str | None:
    return None if project_dir is None else str(reports_dir(project_dir).resolve())


@contextmanager
def reports_copy_lock(src: pathlib.Path | None = None, dst: pathlib.Path | None = None,
                      *, incoming: tuple[str, ...] | frozenset[str] | set[str] = ()):
    """`report_lock` on every report a bundle copy reads or replaces (study
    harness 07e), so a snapshot, restore, import or save-as never reads or
    replaces a report while one write to it is in progress. (A writer that
    saves in several locked steps can still have a copy land between them.)

    `src` is read: its own reports are locked. `dst` is written: its reports,
    `src`'s and the `incoming` ids (an import's archive) are locked there.
    Yields `{"src": ids, "dst": ids}`, the ids held on each side. A copy
    touches only those: a report created after the set was taken is neither
    copied nor removed, so it serialises after the copy.

    Order: the locks are taken in sorted `(reports dir, id)` order, so two
    copies cannot deadlock, and after `PyPSAService.get_lock()` when the copy
    holds that too; a report writer holds one report lock and takes neither
    inside it. All of them share one `COPY_LOCK_TIMEOUT_S` deadline, short
    because the network lock may be held meanwhile. A report that appears
    while they are being taken releases the set and starts over, to keep the
    order, at most `_COPY_LOCK_ROUNDS` times.

    Nested in the same thread (`copy_reports` under a route's own hold), a
    side the outer call holds keeps the outer id set and is not listed again.

    `ReportBusy`, with every lock released, when the deadline passes or the
    reports keep changing."""
    extra = {validate_report_id(rid) for rid in incoming}
    holds = getattr(_HELD, "copies", None)
    if holds is None:
        holds = _HELD.copies = {}
    src_key, dst_key = _reports_key(src), _reports_key(dst)
    deadline = time.monotonic() + COPY_LOCK_TIMEOUT_S

    def wanted() -> tuple[set[str], set[str]]:
        src_ids = set(holds[src_key]) if src_key in holds else _lockable_ids(src)
        if dst is None:
            return src_ids, set()
        dst_ids = set(holds[dst_key]) if dst_key in holds else _lockable_ids(dst)
        return src_ids, dst_ids | src_ids | extra

    for _ in range(_COPY_LOCK_ROUNDS):
        src_ids, dst_ids = wanted()
        if dst is not None and dst_ids:
            # A copy makes its own destination; `report_lock` makes only
            # `reports/` (07f), so a writer cannot bring a project back.
            pathlib.Path(dst).mkdir(parents=True, exist_ok=True)
        keys = sorted(
            {(src_key, rid, "src") for rid in src_ids}
            | {(dst_key, rid, "dst") for rid in dst_ids}
        )
        with ExitStack() as stack:
            for _root, rid, side in keys:
                stack.enter_context(report_lock(
                    src if side == "src" else dst, rid,
                    timeout=max(0.0, deadline - time.monotonic()),
                ))
            if wanted() != (src_ids, dst_ids):
                continue
            mine = {
                key: frozenset(ids)
                for key, ids in ((src_key, src_ids), (dst_key, dst_ids))
                if key is not None and key not in holds
            }
            holds.update(mine)
            try:
                yield {"src": frozenset(src_ids), "dst": frozenset(dst_ids)}
            finally:
                for key in mine:
                    holds.pop(key, None)
            return
    raise ReportBusy("the project's reports kept changing while a copy waited for them")


def is_lock_file(name: str) -> bool:
    """Whether `reports/<name>` is a report's lock file."""
    return _lock_file_id(name) is not None


def _swap_in(build, target: pathlib.Path) -> None:
    """Build `target`'s replacement in a temp sibling with `build(tmp)`, then
    swap it in. An I/O error leaves `target` as it was."""
    stamp = f"{os.getpid()}-{time.time_ns()}"
    tmp = target.with_name(f".{target.name}.tmp-{stamp}")
    old = target.with_name(f".{target.name}.old-{stamp}")
    try:
        build(tmp)
        if target.exists():
            os.replace(target, old)
        try:
            os.replace(tmp, target)
        except OSError:
            if old.exists():
                os.replace(old, target)
            raise
    finally:
        for leftover in (tmp, old):
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)


def _remove_entry(entry: pathlib.Path) -> None:
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def copy_reports(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Make `dst`'s `reports/` a copy of `src`'s, one report at a time under
    `reports_copy_lock`. No `reports/` in `src` → nothing changes.

    Each report directory is built beside its destination and swapped in, and
    a destination report `src` lacks is removed. Only the reports whose locks
    are held are touched: lock files and anything else in `reports/` are
    neither copied nor removed (deleting a held `.<id>.lock` would let a
    second process lock a fresh file). `ReportBusy` before anything is
    written."""
    src_root, dst_root = reports_dir(src), reports_dir(dst)
    if not src_root.is_dir():
        return
    with reports_copy_lock(src, dst) as held:
        dst_root.mkdir(parents=True, exist_ok=True)
        names = {e.name for e in src_root.iterdir() if e.name in held["src"]}
        for entry in list(dst_root.iterdir()):
            if entry.name not in names and entry.name in held["dst"]:
                _remove_entry(entry)
        for name in sorted(names):
            entry = src_root / name
            copy = shutil.copytree if entry.is_dir() else shutil.copy2
            _swap_in(lambda tmp, entry=entry, copy=copy: copy(str(entry), str(tmp)),
                     dst_root / name)


def clear_reports(project_dir: pathlib.Path) -> None:
    """Remove every report of the project under `reports_copy_lock` (a restore
    to a state that had none). The lock files, and a report created after the
    locks were taken, stay. `ReportBusy` before anything is removed."""
    root = reports_dir(project_dir)
    if not root.is_dir():
        return
    with reports_copy_lock(None, project_dir) as held:
        for entry in list(root.iterdir()):
            if entry.name in held["dst"]:
                _remove_entry(entry)


# ── closing a directory (study harness 07f) ─────────────────────────────────

CLOSING_MARKER = ".closing"
# A marker older than this is left from a crash, so nothing refuses for ever.
# Every operation that writes one moves its directory away within seconds; a
# cascade delete closes each target in turn (up to `COPY_LOCK_TIMEOUT_S`
# each), so the bound leaves room for a large tree of busy scenarios.
CLOSING_STALE_S = 600
# A report job's on-disk record while it runs (LC48B-RESUME-1): written when
# the job starts, removed when it ends, so one left behind marks a job a
# restart interrupted. Local to this directory: never copied or exported.
JOB_MARKER = ".job.json"


def is_lock_artifact(name: str) -> bool:
    """Whether a name in `reports/` is a report lock file, the closing marker
    or the job marker: never data, never copied (study harness 07e, 07f)."""
    return name in (CLOSING_MARKER, JOB_MARKER) or _lock_file_id(name) is not None


def _closing_active(root: pathlib.Path) -> bool:
    try:
        written = (root / CLOSING_MARKER).stat().st_mtime
    except OSError:
        return False
    return time.time() - written < CLOSING_STALE_S


def reports_closing(project_dir: pathlib.Path) -> bool:
    """Whether a delete, rename or move of `project_dir` is under way."""
    return _closing_active(reports_dir(project_dir))


@contextmanager
def reports_closed(project_dir: pathlib.Path):
    """Close a directory's reports for an operation that moves or removes the
    whole directory (project delete and rename, snapshot delete and prune, a
    legacy claim; study harness 07f).

    Takes `reports_copy_lock` on every report, so it waits for a write in
    progress (one `COPY_LOCK_TIMEOUT_S` deadline, then `ReportBusy`), and
    writes `reports/.closing` while they are held. It then releases them, so
    no lock file is open when the caller moves the directory (Windows cannot
    move or remove one), and yields. Until the marker goes, `report_lock`
    refuses `ReportBusy` in every process: it checks the marker under its own
    file lock. The marker is removed on exit; one that moved with the
    directory is the caller's (`clear_closing`), and one a crash left goes
    stale after `CLOSING_STALE_S`.

    Order: after `PyPSAService.get_lock()` when the caller holds that, like
    every bundle copy (07e, L1)."""
    root = reports_dir(project_dir)
    with reports_copy_lock(None, project_dir) as held:
        if project_dir.is_dir():
            root.mkdir(exist_ok=True)
            atomic_write_text(root / CLOSING_MARKER, f"{os.getpid()}\n")
            try:
                _drain_new_reports(project_dir, held["dst"])
                _drain_charts(project_dir)
            except BaseException:
                clear_closing(project_dir)
                raise
    try:
        yield
    finally:
        clear_closing(project_dir)


def _drain_new_reports(project_dir: pathlib.Path, held: frozenset[str]) -> None:
    """Wait out a writer of a report the close did not lock (GK B D1).

    A `create_report` for a new id takes that id's lock, which the close does
    not hold, and may have checked the marker before it was written. Its lock
    file exists before it locks (`file_lock` opens, then locks), so once the
    marker is written every such writer is listed here: take each new id's
    lock once (past the marker, which this close wrote) and let it go. A
    writer that locks later meets the marker. Shares one
    `COPY_LOCK_TIMEOUT_S` deadline; `ReportBusy` past it."""
    deadline = time.monotonic() + COPY_LOCK_TIMEOUT_S
    for rid in sorted(_lockable_ids(project_dir) - set(held)):
        with report_lock(project_dir, rid, _closing_ok=True,
                         timeout=max(0.0, deadline - time.monotonic())):
            pass


def _drain_charts(project_dir: pathlib.Path) -> None:
    """Wait out a chart `add` (GK B K1): take the chart lock last, with the
    marker already written, and let it go. An `add` that locks later meets
    the marker. Same `COPY_LOCK_TIMEOUT_S` deadline; `ReportBusy` past it."""
    from services.reports import chart_snapshots as cs

    if not cs.charts_dir(project_dir).is_dir():
        return  # an add that makes it later locks past the marker
    try:
        with cs._lock(project_dir, timeout=COPY_LOCK_TIMEOUT_S, closing_ok=True):
            pass
    except TimeoutError as exc:
        raise ReportBusy("saved charts are being written") from exc


def clear_closing(project_dir: pathlib.Path) -> None:
    """Remove `reports/.closing` from `project_dir`, if it is there."""
    # Realpath plus a prefix check: the marker must resolve to a file directly
    # inside `reports/` (the form CodeQL reads as a py/path-injection barrier,
    # as `harness.durable.file_lock` spells it).
    root = os.path.realpath(reports_dir(project_dir))
    marker = os.path.realpath(os.path.join(root, CLOSING_MARKER))
    if not marker.startswith(root + os.sep) or os.path.dirname(marker) != root:
        return
    try:
        os.unlink(marker)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("could not remove the closing marker of %s", project_dir)


def create_report(project_dir: pathlib.Path, doc: ReportDocument, *,
                  pending: tuple[str, ...] = ()) -> ReportMeta:
    """Create `reports/<id>/` with `v1.json` and `meta.json`. The id must be new."""
    _check_pending(pending)  # a wrong field name refuses before any write
    rdir = report_dir(project_dir, doc.report_id)
    with report_lock(project_dir, doc.report_id):
        if rdir.exists():
            raise ReportExists(f"report {doc.report_id} already exists")
        # `report_lock` made `reports/`; never the project directory (07f).
        rdir.mkdir(exist_ok=False)
        first = doc.model_copy(update={"version": 1})
        _write_version(rdir, first)
        meta = _meta_from_doc(first, latest_version=1)
        if pending:  # see `save_version`
            meta = meta.model_copy(update={"settings_unrecorded": _moved_mark(None, pending, 1)})
        _write_meta(rdir, meta)
    return meta


def save_version(project_dir: pathlib.Path, doc: ReportDocument, *,
                 revised_from: RevisedFrom | None = None,
                 expected_base: int | None = None,
                 pending: tuple[str, ...] = ()) -> int:
    """
    Write the next version of an existing report and return its number.

    The caller does not choose the number: whatever `doc.version` carries, the
    file written is `v<latest+1>.json`, where `latest` is the highest of the
    meta's `latest_version` and the version files actually on disk.

    `expected_base` is the version the caller read and edited. When it is
    given and `latest` is not it, another writer saved in between: nothing is
    written and `ReportVersionConflict` is raised (study harness 07c, GK B F1).

    `pending` names the carried meta fields the caller writes for the new
    version after this save (a report job's `generation` and settings). This
    save writes them as null and marks them `settings_unrecorded`, so if
    that later write is refused the meta shows version n without them,
    never with version n-1's (07f-b, GK B Q2, E1). Every other carried field
    still carries (E4). Fields a mark already lists stay null in the new
    version, so the mark moves to it with them (any caller's save).
    """
    _check_pending(pending)  # a wrong field name refuses before any write
    rdir = _existing_report_dir(project_dir, doc.report_id)
    with report_lock(project_dir, doc.report_id):
        meta = load_meta(project_dir, doc.report_id)
        on_disk = _version_numbers(rdir)
        latest = max([meta.latest_version, *on_disk])
        if expected_base is not None and latest != expected_base:
            raise ReportVersionConflict(
                f"report {doc.report_id} is at version {latest}, "
                f"not {expected_base}: another writer saved first")
        next_version = latest + 1
        # `revised_from` is the caller's statement, never carried over from the
        # base (07c R7): a regenerate of a revision is not itself a revision.
        bumped = doc.model_copy(update={"version": next_version, "revised_from": revised_from})
        _write_version(rdir, bumped)
        # `generation` is the job's, written after ITS save (WP3); the template
        # binding and the mapping plan are the template routes' (WP11). A later
        # version from any caller keeps them rather than silently dropping them.
        _write_meta(rdir, _meta_from_doc(
            bumped, latest_version=next_version, created_at=meta.created_at,
            carried=meta.model_copy(update={
                **dict.fromkeys(pending),
                "settings_unrecorded": _moved_mark(meta.settings_unrecorded, pending,
                                                   next_version),
            }),
        ))
    return next_version


def load_report(project_dir: pathlib.Path, report_id: str,
                version: int | None = None) -> ReportDocument:
    """The latest version when `version` is None, else that exact version."""
    rdir = _existing_report_dir(project_dir, report_id)
    if version is None:
        meta = load_meta(project_dir, report_id)
        version = meta.latest_version
    if not isinstance(version, int) or version < 1:
        raise ReportNotFound(f"report {report_id} has no version {version!r}")
    path = _version_file(rdir, version)
    if path is None:
        raise ReportNotFound(f"report {report_id} has no version {version}")
    try:
        return ReportDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise ReportNotFound(
            f"report {report_id} version {version} is unreadable: {exc}"
        ) from exc


def version_sha256(project_dir: pathlib.Path, report_id: str, version: int) -> str:
    """SHA-256 of one version's stored file, as written (AA-05 S4b, GK B X2).

    A version file is written once and never rewritten, so its raw bytes are
    the stable basis: a release that changes how a version loads or
    normalises does not change this hash. `ReportNotFound` when the report or
    the version is gone; an `OSError` reading it propagates."""
    import hashlib

    rdir = _existing_report_dir(project_dir, report_id)
    path = _version_file(rdir, version) if isinstance(version, int) else None
    if path is None:
        raise ReportNotFound(f"report {report_id} has no version {version}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def list_reports(project_dir: pathlib.Path) -> list[ReportMeta]:
    """Every readable report, newest first by `created_at`. No `reports/` → []."""
    root = reports_dir(project_dir)
    if not root.is_dir():
        return []
    out: list[ReportMeta] = []
    for sub in root.iterdir():
        if not sub.is_dir() or not _REPORT_ID.fullmatch(sub.name):
            continue
        meta = _read_meta(sub / "meta.json")
        if meta is None:
            continue
        out.append(meta)
    out.sort(key=lambda m: (m.created_at, m.report_id), reverse=True)
    return out


def delete_report(project_dir: pathlib.Path, report_id: str) -> None:
    rdir = _existing_report_dir(project_dir, report_id)
    with report_lock(project_dir, report_id):
        shutil.rmtree(rdir)


# ── figures ─────────────────────────────────────────────────────────────────

def figures_dir(project_dir: pathlib.Path, report_id: str) -> pathlib.Path:
    return report_dir(project_dir, report_id) / "figures"


def write_figure(project_dir: pathlib.Path, report_id: str, figure_id: str,
                 png_bytes: bytes) -> pathlib.Path:
    fid = validate_figure_id(figure_id)
    fdir = figures_dir(project_dir, report_id)
    _existing_report_dir(project_dir, report_id)
    path = fdir / f"{fid}.png"
    with report_lock(project_dir, report_id):
        try:
            # Never the report's directory (07f, GK B D1): one removed since
            # the check above stays removed.
            fdir.mkdir(exist_ok=True)
        except FileNotFoundError as exc:
            raise ReportNotFound(f"report {report_id} not found") from exc
        atomic_write_bytes(path, png_bytes)
    return path


def figure_path(project_dir: pathlib.Path, report_id: str, figure_id: str) -> pathlib.Path:
    """The PNG's path, or `FigureNotFound`. Validates both ids first."""
    fid = validate_figure_id(figure_id)
    _existing_report_dir(project_dir, report_id)
    path = figures_dir(project_dir, report_id) / f"{fid}.png"
    if not path.is_file():
        raise FigureNotFound(f"report {report_id} has no figure {fid}")
    return path
