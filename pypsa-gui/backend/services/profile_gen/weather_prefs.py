"""The weather bar's saved choice (RP-04b, spec R3).

One source and one year per project, kept in the project's metadata under
`profile_weather`. It is the default of every generate that names neither: the
Time Series Manager's per-generator Generate, Profile Studio and the chat
assistant read the same value. Nothing saved means RP-03's default (the
snapshots' year), so the bar shows "from your snapshots" or "default".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.atomic_io import atomic_write_text
from services.profile_gen.horizon import default_weather_year, last_completed_year

META_KEY = "profile_weather"
SOURCES = ("synthetic", "atlite")
FIRST_YEAR = 1950  # the ERA5 record starts here


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
    if not FIRST_YEAR <= year <= last:
        raise WeatherChoiceError(f"the weather year is a completed year from {FIRST_YEAR} to {last}, not {year}")


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
