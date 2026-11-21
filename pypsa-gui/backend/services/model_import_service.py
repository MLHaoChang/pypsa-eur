"""
Model files into a new project: one path for every registry importer (project-io 04, Slice A).

`routers/model_import.py` serves it. A **preview** reads the uploaded files and writes
nothing a caller can see; an **import** reads them again and creates a new project, all
or nothing. It never overwrites one (IMPORT-FLOW-1).

- **The registry is the list.** The formats are `gridspine.ingest.import_result.importers()`,
  so a new importer appears without an edit here. `EXCLUDED` names the one registry format
  with its own route: the Excel input workbook carries the project's solver settings and
  time series, which `services.excel_workbook` checks (#1798).
- **Uploads.** Plain, distinct file names only (no folders, no `..`); at most `MAX_FILES`
  files and the upload service's byte cap for all of them together. Each request stages
  into its own folder under `<projects root>/.model-import-staging`, removed on every path.
- **The reader runs in a child.** `services.model_import_worker` reads in a `spawn` child
  with a wall-clock kill (`READ_SECONDS`) and an address-space limit (`READ_MEMORY`), and
  writes `network.nc` and the sidecars into the staging folder. Over the budget, a crash
  or the memory limit is a 422 in plain words, never a 500.
- **The digest covers the options.** `sha256` hashes the options and every file's name and
  bytes, so an import whose files or options differ from the preview's is a 409
  (`import_file_changed`).
- **Sidecars.** A reader may store only the importer files the project contents table
  lists (`project_contents.IMPORTER_SIDECARS`), plus a `metadata.json` whose keys are
  merged into the project's metadata. A key the project writes itself, or any other name,
  refuses the import (`import_sidecar_refused`) before a project row exists.
- **All or nothing.** `project_registry.create_root` refuses a taken name (409) before any
  folder exists; a failure after the row exists removes the row and the folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pathlib
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

#: Registry formats served by their own route, with where the user goes instead.
EXCLUDED = {
    "excel": "An Excel input workbook is imported from New project › Import file or the "
    "Project Data window, which also read its solver settings and time series.",
}
MAX_FILES = 8
#: Spawn (~1 s), the gridspine and PyPSA imports (~5 s) and the read. PLEXOS's
#: world fixture reads in well under 30 s; the budget leaves room for larger models.
READ_SECONDS = 180.0
READ_MEMORY = 4 * 1024 * 1024 * 1024
MAX_OPTIONS = 16
#: Reads running at once; each holds a child of up to READ_MEMORY for up to READ_SECONDS.
MAX_CONCURRENT_READS = 2
STALE_STAGING_SECONDS = 24 * 3600
_OPTION_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")
REPORT_FILE = "import_report.json"
#: The metadata.json keys an importer may add (the DGS reader's names live under "import").
IMPORTER_META_KEYS = frozenset({"import"})
META_FILE = "metadata.json"
NETWORK_FILE = "network.nc"


_READS = threading.BoundedSemaphore(MAX_CONCURRENT_READS)


def _refuse(status: int, detail: dict) -> None:
    """Raise `detail` ({"error_kind": <literal>, "message": ...}); a literal kind, so the
    error-kind manifest test sees it."""
    raise HTTPException(status, detail)


_UNAVAILABLE = (
    "Model import is not available in this build: its importers (gridspine) are not installed."
)


def _contract():
    """gridspine's import contract, or a plain 503 when this build does not carry it
    (gridspine is optional at runtime, as in `gridspine_service`)."""
    try:
        from gridspine.ingest import import_result as IR
    except ImportError:
        _refuse(503, {"error_kind": "import_unavailable", "message": _UNAVAILABLE})
    return IR


def declared():
    """The importer registry this service reads (a test passes its own)."""
    IR = _contract()

    return IR.DECLARED


def formats() -> list[dict]:
    """Every importer this build can run, in plain names."""
    IR = _contract()

    return [
        {
            "id": s.format_id,
            "label": s.display_name,
            "extensions": list(s.extensions),
            "help": s.help,
        }
        for s in IR.importers(declared())
        if s.format_id not in EXCLUDED
    ]


def staging_root() -> pathlib.Path:
    """Beside the projects, so a staged file moves into its project by rename.
    A dot folder: `storage_reconcile` never reads it as a project."""
    from settings import get_settings

    root = pathlib.Path(get_settings().projects_root) / ".model-import-staging"
    root.mkdir(parents=True, exist_ok=True)
    return root


def clear_stale_staging(max_age_seconds: float = STALE_STAGING_SECONDS) -> None:
    """Remove staging folders a killed process left behind (each request removes its own)."""
    import time

    cutoff = time.time() - max_age_seconds
    for p in staging_root().glob("import-*"):
        try:
            if p.is_dir() and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


@dataclass
class Staged:
    """One request's uploads, staged on disk."""

    dir: pathlib.Path
    names: list[str]
    options: dict
    sha256: str
    format_id: str = ""
    result: dict = field(default_factory=dict)

    @property
    def src(self) -> pathlib.Path:
        return self.dir / "src"

    @property
    def out(self) -> pathlib.Path:
        return self.dir / "out"


