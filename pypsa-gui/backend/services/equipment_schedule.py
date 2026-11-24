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

# Guarded like services/campus_electrical_service.py: a build without
# gridspine still loads the router, and ``for_project`` answers 503.
try:
    from gridspine.drivers import campus_study as cs
    from gridspine.templates.campus_assets import load_asset_library, value
except ImportError:  # pragma: no cover - build-shape branch
    cs = load_asset_library = value = None

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
    "declared_unit",
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


NOT_EVALUATED = "not evaluated"
#: Kept with the run by the DC Grid study: the library the ratings were chosen
#: from, so every capex in the schedule is priced on that one basis.
RATINGS_LIBRARY_FILE = "campus_ratings_library.yaml"


def _fmt(x, spec: str, suffix: str = "") -> str:
    """``x`` formatted, or "not evaluated" for a missing or NaN figure."""
    f = _num(x)
    return NOT_EVALUATED if f is None else f"{f:{spec}}{suffix}"


def _text(x):
    """A name or id read as text ("1234" and "010" stay as written); None when empty."""
    return x if isinstance(x, str) and x != "" else None


def _read(path: Path, text: tuple, columns: tuple = ()) -> pd.DataFrame:
    """A ratings or sizing table with its id and name columns as text and
    every expected column present (a missing one reads NaN): a run that
    rated nothing writes fewer columns."""
    df = pd.read_csv(path, dtype={c: str for c in text})
    return df.reindex(
        columns=list(df.columns) + [c for c in (*text, *columns) if c not in df.columns]
    )


def _row(**kw) -> dict:
    return {c: kw.get(c) for c in COLUMNS}


def _tagged(field):
    return _num(value(field)) if isinstance(field, dict) else _num(field)


def _transformers(run: Path, spec: dict, lib: dict) -> list:
    sizing = _read(
        run / cs.SIZING_TRAFO_CSV,
        ("group",),
        (
            "n_minus_1",
            "max_s_n1_mva",
            "max_s_intact_mva",
            "margin",
            "required_unit_mva",
            "worst_period",
            "worst_hour",
            "recommended_unit_mva",
            "units",
            "unit_rating_mva",
        ),
    )
    trafos = spec.get("transformers") or {}
    rows = []
    for r in sizing.itertuples(index=False):
        # a group is its transformers' names joined by "+" (campus_flow.trafo_groups)
        members = [trafos[n] for n in str(r.group).split("+") if n in trafos]
        t = members[0] if members else {}
        hv, lv = _tagged(t.get("vn_hv_kv")), _tagged(t.get("vn_lv_kv"))
        n1 = (
            _num(r.n_minus_1) is not None
            and bool(r.n_minus_1)
            and _num(r.max_s_n1_mva) is not None
            and r.max_s_n1_mva >= r.max_s_intact_mva
        )
        flow = (
            f"N-1 {_fmt(r.max_s_n1_mva, '.1f', ' MVA')}"
            if n1
            else f"intact {_fmt(r.max_s_intact_mva, '.1f', ' MVA')}"
        )
        governing = (
            f"{flow} + {_fmt(r.margin, '.0%')} margin = {_fmt(r.required_unit_mva, '.1f', ' MVA')} per unit, "
            f"period {_fmt(r.worst_period, '.0f')} hour {_fmt(r.worst_hour, '.0f')}"
        )
        rated, units = _num(r.recommended_unit_mva), _num(r.units)
        units = int(units) if units is not None else None
        hit = [
            e
            for e in lib.get("transformers", [])
            if hv is not None
            and lv is not None
            and rated is not None
            and math.isclose(value(e["hv_kv"]), hv)
            and math.isclose(value(e["lv_kv"]), lv)
            and math.isclose(value(e["s_mva"]), rated)
        ]
        declared = _num(r.unit_rating_mva)
        rows.append(
            _row(
                kind="transformer",
                asset=_text(r.group),
                location=f"{t.get('hv_bus', '?')} / {t.get('lv_bus', '?')}"
                if t
                else None,
                rating=rated,
                unit="MVA",
                quantity=units,
                governing=governing,
                source="computed",
                declared_rating=declared,
                declared_unit="MVA" if declared is not None else None,
                flag="declared_below_chosen"
                if declared is not None
                and rated is not None
                and declared < rated - 1e-9
                else None,
                library_id=str(hit[0]["id"]) if hit else None,
                capex_eur=units * float(value(hit[0]["capex_eur"]))
                if hit and units is not None
                else None,
            )
        )
    return rows


