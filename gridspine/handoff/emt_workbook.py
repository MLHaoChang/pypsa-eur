"""
The EMT parameter workbook: plant data, one CSV per sheet, for PSCAD and RSCAD.

An EMT or HIL model (TRGC 05, 07, 17, 19–23) is built by hand from plant
data. The workbook lists what the campus model holds, sheet by sheet, and
names every field an EMT model needs that it does not hold. Nothing is
invented and nothing is written as zero: a missing field is an empty cell
and a row of ``to_be_supplied.csv`` (sheet, element, param, why).

Sheets (:data:`SHEETS`): ``plant_units``, ``converters``, ``transformers``,
``cables``, ``filters``, ``protection_relays``, ``insulation``. A row is one
element. A value column is headed ``<param> [<unit>]`` and is followed by
``<param> source``, the campus tag (measured, datasheet, assumed). The unit
of a column is the unit the campus schema fixes by the field's name
(:data:`PARAM_UNITS`): ``pfe_kw`` stays in kW beside ``sn_mva`` in MVA; no
value is converted. A field without a unit entry is refused, never guessed.

Fields the campus schema never holds are always listed as to be supplied:
transformer saturation and inrush data, cable geometry for a
frequency-dependent model, the manufacturer's EMT converter model, a
time-delayed relay's curve, and the insulation data (BIL, surge arrester).

``RSCAD_NOTES.md`` names the same sheets for a real-time (HIL) build.

Generic engineering only; TRGC numbers are cited, nothing else.
"""

import math

import pandas as pd

from gridspine.ingest.campus import _UNIT_FIELDS
from gridspine.schema.contracts import ContractError

#: The unit of each field, fixed by the campus schema's field name.
PARAM_UNITS = {
    "p_mw": "MW",
    "s_mva": "MVA",
    "e_mwh": "MWh",
    "pf": "-",
    "k_sc": "pu",
    "rx_sc": "-",
    "xd_pp": "pu",
    "i_max_pu": "pu",
    "pf_rated": "-",
    "emt_model": "-",
    "sn_mva": "MVA",
    "vn_hv_kv": "kV",
    "vn_lv_kv": "kV",
    "vk_percent": "%",
    "vkr_percent": "%",
    "pfe_kw": "kW",
    "i0_percent": "%",
    "vector_group": "-",
    "saturation_curve": "pu flux vs pu current",
    "knee_voltage_pu": "pu",
    "air_core_reactance_pu": "pu",
    "length_km": "km",
    "r_ohm_per_km": "ohm/km",
    "x_ohm_per_km": "ohm/km",
    "c_nf_per_km": "nF/km",
    "max_i_ka": "kA",
    "r0_ohm_per_km": "ohm/km",
    "x0_ohm_per_km": "ohm/km",
    "c0_nf_per_km": "nF/km",
    "cable_geometry": "-",
    "q_mvar": "Mvar",
    "steps": "-",
    "i_pickup_ka": "kA",
    "i_pickup_e_ka": "kA",
    "t_min_s": "s",
    "curve": "-",
    "vn_kv": "kV",
    "ik_rated_ka": "kA",
    "bil_kv": "kV",
    "arrester_ur_kv": "kV",
    "arrester_uc_kv": "kV",
    "arrester_energy_kj_per_kv": "kJ/kV",
}

CONVERTER_KINDS = ("bess", "pv", "wind", "fuel_cell", "ups")
_PLANT = ("p_mw", "s_mva", "e_mwh", "pf", "xd_pp")
_CONVERTER = ("s_mva", "k_sc", "rx_sc", "i_max_pu", "emt_model")
_TRAFO = (
    "sn_mva",
    "vn_hv_kv",
    "vn_lv_kv",
    "vk_percent",
    "vkr_percent",
    "pfe_kw",
    "i0_percent",
    "vector_group",
    "saturation_curve",
    "knee_voltage_pu",
    "air_core_reactance_pu",
)
_CABLE = (
    "length_km",
    "r_ohm_per_km",
    "x_ohm_per_km",
    "c_nf_per_km",
    "max_i_ka",
    "r0_ohm_per_km",
    "x0_ohm_per_km",
    "c0_nf_per_km",
    "cable_geometry",
)
_FILTER = ("q_mvar", "steps")
_RELAY = ("i_pickup_ka", "i_pickup_e_ka", "t_min_s", "curve")
_INSULATION = (
    "vn_kv",
    "ik_rated_ka",
    "bil_kv",
    "arrester_ur_kv",
    "arrester_uc_kv",
    "arrester_energy_kj_per_kv",
)

