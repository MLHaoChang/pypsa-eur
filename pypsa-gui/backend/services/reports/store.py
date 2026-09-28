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
import pathlib
import re
import secrets
import shutil
from datetime import datetime, UTC

from pydantic import ValidationError

from models.report import ReportDocument, ReportMeta
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


def validate_report_id(report_id: object) -> str:
    if not isinstance(report_id, str) or not _REPORT_ID.fullmatch(report_id):
        raise InvalidReportId(f"not a report id: {report_id!r}")
    return report_id


def validate_figure_id(figure_id: object) -> str:
    if not isinstance(figure_id, str) or not _FIGURE_ID.fullmatch(figure_id):
        raise InvalidFigureId(f"not a figure id: {figure_id!r}")
    return figure_id


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

def _meta_from_doc(doc: ReportDocument, *, latest_version: int,
                   created_at: str | None = None) -> ReportMeta:
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
    )


def _read_meta(path: pathlib.Path) -> ReportMeta | None:
    """None (with one warning) for a missing, unparsable or invalid meta.json."""
    try:
        return ReportMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        logger.warning("reports: unreadable meta.json at %s — skipping: %s", path, exc)
        return None


def _write_meta(rdir: pathlib.Path, meta: ReportMeta) -> None:
    atomic_write_text(rdir / "meta.json", meta.model_dump_json(indent=2))


def load_meta(project_dir: pathlib.Path, report_id: str) -> ReportMeta:
    rdir = _existing_report_dir(project_dir, report_id)
    meta = _read_meta(rdir / "meta.json")
    if meta is None:
        raise ReportNotFound(f"report {report_id} has an unreadable meta.json")
    return meta


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


def _write_version(rdir: pathlib.Path, doc: ReportDocument) -> None:
    path = rdir / f"v{doc.version}.json"
    # Never overwrite: `os.replace` would, so the check is explicit.
    if path.exists():
        raise ReportExists(f"{path.name} already exists for report {doc.report_id}")
    atomic_write_text(path, doc.model_dump_json(indent=2))


def create_report(project_dir: pathlib.Path, doc: ReportDocument) -> ReportMeta:
    """Create `reports/<id>/` with `v1.json` and `meta.json`. The id must be new."""
    rdir = report_dir(project_dir, doc.report_id)
    if rdir.exists():
        raise ReportExists(f"report {doc.report_id} already exists")
    rdir.mkdir(parents=True, exist_ok=False)
    first = doc.model_copy(update={"version": 1})
    _write_version(rdir, first)
    meta = _meta_from_doc(first, latest_version=1)
    _write_meta(rdir, meta)
    return meta


def save_version(project_dir: pathlib.Path, doc: ReportDocument) -> int:
    """
    Write the next version of an existing report and return its number.

    The caller does not choose the number: whatever `doc.version` carries, the
    file written is `v<latest+1>.json`, where `latest` is the highest of the
    meta's `latest_version` and the version files actually on disk.
    """
    rdir = _existing_report_dir(project_dir, doc.report_id)
    meta = load_meta(project_dir, doc.report_id)
    on_disk = _version_numbers(rdir)
    next_version = max([meta.latest_version, *on_disk]) + 1
    bumped = doc.model_copy(update={"version": next_version})
    _write_version(rdir, bumped)
    _write_meta(rdir, _meta_from_doc(
        bumped, latest_version=next_version, created_at=meta.created_at,
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
    path = rdir / f"v{version}.json"
    if not path.is_file():
        raise ReportNotFound(f"report {report_id} has no version {version}")
    try:
        return ReportDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise ReportNotFound(
            f"report {report_id} version {version} is unreadable: {exc}"
        ) from exc


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
    shutil.rmtree(rdir)


# ── figures ─────────────────────────────────────────────────────────────────

def figures_dir(project_dir: pathlib.Path, report_id: str) -> pathlib.Path:
    return report_dir(project_dir, report_id) / "figures"


def write_figure(project_dir: pathlib.Path, report_id: str, figure_id: str,
                 png_bytes: bytes) -> pathlib.Path:
    fid = validate_figure_id(figure_id)
    fdir = figures_dir(project_dir, report_id)
    _existing_report_dir(project_dir, report_id)
    fdir.mkdir(parents=True, exist_ok=True)
    path = fdir / f"{fid}.png"
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