def _no_constant(name: str):
    raise ValueError(f"{name} is not a number")


def parse_options(raw: str | None) -> dict:
    """The importer options: a JSON object of short lowercase keys and scalar values."""
    if raw is None or not raw.strip():
        return {}
    try:
        opts = json.loads(raw, parse_constant=_no_constant)
    except ValueError:
        _refuse(
            422,
            {
                "error_kind": "import_options_invalid",
                "message": "The import options are not valid JSON.",
            },
        )
    if not isinstance(opts, dict) or len(opts) > MAX_OPTIONS:
        _refuse(
            422,
            {
                "error_kind": "import_options_invalid",
                "message": f"The import options must be an object with at most {MAX_OPTIONS} entries.",
            },
        )
    for key, value in opts.items():
        if not key or len(key) > 32 or not set(key) <= _OPTION_KEY_CHARS:
            _refuse(
                422,
                {
                    "error_kind": "import_options_invalid",
                    "message": f'The import option "{key}" is not a known kind of option.',
                },
            )
        if value is not None and not isinstance(value, (str, int, float, bool)):
            _refuse(
                422,
                {
                    "error_kind": "import_options_invalid",
                    "message": f'The import option "{key}" must be a single value.',
                },
            )
        if isinstance(value, float) and not math.isfinite(value):
            _refuse(
                422,
                {
                    "error_kind": "import_options_invalid",
                    "message": f'The import option "{key}" must be a finite number.',
                },
            )
        if isinstance(value, str) and len(value) > 256:
            _refuse(
                422,
                {
                    "error_kind": "import_options_invalid",
                    "message": f'The import option "{key}" is too long.',
                },
            )
    return opts


#: Names Windows reserves for devices, whatever the extension (the desktop build).
_WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
MAX_NAME_BYTES = 255


def check_names(names: list[str]) -> None:
    """Plain, distinct file names on every platform the app runs on: no folders, no
    `..`, no `:` (an NTFS stream), no device name, at most 255 bytes. Two names that
    Windows and macOS read as one file (case, trailing dots and spaces) are refused."""
    if not names:
        _refuse(
            422,
            {
                "error_kind": "import_files_invalid",
                "message": "Choose at least one file to import.",
            },
        )
    if len(names) > MAX_FILES:
        _refuse(
            422,
            {
                "error_kind": "import_files_invalid",
                "message": f"At most {MAX_FILES} files can be imported together.",
            },
        )
    seen: set[str] = set()
    for name in names:
        if (
            not name
            or name in (".", "..")
            or "/" in name
            or "\\" in name
            or name.strip() != name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or ":" in name
            or len(name.encode("utf-8", "surrogatepass")) > MAX_NAME_BYTES
            or name.split(".")[0].rstrip(" ").casefold() in _WINDOWS_DEVICES
            or os.path.basename(name) != name
        ):
            _refuse(
                422,
                {
                    "error_kind": "import_files_invalid",
                    "message": f'"{name}" is not a plain file name.',
                },
            )
        folded = name.rstrip(". ").casefold()
        if folded in seen:
            _refuse(
                422,
                {
                    "error_kind": "import_files_invalid",
                    "message": f'Two files are named "{name}".',
                },
            )
        seen.add(folded)