#: Why an EMT model needs a field the campus schema never holds.
EMT_ONLY = {
    "emt_model": "the manufacturer's EMT converter model and its parameter set (TRGC 05, 07, 21)",
    "saturation_curve": "transformer saturation for energisation and inrush studies (TRGC 17, 22)",
    "knee_voltage_pu": "transformer saturation knee for energisation and inrush studies (TRGC 17, 22)",
    "air_core_reactance_pu": "transformer air-core reactance for inrush studies (TRGC 17, 22)",
    "cable_geometry": "conductor and screen geometry and laying for a frequency-dependent cable model (TRGC 22)",
    "bil_kv": "basic insulation level for insulation coordination (TRGC 22)",
    "arrester_ur_kv": "surge arrester rated voltage for insulation coordination (TRGC 22)",
    "arrester_uc_kv": "surge arrester continuous operating voltage for insulation coordination (TRGC 22)",
    "arrester_energy_kj_per_kv": "surge arrester energy capability for insulation coordination (TRGC 22)",
}
_CABLE_ZERO_WHY = "cable zero sequence for unbalanced faults and auto-reclose (TRGC 17)"
_CURVE_WHY = "a time-delayed relay's characteristic (curve type and time multiplier) for auto-reclose (TRGC 17)"
_IMAX_WHY = (
    "converter current limit for fault ride-through in the EMT model (TRGC 05, 07)"
)
_PLANT_WHY = "required by the campus schema for this unit kind"

SUPPLY_COLUMNS = ["sheet", "element", "param", "why"]
SHEETS = (
    "plant_units",
    "converters",
    "transformers",
    "cables",
    "filters",
    "protection_relays",
    "insulation",
)
RSCAD_NOTES = "RSCAD_NOTES.md"


def header(param) -> str:
    """``<param> [<unit>]``; a field without a unit entry is refused."""
    if param not in PARAM_UNITS:
        raise ContractError(
            f"workbook field {param!r} has no unit in PARAM_UNITS; a unit is never guessed"
        )
    return f"{param} [{PARAM_UNITS[param]}]"


class _Sheet:
    def __init__(self, name, fields, held):
        self.name, self.fields, self.held = name, fields, held
        self.rows, self.gaps = [], []

    def add(self, element, fixed, required):
        """
        One row for ``element``: ``fixed`` text columns, then every field's
        value and source; each ``required`` field not held is a gap with its
        reason.
        """
        row = {"element": element, **fixed}
        for p in self.fields:
            v = self.held.get((element, p))
            row[header(p)] = None if v is None else v[0]
            row[f"{p} source"] = None if v is None else v[1]
            if v is None and p in required:
                self.gaps.append(
                    {
                        "sheet": self.name,
                        "element": element,
                        "param": p,
                        "why": required[p],
                    }
                )
        self.rows.append(row)

    def frame(self, fixed_cols) -> pd.DataFrame:
        cols = ["element", *fixed_cols]
        for p in self.fields:
            cols += [header(p), f"{p} source"]
        return pd.DataFrame(self.rows, columns=cols)


def _held(campus) -> dict:
    """``{(element, param): (value, source)}`` from the campus's tagged values."""
    p = campus.params
    return {
        (str(r.element), str(r.param)): (float(r.value), str(r.source))
        for r in p.itertuples()
    }


def _emt(fields) -> dict:
    return {f: EMT_ONLY[f] for f in fields if f in EMT_ONLY}


