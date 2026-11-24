"""The campus electrical equipment schedule (DC review 09).

One table of every electrical asset of the last DC Grid study, each with its
rating, quantity, the criterion that set it, where the rating comes from and
its capex:

* ``computed``: chosen by the study (transformers and reactive compensation
  by ``size_campus``; switchgear and cables by ``rate_campus``);
* ``declared``: typed into the campus file (gensets, BESS, UPS, PV, wind,
  fuel cells);
* ``assumed``: a quantity the study does not know (switchgear bays: one per
  connection at the bus);
* ``not sized``: an asset the study cannot size yet (harmonic filters, DC
  review 08).

A declared rating below the chosen one is reported as ``declared_rating``
with ``flag = "declared_below_chosen"``; the campus file is never changed.
Capex is the library price of the chosen rating times the quantity; a
rating the library does not price has capex None, never zero.

``build(run_dir, library_path)`` returns ``{"rows", "assumptions", "missing"}``;
``to_xlsx(schedule)`` the workbook bytes.
"""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import pandas as pd
import yaml

from gridspine.drivers import campus_study as cs
from gridspine.templates.campus_assets import load_asset_library, value

COLUMNS = [
    "kind",
    "asset",
    "location",
    "rating",
    "unit",
    "quantity",
    "governing",
    "source",
    "declared_rating",
    "flag",
    "library_id",
    "capex_eur",
]
#: Display order of the kinds, the schedule's groups.
KINDS = (
    "transformer",
    "reactive",
    "filter",
    "switchgear",
    "cable",
    "genset",
    "bess",
    "ups",
    "fuel_cell",
    "pv",
    "wind",
)
#: The quantity of every switchgear row (``_bays``), with the capex it multiplies.
BAYS_ASSUMPTION = {
    "applies_to": "switchgear",
    "name": "bays",
    "value": "one per connection at the bus",
    "unit": "",
    "source": "assumed",
}
_UNIT_KINDS = ("genset", "bess", "ups", "fuel_cell", "pv", "wind")


def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _row(**kw) -> dict:
    return {c: kw.get(c) for c in COLUMNS}


def _tagged(field):
    return _num(value(field)) if isinstance(field, dict) else _num(field)


def _transformers(run: Path, spec: dict, lib: dict) -> list:
    sizing = pd.read_csv(run / cs.SIZING_TRAFO_CSV)
    trafos = spec.get("transformers") or {}
    rows = []
    for r in sizing.itertuples(index=False):
        # a group is its transformers' names joined by "+" (campus_flow.trafo_groups)
        members = [trafos[n] for n in str(r.group).split("+") if n in trafos]
        t = members[0] if members else {}
        hv, lv = _tagged(t.get("vn_hv_kv")), _tagged(t.get("vn_lv_kv"))
        n1 = (
            bool(r.n_minus_1)
            and _num(r.max_s_n1_mva) is not None
            and r.max_s_n1_mva >= r.max_s_intact_mva
        )
        flow = (
            f"N-1 {r.max_s_n1_mva:.1f} MVA"
            if n1
            else f"intact {r.max_s_intact_mva:.1f} MVA"
        )
        governing = (
            f"{flow} + {r.margin:.0%} margin = {r.required_unit_mva:.1f} MVA per unit, "
            f"period {int(r.worst_period)} hour {int(r.worst_hour)}"
        )
        rated, units = float(r.recommended_unit_mva), int(r.units)
        hit = [
            e
            for e in lib.get("transformers", [])
            if hv is not None
            and lv is not None
            and math.isclose(value(e["hv_kv"]), hv)
            and math.isclose(value(e["lv_kv"]), lv)
            and math.isclose(value(e["s_mva"]), rated)
        ]
        declared = _num(r.unit_rating_mva)
        rows.append(
            _row(
                kind="transformer",
                asset=str(r.group),
                location=f"{t.get('hv_bus', '?')} / {t.get('lv_bus', '?')}"
                if t
                else None,
                rating=rated,
                unit="MVA",
                quantity=units,
                governing=governing,
                source="computed",
                declared_rating=declared,
                flag="declared_below_chosen"
                if declared is not None and declared < rated - 1e-9
                else None,
                library_id=hit[0]["id"] if hit else None,
                capex_eur=units * float(value(hit[0]["capex_eur"])) if hit else None,
            )
        )
    return rows


