"""
Decision-study persistence: one sidecar per study.

A study lives at ``<project storage dir>/studies/<study_id>.json`` — inside
the base project's own directory, so the project's ACL is the study's ACL and
``{study_id}`` can only ever be looked up inside a project the caller was
already authorised for (review v1 B7: no global id index, no IDOR).

``studies`` is in ``routers/projects._BUNDLE_DIRS`` (review v2 BC-4), so
bundle export/import, Save-As and snapshot create/restore carry it through
``_copy_bundle_dirs``. S4's fork copier (``services/study/forks.py``) must NOT
reuse that walk unchanged: option forks skip ``studies/`` (and
``results_state.pkl``), or every fork would claim to own the study.

Writes go through ``services/atomic_io.atomic_write_text``: a killed process
leaves the old record or the new one, never half of one. The id is the only
client input that reaches a path, and it must be a lowercase uuid4 hex; any
other shape is "not found" before the disk is touched.
"""
from __future__ import annotations

import json
import pathlib
import uuid

from pydantic import ValidationError

from models.study import STUDY_ID_RE, DecisionStudy
from services.atomic_io import atomic_write_text

SIDECAR_DIR = "studies"
# A study record is intake answers and references, never time series (those
# live in the network and in uploads/). 1 MB bounds a malformed or abusive
# payload without clipping a real one.
MAX_STUDY_BYTES = 1024 * 1024


class StudyNotFound(LookupError):
    """No study with that id in this project (or the id is malformed)."""


class StudyUnreadable(ValueError):
    """The sidecar exists but does not parse as the study it is named for."""


class StudyTooLarge(ValueError):
    """The serialised study exceeds `MAX_STUDY_BYTES`."""


def new_study_id() -> str:
    return uuid.uuid4().hex


def studies_dir(project_dir: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(project_dir) / SIDECAR_DIR


def _path(project_dir: pathlib.Path, study_id: str) -> pathlib.Path:
    if not isinstance(study_id, str) or not STUDY_ID_RE.fullmatch(study_id):
        raise StudyNotFound(study_id)
    return studies_dir(project_dir) / f"{study_id}.json"


def _read(path: pathlib.Path, study_id: str) -> DecisionStudy:
    try:
        study = DecisionStudy.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise StudyUnreadable(f"{path.name}: {exc}") from exc
    if study.study_id != study_id:
        raise StudyUnreadable(
            f"{path.name} holds study {study.study_id!r}, not {study_id!r}")
    return study


def load_study(project_dir: pathlib.Path, study_id: str) -> DecisionStudy:
    path = _path(project_dir, study_id)
    if not path.is_file():
        raise StudyNotFound(study_id)
    return _read(path, study_id)


def list_studies(project_dir: pathlib.Path) -> list[DecisionStudy]:
    """
    Every readable study in the project, oldest first. An unreadable
    sidecar is skipped here (a listing must not fail on one bad file); a
    direct `load_study` of it still raises.
    """
    d = studies_dir(project_dir)
    if not d.is_dir():
        return []
    out = []
    for path in d.glob("*.json"):
        sid = path.stem
        if not STUDY_ID_RE.fullmatch(sid):
            continue
        try:
            out.append(_read(path, sid))
        except StudyUnreadable:
            continue
    return sorted(out, key=lambda s: (s.created_at, s.study_id))


def save_study(project_dir: pathlib.Path, study: DecisionStudy) -> DecisionStudy:
    path = _path(project_dir, study.study_id)
    text = json.dumps(study.model_dump(mode="json", by_alias=True),
                      indent=2, sort_keys=True)
    if len(text.encode("utf-8")) > MAX_STUDY_BYTES:
        raise StudyTooLarge(
            f"study {study.study_id} is larger than {MAX_STUDY_BYTES} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return study


def delete_study(project_dir: pathlib.Path, study_id: str) -> None:
    path = _path(project_dir, study_id)
    try:
        path.unlink()
    except FileNotFoundError:
        raise StudyNotFound(study_id) from None