def _reactive(run: Path) -> list:
    comp = _read(
        run / cs.SIZING_COMP_CSV,
        ("direction",),
        ("recommended_mvar", "required_mvar", "margin", "worst_period", "worst_hour"),
    )
    rows = []
    for r in comp.itertuples(index=False):
        q = _num(r.recommended_mvar)
        when = (
            f", period {int(r.worst_period)} hour {_fmt(r.worst_hour, '.0f')}"
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
                quantity=None if q is None else 1 if q > 0 else 0,
                governing=(
                    f"PCC reactive band: {NOT_EVALUATED}"
                    if q is None
                    else f"PCC reactive band: {_fmt(r.required_mvar, '.1f', ' Mvar')} + {_fmt(r.margin, '.0%')} margin"
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
    sg = _read(
        run / cs.SWITCHGEAR_RATING_CSV,
        ("bus", "chosen_id", "governing", "flag"),
        ("vn_kv", "chosen_ka", "capex_eur", "tk_s", "tkr_s", "declared_ka"),
    )
    bays = _bays(spec)
    rows = []
    for r in sg.itertuples(index=False):
        n = max(1, bays.get(str(r.bus), 1))
        rated = _num(r.chosen_ka)
        capex = _num(r.capex_eur) if rated is not None else None
        rows.append(
            _row(
                kind="switchgear",
                asset=f"{r.bus} switchgear",
                location=f"{r.bus} ({_fmt(r.vn_kv, 'g', ' kV')})",
                rating=rated,
                unit="kA",
                quantity=n,
                governing=f"{r.governing}; fault {_fmt(r.tk_s, 'g', ' s')}, rated {_fmt(r.tkr_s, 'g', ' s')}"
                if rated is not None
                else _text(r.governing) or NOT_EVALUATED,
                source="computed",
                declared_rating=_num(r.declared_ka),
                declared_unit="kA" if _num(r.declared_ka) is not None else None,
                flag=_text(r.flag),
                library_id=_text(r.chosen_id) if rated is not None else None,
                capex_eur=n * capex if capex is not None else None,
            )
        )
    return rows


def _cables(run: Path) -> list:
    cb = _read(
        run / cs.CABLE_RATING_CSV,
        (
            "cable",
            "from_bus",
            "to_bus",
            "chosen_id",
            "governing",
            "design_case",
            "conductor",
            "flag",
        ),
        (
            "length_km",
            "declared_runs",
            "declared_i_ka",
            "design_i_ka",
            "ith_ka",
            "runs",
            "section_mm2",
            "capex_eur",
            "utilisation",
            "vd_pct",
            "s_min_mm2",
        ),
    )
    rows = []
    for r in cb.itertuples(index=False):
        chosen = _text(r.chosen_id) is not None and _num(r.section_mm2) is not None
        if chosen:
            gov = (
                f"{r.governing} {_fmt(r.utilisation, '.0%')}: design "
                f"{_fmt(_num(r.design_i_ka) * 1e3 if _num(r.design_i_ka) is not None else None, '.0f', ' A')} in "
                f"{_text(r.design_case) or NOT_EVALUATED}"
            )
            if r.governing == "voltage drop":
                gov += f" ({_fmt(r.vd_pct, '.2f', ' %')})"
            elif r.governing == "short-circuit withstand":
                gov += f" (Ith {_fmt(r.ith_ka, '.1f', ' kA')} needs {_fmt(r.s_min_mm2, '.0f', ' mm2')})"
        else:
            gov = _text(r.governing) or NOT_EVALUATED
        runs = _num(r.runs) if chosen else _num(r.declared_runs)
        declared = _num(r.declared_i_ka)
        rows.append(
            _row(
                kind="cable",
                asset=_text(r.cable),
                location=f"{r.from_bus} - {r.to_bus}, {_fmt(r.length_km, 'g', ' km')}",
                rating=_num(r.section_mm2) if chosen else None,
                unit=f"mm2 {r.conductor}" if _text(r.conductor) else "mm2",
                quantity=int(runs) if runs is not None else None,
                governing=gov,
                source="computed",
                declared_rating=declared * 1e3 if declared is not None else None,
                declared_unit="A" if declared is not None else None,
                flag=_text(r.flag),
                library_id=_text(r.chosen_id) if chosen else None,
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
    kept = run / RATINGS_LIBRARY_FILE
    lib = load_asset_library(kept if kept.is_file() else library_path)
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
    return {
        "rows": rows,
        "assumptions": assumptions,
        "missing": missing,
        "stale": False,
    }


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
    "declared_unit": "Declared unit",
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

    ce.require_engine()
    d = ce.campus_dir(project)
    run = d / "run"
    if not (run / cs.SIZING_TRAFO_CSV).is_file():
        return {
            "rows": [],
            "assumptions": [],
            "missing": ["no DC Grid study has run: run it in Campus electrical"],
            "stale": False,
        }
    out = build(run, ce.effective_library(d))
    kept = run / RATINGS_LIBRARY_FILE
    library_changed = (
        kept.is_file() and kept.read_bytes() != ce.effective_library(d).read_bytes()
    )
    # The study's inputs (campus file, network) or the library a run now would
    # use differ from the ones this schedule was made from: run it again.
    out["stale"] = bool(ce._stale(project, d) or library_changed)
    return out
