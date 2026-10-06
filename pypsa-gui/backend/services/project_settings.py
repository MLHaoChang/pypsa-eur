"""
Per-project settings, read from the project's `metadata.json`.

    {"...": "...", "settings": {"derive_lengths_from_geometry": true}}

The settings live in `metadata.json` rather than a sidecar of their own
because they must travel with the bundle and survive a scenario fork, and
`metadata.json` already does both. `routers/projects.py` owns the WRITE
(`PATCH /api/projects/{name}/settings`, and the save path carries the key
forward); this module is the READ side the network routes use — a service
must not import a router, and a bus drag needs to know whether lengths follow
geometry (plan M2).

Defaults are the fallback for every failure: a missing file, a corrupt one,
a `settings` that is not an object, a flag that is not a boolean. A setting
that cannot be read is a setting that is off.
"""
from __future__ import annotations

import json
import pathlib

SETTINGS_KEY = "settings"
DERIVE_LENGTHS = "derive_lengths_from_geometry"
DEFAULTS: dict[str, bool] = {DERIVE_LENGTHS: False}


def read_settings(project_dir: pathlib.Path | str | None) -> dict:
    """The project's settings table, defaults filled in; never raises."""
    out = dict(DEFAULTS)
    if not project_dir:
        return out
    path = pathlib.Path(project_dir) / "metadata.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return out
    table = meta.get(SETTINGS_KEY) if isinstance(meta, dict) else None
    if isinstance(table, dict):
        for key, default in DEFAULTS.items():
            value = table.get(key, default)
            # A boolean flag stays a boolean: "yes" or 1 is not a setting.
            out[key] = value if isinstance(value, bool) else default
    return out


def derive_lengths_enabled(project_dir: pathlib.Path | str | None) -> bool:
    """"Derive lengths from geometry" for this project; False when unknown."""
    return read_settings(project_dir)[DERIVE_LENGTHS]