def _reactive(run: Path) -> list:
    comp = pd.read_csv(run / cs.SIZING_COMP_CSV)
    rows = []
    for r in comp.itertuples(index=False):
        q = float(r.recommended_mvar)
        when = (
            f", period {int(r.worst_period)} hour {int(r.worst_hour)}"
            if _num(r.worst_period) is not None
            else ""
        )
        rows.append(
            _row(
                kind="reactive",
                asset=f"{r.direction} compensation",
                location="PCC",
                rating=q,
                unit="Mvar",
                quantity=1 if q > 0 else 0,
                governing=(
                    f"PCC reactive band: {float(r.required_mvar):.1f} Mvar + {r.margin:.0%} margin"
                    f"{when}"
                    if q > 0
                    else "none needed: the PCC stays in its band"
                ),
                source="computed",
            )
        )
    return rows


def _bays(spec: dict) -> dict:
    """Connections per bus: one switchgear bay each (assumed)."""
    n: dict = {}

    def add(bus, k=1):
        if bus is not None:
            n[str(bus)] = n.get(str(bus), 0) + k

    for t in (spec.get("transformers") or {}).values():
        add(t.get("hv_bus"))
        add(t.get("lv_bus"))
    for c in (spec.get("cables") or {}).values():
        k = int(c.get("parallel", 1))
        add(c.get("from_bus"), k)
        add(c.get("to_bus"), k)
    for u in (spec.get("units") or {}).values():
        add(u.get("bus"))
    return n


def _switchgear(run: Path, spec: dict) -> list:
    sg = pd.read_csv(run / cs.SWITCHGEAR_RATING_CSV)
    bays = _bays(spec)
    rows = []
    for r in sg.itertuples(index=False):
        n = max(1, bays.get(str(r.bus), 1))
        capex = _num(r.capex_eur)
        rows.append(
            _row(
                kind="switchgear",
                asset=f"{r.bus} switchgear",
                location=f"{r.bus} ({r.vn_kv:g} kV)",
                rating=_num(r.chosen_ka),
                unit="kA",
                quantity=n,
                governing=f"{r.governing}; fault {r.tk_s:g} s, rated {r.tkr_s:g} s"
                if _num(r.chosen_ka) is not None
                else r.governing,
                source="computed",
                declared_rating=_num(r.declared_ka),
                flag=r.flag if isinstance(r.flag, str) else None,
                library_id=r.chosen_id if isinstance(r.chosen_id, str) else None,
                capex_eur=n * capex if capex is not None else None,
            )
        )
    return rows


def _cables(run: Path) -> list:
    cb = pd.read_csv(run / cs.CABLE_RATING_CSV)
    rows = []
    for r in cb.itertuples(index=False):
        chosen = isinstance(r.chosen_id, str)
        if chosen:
            gov = (
                f"{r.governing} {float(r.utilisation):.0%}: design {float(r.design_i_ka) * 1e3:.0f} A in "
                f"{r.design_case}"
            )
            if r.governing == "voltage drop":
                gov += f" ({float(r.vd_pct):.2f} %)"
            elif r.governing == "short-circuit withstand":
                gov += f" (Ith {float(r.ith_ka):.1f} kA needs {float(r.s_min_mm2):.0f} mm2)"
        else:
            gov = r.governing
        runs = int(r.runs) if chosen else int(r.declared_runs)
        rows.append(
            _row(
                kind="cable",
                asset=str(r.cable),
                location=f"{r.from_bus} - {r.to_bus}, {r.length_km:g} km",
                rating=_num(r.section_mm2) if chosen else None,
                unit=f"mm2 {r.conductor}",
                quantity=runs,
                governing=gov,
                source="computed",
                declared_rating=_num(r.declared_i_ka) * 1e3
                if _num(r.declared_i_ka) is not None
                else None,
                flag=r.flag if isinstance(r.flag, str) else None,
                library_id=r.chosen_id if chosen else None,
                capex_eur=_num(r.capex_eur) if chosen else None,
            )
        )
    return rows


