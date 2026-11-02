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
a `settings` that is not an object, a flag that is not a boolean, an autonomy
that is not a finite number in (0, 720] h. A setting that cannot be read is
its default.
"""
from __future__ import annotations

import json
import math
import pathlib

from models.design_intent import BACKUP_AUTONOMY_DEFAULT_H, BACKUP_AUTONOMY_MAX_H

SETTINGS_KEY = "settings"
DERIVE_LENGTHS = "derive_lengths_from_geometry"
#: AS-AUTONOMY-1: the intent-A backup autonomy in hours (owner decision
#: AS10-AUTONOMY-2), the value the intent comparison starts from.
BACKUP_AUTONOMY = "backup_autonomy_h"
DEFAULTS: dict[str, object] = {DERIVE_LENGTHS: False, BACKUP_AUTONOMY: BACKUP_AUTONOMY_DEFAULT_H}


def _hours(value: object) -> float | None:
    """A finite number in (0, BACKUP_AUTONOMY_MAX_H] as a float, else None.
    A bool is refused explicitly: it is an int to Python."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # The bound check first: NaN fails both comparisons and +inf fails the
    # upper one, and a huge int never reaches float().
    if not 0 < value <= BACKUP_AUTONOMY_MAX_H:
        return None
    hours = float(value)
    return hours if math.isfinite(hours) else None


def _flag(value: object) -> bool | None:
    # A boolean flag stays a boolean: "yes" or 1 is not a setting.
    return value if isinstance(value, bool) else None


#: One check per setting: the value as stored, or None when it is not valid.
_CHECKS = {DERIVE_LENGTHS: _flag, BACKUP_AUTONOMY: _hours}


def settings_from_table(table: object) -> dict:
    """
    The settings `table` (the `settings` entry of a metadata.json) with every
    known key checked and defaults filled in; never raises. The one validator
    both readers use: `read_settings` below and the projects router's
    `ProjectInfo.settings`, so a route and the UI agree on every stored value.
    """
    out = dict(DEFAULTS)
    if isinstance(table, dict):
        for key, check in _CHECKS.items():
            if key in table:
                value = check(table[key])
                if value is not None:
                    out[key] = value
    return out


def read_settings(project_dir: pathlib.Path | str | None) -> dict:
    """The project's settings table, defaults filled in; never raises."""
    if not project_dir:
        return dict(DEFAULTS)
    path = pathlib.Path(project_dir) / "metadata.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return dict(DEFAULTS)
    return settings_from_table(meta.get(SETTINGS_KEY) if isinstance(meta, dict) else None)


def derive_lengths_enabled(project_dir: pathlib.Path | str | None) -> bool:
    """"Derive lengths from geometry" for this project; False when unknown."""
    return read_settings(project_dir)[DERIVE_LENGTHS]