def digest(options: dict, files: list[tuple[str, bytes]]) -> str:
    """One hash over the options and every file's name and bytes, in name order."""
    h = hashlib.sha256()
    h.update(json.dumps(options, sort_keys=True, separators=(",", ":")).encode())
    for name, data in sorted(files):
        h.update(b"\0" + name.encode() + b"\0" + str(len(data)).encode() + b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


async def stage(files: list[UploadFile], options_raw: str | None) -> Staged:
    """Read the uploads under one byte cap and stage them; the caller removes `Staged.dir`."""
    from services.upload_guard import UploadBudget

    options = parse_options(options_raw)
    names = [f.filename or "" for f in files]
    check_names(names)
    budget = UploadBudget()
    blobs = [(name, await budget.read(f)) for name, f in zip(names, files)]
    # Off the event loop: hashing and writing up to the byte cap takes seconds.
    return await run_in_threadpool(_stage_files, names, options, blobs)


def _stage_files(
    names: list[str], options: dict, blobs: list[tuple[str, bytes]]
) -> Staged:
    sha = digest(options, blobs)
    clear_stale_staging()
    st = Staged(
        pathlib.Path(tempfile.mkdtemp(dir=staging_root(), prefix="import-")),
        names,
        options,
        sha,
    )
    try:
        st.src.mkdir()
        st.out.mkdir()
        for name, data in blobs:
            (st.src / name).write_bytes(data)
    except BaseException:
        discard(st)
        raise
    return st


def discard(st: Staged | None) -> None:
    if st is not None:
        shutil.rmtree(st.dir, ignore_errors=True)


def format_of(st: Staged) -> str:
    """The one importer for every staged file; 422 when they disagree or none reads them."""
    IR = _contract()

    ids = set()
    for name in st.names:
        try:
            spec = IR.for_file(st.src / name, declared())
        except IR.ImportFormatError as exc:
            _refuse(422, {"error_kind": "import_unreadable", "message": str(exc)})
        except Exception:  # a format sniff that breaks on a malformed file
            logger.warning(
                "Model import: telling the format of %s failed", name, exc_info=True
            )
            _refuse(
                422,
                {
                    "error_kind": "import_unreadable",
                    "message": f"{name}: its format could not be told from its content.",
                },
            )
        ids.add(spec.format_id)
    if len(ids) > 1:
        _refuse(
            422,
            {
                "error_kind": "import_files_invalid",
                "message": "These files are of different formats; import one model at a time.",
            },
        )
    (fid,) = ids
    if fid in EXCLUDED:
        _refuse(
            422, {"error_kind": "import_format_elsewhere", "message": EXCLUDED[fid]}
        )
    return fid


def _read_in_child(st: Staged) -> dict:
    import multiprocessing

    from services import model_import_worker as W

    ctx = multiprocessing.get_context("spawn")
    receive, send = ctx.Pipe(duplex=False)
    paths = [str(st.src / n) for n in st.names]
    child = ctx.Process(
        target=W.read_to,
        args=(
            send,
            st.format_id,
            paths,
            st.options,
            str(st.out),
            tuple(declared()),
            READ_MEMORY,
        ),
        daemon=True,
        name="model-import",
    )
    child.start()
    send.close()
    try:
        if not receive.poll(READ_SECONDS):
            _refuse(
                422,
                {
                    "error_kind": "import_timeout",
                    "message": f"The files could not be read within {READ_SECONDS:g} s, so the import stopped. "
                    "Split the model, or export a smaller case from its source.",
                    "budget_seconds": READ_SECONDS,
                },
            )
        try:
            out = receive.recv()
        except (
            EOFError,
            OSError,
        ):  # the child died (the memory limit, a crash) before it answered
            out = {}
    finally:
        receive.close()
        if child.is_alive():
            child.kill()
        child.join(5)
    return out


def read(st: Staged) -> Staged:
    """Run the importer on the staged files; the result's sidecars are checked."""
    st.format_id = format_of(st)
    if not _READS.acquire(blocking=False):
        _refuse(
            503,
            {
                "error_kind": "import_busy",
                "message": "Other model imports are being read right now. Try again in a minute.",
            },
        )
    try:
        out = _read_in_child(st)
    finally:
        _READS.release()
    if "refused" in out:
        _refuse(422, {"error_kind": "import_unreadable", "message": out["refused"]})
    if "unavailable" in out:
        _refuse(503, {"error_kind": "import_unavailable", "message": _UNAVAILABLE})
    if "report" not in out:
        why = (
            f" ({out['error']})"
            if out.get("error")
            else " (it ran out of memory or stopped)"
        )
        _refuse(
            422,
            {
                "error_kind": "import_unreadable",
                "message": "The importer could not read these files" + why + ".",
            },
        )
    check_sidecars(st, out["sidecars"])
    st.result = out
    return st


def check_sidecars(st: Staged, names: list[str]) -> None:
    """Only the importer files the contents table lists, and a metadata.json whose only
    keys are `IMPORTER_META_KEYS`: an importer never sets a field the project owns."""
    from services import project_contents as PC

    for name in names:
        if name == META_FILE:
            try:
                meta = json.loads((st.out / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = None
            clash = (
                sorted(set(meta) - IMPORTER_META_KEYS)
                if isinstance(meta, dict)
                else None
            )
            if clash is None or clash:
                _refuse(
                    422,
                    {
                        "error_kind": "import_sidecar_refused",
                        "message": "The importer tried to set project fields it may not set"
                        + (f" ({', '.join(clash)})" if clash else "")
                        + ".",
                    },
                )
        elif name not in PC.IMPORTER_SIDECARS:
            _refuse(
                422,
                {
                    "error_kind": "import_sidecar_refused",
                    "message": f'The importer tried to store "{name}", which a project does not keep.',
                },
            )


def payload(st: Staged) -> dict:
    """What the preview shows: counts, then every unmapped and assumed row, in plain names."""
    IR = _contract()

    r = st.result
    report = r["report"]
    stem = pathlib.Path(st.names[0]).stem
    return {
        "sha256": st.sha256,
        "format": st.format_id,
        "format_label": IR.get(st.format_id, declared()).display_name,
        "suggested_name": (r.get("network_name") or "").strip() or stem,
        "files": list(st.names),
        "bus_count": r["bus_count"],
        "snapshot_count": r["snapshot_count"],
        "counts": report.get("counts", {}),
        "unmapped": report.get("unmapped", []),
        "assumed": report.get("assumed", []),
        "warnings": report.get("warnings", []),
        "suggestions": report.get("suggestions", []),
        "stored_files": [n for n in r["sidecars"] if n != META_FILE],
    }


def _create_meta(bus_count: int, snapshot_count: int) -> dict:
    now = datetime.now(tz=UTC).isoformat()
    return {
        "created_at": now,
        "last_saved": now,
        "bus_count": bus_count,
        "snapshot_count": snapshot_count,
        "objective": None,
        "has_results": False,
        "condition": None,
        "solve_time": None,
        "user_ts_count": 0,
        "network_revision": 0,
        "parent_project": None,
        "scenario_description": None,
    }


def _write_files(dest: pathlib.Path, st: Staged) -> None:
    from routers.projects import _write_meta
    from services.atomic_io import atomic_write_text

    r = st.result
    for name in [NETWORK_FILE, *r["sidecars"]]:
        if name != META_FILE:
            os.replace(st.out / name, dest / name)
    report = {
        "format": st.format_id,
        "files": list(st.names),
        "options": st.options,
        "sha256": st.sha256,
        "imported_at": datetime.now(tz=UTC).isoformat(),
        "report": r["report"],
    }
    atomic_write_text(dest / REPORT_FILE, json.dumps(report, indent=2, allow_nan=False))
    meta = _create_meta(r["bus_count"], r["snapshot_count"])
    if META_FILE in r["sidecars"]:
        meta.update(json.loads((st.out / META_FILE).read_text(encoding="utf-8")))
    _write_meta(dest, meta)


def create_project(db, user, name: str, st: Staged):
    """
    A new project row and folder holding the read model, all or nothing.

    Every check has run (`read`, the sidecars included) before the row exists.
    `create_root` refuses a taken name (409) before any folder is made, and a fresh
    folder is never an existing one, so a collision overwrites nothing. A failure
    after the row exists removes the row and the folder this call made.
    """
    from services import project_registry

    project = project_registry.create_root(db, user, name)
    made = False
    try:
        dest = project_registry.project_dir(project)
        dest.mkdir(parents=True, exist_ok=False)
        made = True
        _write_files(dest, st)
    except BaseException as exc:
        if made:
            shutil.rmtree(dest, ignore_errors=True)
        # A failed flush or statement leaves the session unusable until rolled back.
        db.rollback()
        project_registry.delete_project_row(db, project)
        if isinstance(exc, Exception) and not isinstance(exc, HTTPException):
            logger.warning("Model import could not write the project", exc_info=True)
            _refuse(
                500,
                {
                    "error_kind": "import_write_failed",
                    "message": "The project could not be written; nothing was kept. Try the import again.",
                },
            )
        raise
    return project
