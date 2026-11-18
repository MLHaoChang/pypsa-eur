"""The weather bar's saved choice (RP-04b, spec R3).

One source and one year per project, kept in the project's metadata under
`profile_weather`. It is the default of every generate that names neither: the
Time Series Manager's per-generator Generate, Profile Studio and the chat
assistant read the same value. Nothing saved means RP-03's default (the
snapshots' year), so the bar shows "from your snapshots" or "default".
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from services.atomic_io import atomic_write_text
from services.profile_gen.horizon import default_weather_year, last_completed_year

META_KEY = "profile_weather"
SOURCES = ("synthetic", "atlite")
# The earliest year each source can serve: ERA5 reanalysis starts in 1940; the
# synthetic source is a deterministic function of the year, so any calendar year
# from 1900 is meaningful. Neither serves a year not yet complete.
FIRST_YEAR = {"synthetic": 1900, "atlite": 1940}


class WeatherChoiceError(ValueError):
    """A source or year the bar cannot save."""


def _meta_file(project_dir: Path | str) -> Path:
    return Path(project_dir) / "metadata.json"


def _read(project_dir: Path | str) -> dict[str, Any]:
    try:
        return json.loads(_meta_file(project_dir).read_text())
    except (OSError, ValueError):
        return {}


def saved_choice(project_dir: Path | str) -> dict[str, Any] | None:
    got = _read(project_dir).get(META_KEY)
    if not isinstance(got, dict) or got.get("source") not in SOURCES:
        return None
    year = got.get("year")
    return {"source": got["source"], "year": int(year) if isinstance(year, int) else None}


def validate(source: str, year: int) -> None:
    if source not in SOURCES:
        raise WeatherChoiceError(f"the weather source is one of {', '.join(SOURCES)}, not {source!r}")
    last = last_completed_year()
    first = FIRST_YEAR[source]
    if not first <= year <= last:
        raise WeatherChoiceError(
            f"the {source} weather year is a completed year from {first} to {last}, not {year}")


def save_choice(project_dir: Path | str, source: str, year: int) -> None:
    validate(source, year)
    meta = _read(project_dir)
    meta[META_KEY] = {"source": source, "year": int(year)}
    atomic_write_text(_meta_file(project_dir), json.dumps(meta, indent=2))


def clear_choice(project_dir: Path | str) -> None:
    meta = _read(project_dir)
    if meta.pop(META_KEY, None) is not None:
        atomic_write_text(_meta_file(project_dir), json.dumps(meta, indent=2))


def resolved(project_dir: Path | str, snapshots) -> dict[str, Any]:
    """{source, year, year_source}: the saved choice, else the snapshot default."""
    saved = saved_choice(project_dir)
    default_year, default_kind = default_weather_year(snapshots)
    if saved and saved["year"] is not None:
        return {"source": saved["source"], "year": saved["year"], "year_source": "saved"}
    return {"source": saved["source"] if saved else "synthetic",
            "year": default_year, "year_source": default_kind}


# ── Where the project's generated profiles came from (project-io 03) ─────────
# One entry per applied target (component type, component, attribute): the
# cutout its current series was generated from. Re-applying a target replaces
# its entry; applying a series with no cutout (synthetic, or a cutout that could
# not be recorded) removes it, so an entry never names a replaced source.
CUTOUTS_KEY = "profile_cutouts"
_TARGET = ("component_type", "component", "attribute")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _valid_cutout(entry: Any) -> bool:
    """Imported metadata is untrusted: keep only well-formed entries (W4)."""
    if not isinstance(entry, dict):
        return False
    if not all(isinstance(entry.get(k), str) for k in (*_TARGET, "module")):
        return False
    sha, year, bbox = entry.get("sha256"), entry.get("year"), entry.get("bbox")
    if not (isinstance(sha, str) and _SHA256.fullmatch(sha)):
        return False
    if not isinstance(year, int) or isinstance(year, bool):
        return False
    if not (isinstance(bbox, list) and len(bbox) == 4):
        return False
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in bbox
    ):
        return False
    w, s, e, n = bbox
    return w < e and s < n


def recorded_cutouts(project_dir: Path | str) -> list[dict[str, Any]]:
    """The valid entries of `profile_cutouts`; read-only (W6)."""
    got = _read(project_dir).get(CUTOUTS_KEY)
    if not isinstance(got, list):
        return []
    return [e for e in got if _valid_cutout(e)]


def record_cutouts(
    project_dir: Path | str,
    targets: list[tuple[str, str, str]],
    cutout: dict[str, Any] | None,
    applied_at: str,
) -> None:
    """Set each target's entry to `cutout`, or remove it when `cutout` is None.
    One write for all the targets (W5)."""
    meta = _read(project_dir)
    keys = {tuple(t) for t in targets}
    kept = [
        e
        for e in recorded_cutouts(project_dir)
        if tuple(e[k] for k in _TARGET) not in keys
    ]
    if cutout is not None:
        for t in targets:
            entry = {
                **dict(zip(_TARGET, t)),
                "module": cutout.get("module"),
                "bbox": cutout.get("bbox"),
                "year": cutout.get("year"),
                "sha256": cutout.get("sha256"),
                "applied_at": applied_at,
            }
            if _valid_cutout(entry):
                kept.append(entry)
    if kept == meta.get(CUTOUTS_KEY) or (not kept and CUTOUTS_KEY not in meta):
        return
    if kept:
        meta[CUTOUTS_KEY] = kept
    else:
        meta.pop(CUTOUTS_KEY, None)
    atomic_write_text(_meta_file(project_dir), json.dumps(meta, indent=2))