def workbook(campus) -> tuple:
    """``({sheet: frame}, to_be_supplied frame)`` for the campus."""
    held = _held(campus)
    net = campus.net
    sheets = {}

    s = _Sheet("plant_units", _PLANT, held)
    for uid, u in campus.units.sort_index().iterrows():
        s.add(
            str(uid),
            {"kind": u["kind"], "bus": u["bus"]},
            {f: _PLANT_WHY for f in _UNIT_FIELDS[u["kind"]] if f in _PLANT},
        )
    sheets["plant_units"] = (s, ["kind", "bus"])

    s = _Sheet("converters", _CONVERTER, held)
    blocks = campus.island["units"]
    for uid, u in campus.units.sort_index().iterrows():
        if u["kind"] not in CONVERTER_KINDS:
            continue
        control = (blocks.get(uid) or {}).get("control")
        need = {"s_mva": _PLANT_WHY, **_emt(_CONVERTER)}
        if u["kind"] != "ups":
            need["i_max_pu"] = _IMAX_WHY
        s.add(str(uid), {"kind": u["kind"], "bus": u["bus"], "control": control}, need)
    for name, c in campus.compensation.sort_index().iterrows():
        if c["kind"] == "statcom":
            s.add(
                str(name),
                {"kind": "statcom", "bus": c["bus"], "control": None},
                {"emt_model": EMT_ONLY["emt_model"]},
            )
    sheets["converters"] = (s, ["kind", "bus", "control"])

    s = _Sheet("transformers", _TRAFO, held)
    for _, t in net.trafo.sort_values("name").iterrows():
        name = str(t["name"])
        w = campus.windings.get(name)
        fixed = {
            "hv_bus": str(net.bus.at[t["hv_bus"], "name"]),
            "lv_bus": str(net.bus.at[t["lv_bus"], "name"]),
        }
        if w:
            assumed = any(
                a.startswith(f"{name}: no vector_group") for a in campus.assumptions
            )
            s.held[(name, "vector_group")] = (
                _vector_group(w),
                "assumed" if assumed else "given",
            )
        s.add(name, fixed, {**{f: _PLANT_WHY for f in _TRAFO[:7]}, **_emt(_TRAFO)})
    sheets["transformers"] = (s, ["hv_bus", "lv_bus"])

    s = _Sheet("cables", _CABLE, held)
    for _, ln in net.line.sort_values("name").iterrows():
        need = {
            **{f: _PLANT_WHY for f in _CABLE[:5]},
            **{f: _CABLE_ZERO_WHY for f in _CABLE[5:8]},
            **_emt(_CABLE),
        }
        s.add(
            str(ln["name"]),
            {
                "from_bus": str(net.bus.at[ln["from_bus"], "name"]),
                "to_bus": str(net.bus.at[ln["to_bus"], "name"]),
            },
            need,
        )
    sheets["cables"] = (s, ["from_bus", "to_bus"])

    s = _Sheet("filters", _FILTER, held)
    for name, c in campus.compensation.sort_index().iterrows():
        if c["kind"] == "statcom":
            continue
        if c["kind"] == "capacitor_bank" and _finite(c["steps"]):
            s.held[(str(name), "steps")] = (float(c["steps"]), "campus compensation")
        need = {"q_mvar": _PLANT_WHY}
        if c["kind"] == "capacitor_bank":
            need["steps"] = _PLANT_WHY
        s.add(str(name), {"kind": c["kind"], "bus": c["bus"]}, need)
    sheets["filters"] = (s, ["kind", "bus"])

    s = _Sheet("protection_relays", _RELAY, held)
    for _, r in campus.relays.sort_values("relay").iterrows():
        need = {"curve": _CURVE_WHY} if r["timing"] == "time_delayed" else {}
        s.add(
            str(r["relay"]),
            {
                "branch": r["branch"],
                "kind": r["kind"],
                "fault_bus": r["fault_bus"],
                "timing": r["timing"],
            },
            need,
        )
    sheets["protection_relays"] = (s, ["branch", "kind", "fault_bus", "timing"])

    s = _Sheet("insulation", _INSULATION, held)
    for _, b in net.bus.sort_values("name").iterrows():
        name = str(b["name"])
        s.held[(name, "vn_kv")] = (float(b["vn_kv"]), "campus bus (definition)")
        s.add(name, {}, _emt(_INSULATION))
    sheets["insulation"] = (s, [])

    frames = {name: sh.frame(cols) for name, (sh, cols) in sheets.items()}
    gaps = [g for sh, _ in sheets.values() for g in sh.gaps]
    return frames, pd.DataFrame(gaps, columns=SUPPLY_COLUMNS)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _vector_group(w) -> str:
    hv = w["hv"] + ("N" if w.get("hv_earth") not in (None, "isolated") else "")
    lv = w["lv"].lower() + ("n" if w.get("lv_earth") not in (None, "isolated") else "")
    return f"{hv}{lv}{w['clock']}"


def rscad_notes() -> str:
    """The RSCAD (HIL, TRGC 23) note: the same sheets, how they map."""
    lines = [
        "# RSCAD notes (HIL, TRGC 23)",
        "",
        "The parameter workbook in this pack serves the real-time (RSCAD) build as it serves PSCAD.",
        "Each sheet is one CSV, one row per element; a value column is `<param> [<unit>]`, followed by",
        "`<param> source` (measured, datasheet, assumed). Empty cells are listed in `to_be_supplied.csv`;",
        "none is zero.",
        "",
        "| Sheet | File | RSCAD use |",
        "|---|---|---|",
        "| plant_units | `plant_units.csv` | ratings of every unit; loads as dynamic loads |",
        "| converters | `converters.csv` | converter ratings and current limit; the manufacturer's real-time "
        "model replaces the EMT model in HIL |",
        "| transformers | `transformers.csv` | transformer models incl. saturation (to be supplied) |",
        "| cables | `cables.csv` | PI or travelling-wave cable sections |",
        "| filters | `filters.csv` | capacitor banks and shunt reactors |",
        "| protection_relays | `protection_relays.csv` | relay settings for the protection under test |",
        "| insulation | `insulation.csv` | BIL and arrester data (not used in HIL; listed for completeness) |",
        "",
        "The Thevenin set (`thevenin_set.csv`) and the case schedule (`case_schedule.csv`) give the grid",
        "source and the test cases; the real-time time step is the engineer's choice.",
        "",
    ]
    return "\n".join(lines)
