# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""
Public generic WECC parameters for a grid-following converter that has no
supplied dynamic data (owner decision GC04-DEFAULTS-1, "Generic, tagged").

The set and its per-block citations are in ``data/wecc_generic.yaml``:
REGCA1 + REECA1 for a converter, REGCA1 + REECC1 for a battery, and REPCA1
for the aggregated plant. Every value is tagged ``assumed``. A pack that uses
any of them is marked ``NOT_FOR_SUBMISSION`` and lists each assumed group.
The campus bundle's own ``campus_assets.yaml`` is not touched.

``load_generic`` refuses a block that does not cover its model's fields
exactly, a value that is not a finite number, and a set that fails the
template loader's own converter checks (``unit_params``), so a value edited
into the file cannot reach a ``.dyr`` unchecked.
"""

import math
from pathlib import Path

import yaml

from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import (
    REECA1_CONS,
    REECA1_ICONS,
    REECC1_CONS,
    REECC1_ICONS,
    REECC1_ONLY,
    REGCA1_CONS,
    REGCA1_ICONS,
    REPCA1_CONS,
    REPCA1_ICONS,
    _check_physics,
)

PATH = Path(__file__).parent / "data" / "wecc_generic.yaml"
SOURCE = "assumed"
NOT_FOR_SUBMISSION = (
    "not for submission: generic WECC parameters (assumed) until OEM data"
)

#: block -> (key prefix, the unit_params fields it must cover exactly, PSS/E model).
BLOCKS = {
    "regc": ("regc_", REGCA1_ICONS + REGCA1_CONS, "REGCA1"),
    "reec": ("reec_", REECA1_ICONS + REECA1_CONS, "REECA1"),
    "reecc": ("reec_", REECC1_ICONS + REECC1_CONS + REECC1_ONLY, "REECC1"),
    "repc": ("repc_", REPCA1_ICONS + REPCA1_CONS, "REPCA1"),
}


def load_generic(path=None) -> dict:
    """Block -> {unit_params field: value}, checked as described above."""
    raw = yaml.safe_load(Path(path or PATH).read_text())
    blocks = raw.get("blocks") if isinstance(raw, dict) else None
    if not isinstance(blocks, dict) or set(blocks) != set(BLOCKS):
        got = sorted(blocks) if isinstance(blocks, dict) else blocks
        raise ContractError(
            f"generic WECC set: blocks must be exactly {sorted(BLOCKS)}, got {got}"
        )
    out = {}
    for name, (prefix, fields, model) in BLOCKS.items():
        spec = blocks[name]
        if not isinstance(spec, dict) or not str(spec.get("source") or "").strip():
            raise ContractError(
                f"generic WECC set: block {name} ({model}) has no source"
            )
        values = spec.get("values")
        if not isinstance(values, dict):
            raise ContractError(f"generic WECC set: block {name} has no values mapping")
        got = {prefix + k: v for k, v in values.items()}
        missing, extra = sorted(set(fields) - set(got)), sorted(set(got) - set(fields))
        if missing or extra:
            raise ContractError(
                f"generic WECC set: block {name} must cover {model}'s fields exactly; "
                f"missing {missing}, unknown {extra}"
            )
        block = {}
        for field in fields:
            entry = got[field]
            if not isinstance(entry, dict) or not str(entry.get("from") or "").strip():
                raise ContractError(
                    f"generic WECC set: {name}.{field} needs a value and a 'from' citation"
                )
            v = entry.get("value")
            if (
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
            ):
                raise ContractError(
                    f"generic WECC set: {name}.{field} must be a finite number, got {v!r}"
                )
            block[field] = float(v)
        out[name] = block
    for battery in (False, True):
        _check_physics(
            f"generic {'battery' if battery else 'converter'}",
            "inverter",
            converter_set(out, battery=battery, plant=True),
            False,
            1.0,
        )
    return out


def groups(battery: bool, plant: bool) -> tuple:
    """
    The blocks a converter takes: REGCA1 with REECC1 (battery) or REECA1,
    plus REPCA1 when it is the plant's aggregated machine.
    """
    return ("regc", "reecc" if battery else "reec") + (("repc",) if plant else ())


def converter_set(generic: dict, battery: bool, plant: bool) -> dict:
    """One converter's fields, merged from its blocks."""
    out = {}
    for name in groups(battery, plant):
        out |= generic[name]
    return out