def _units(spec: dict) -> list:
    rows = []
    for name, u in (spec.get("units") or {}).items():
        kind = u.get("kind")
        if kind not in _UNIT_KINDS:
            continue
        s, p = _tagged(u.get("s_mva")), _tagged(u.get("p_mw"))
        e = _tagged(u.get("e_mwh"))
        rating, unit = (s, "MVA") if s is not None else (p, "MW")
        gov = "declared in the campus file" + (f"; {e:g} MWh" if e is not None else "")
        rows.append(
            _row(
                kind=kind,
                asset=str(name),
                location=str(u.get("bus")),
                rating=rating,
                unit=unit,
                quantity=1,
                governing=gov,
                source="declared",
            )
        )
    return rows


def _filters() -> list:
    return [
        _row(
            kind="filter",
            asset="harmonic filters",
            quantity=0,
            source="not sized",
            governing="not sized yet: filter sizing is not part of this study",
        )
    ]


def build(run_dir, library_path=None) -> dict:
    """The schedule of the study in ``run_dir`` (module docstring). A run
    from before ratings existed has no switchgear or cable rows; its
    ``missing`` names what to re-run."""
    run = Path(run_dir)
    spec = yaml.safe_load((run / cs.CAMPUS_YAML).read_text())["campus"]
    lib = load_asset_library(library_path)
    rows = _transformers(run, spec, lib) + _reactive(run) + _filters()
    missing = []
    if (run / cs.SWITCHGEAR_RATING_CSV).is_file() and (
        run / cs.CABLE_RATING_CSV
    ).is_file():
        rows += _switchgear(run, spec) + _cables(run)
    else:
        missing.append("switchgear and cable ratings: run the DC Grid study again")
    rows += _units(spec)
    rows.sort(key=lambda r: KINDS.index(r["kind"]))
    path = run / cs.RATING_ASSUMPTIONS_JSON
    assumptions = json.loads(path.read_text()) if path.is_file() else []
    if assumptions:
        assumptions.append(BAYS_ASSUMPTION)
    return {"rows": rows, "assumptions": assumptions, "missing": missing}


_HEAD = {
    "kind": "Kind",
    "asset": "Asset",
    "location": "Location",
    "rating": "Rating",
    "unit": "Unit",
    "quantity": "Qty",
    "governing": "Governing criterion",
    "source": "Source",
    "declared_rating": "Declared rating",
    "flag": "Flag",
    "library_id": "Library id",
    "capex_eur": "Capex (EUR)",
}


def to_xlsx(schedule: dict) -> bytes:
    """Two sheets: ``Schedule`` (the rows, in order) and ``Assumptions``."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(schedule["rows"], columns=COLUMNS).rename(columns=_HEAD).to_excel(
            xw, sheet_name="Schedule", index=False
        )
        pd.DataFrame(
            schedule["assumptions"],
            columns=["applies_to", "name", "value", "unit", "source"],
        ).to_excel(xw, sheet_name="Assumptions", index=False)
    return buf.getvalue()


def for_project(project) -> dict:
    """The schedule of the project's published DC Grid study; empty rows and
    ``missing`` saying so when no study has run."""
    from services import campus_electrical_service as ce

    d = ce.campus_dir(project)
    run = d / "run"
    if not (run / cs.SIZING_TRAFO_CSV).is_file():
        return {
            "rows": [],
            "assumptions": [],
            "missing": ["no DC Grid study has run: run it in Campus electrical"],
        }
    return build(run, ce.effective_library(d))
