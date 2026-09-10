"""Stage 2: IEC 60909 three-phase fault levels, per bus, per selected hour.

THIS MODULE HAS ITS OWN STATUS -> ELEMENT MAPPING. Do not reuse
``loadflow._apply_res`` here, and do not "fix" ``_apply_res`` either.

``apply_snapshot`` maps a curtailed RES unit (status 0, p_mw 0) to
``in_service=False``. For load flow that is exact: a zero-injection PQ element
and an absent one are the same node equation. For short circuit it is wrong: a
curtailed inverter is still energised, still synchronised, and still feeds
fault current up to its converter limit, so ``in_service=False`` deletes it
from the calculation and understates the level at exactly the buses the study
is about. ``apply_fault_state`` therefore keeps every RES unit energised and
takes only DECOMMITTED SYNCHRONOUS machines out — a synchronous unit with
status 0 is disconnected from the grid, an inverter with status 0 is not.

Known limit, ledgered: the dispatch table cannot distinguish "curtailed" from
"on outage" for a RES unit (both are status 0 / p 0), so every RES unit is
treated as energised. A RES availability table is the increment that fixes it.

Parameters come from ``templates/`` and are tagged there. pandapower 3.1.2
(probed) raises when a required COLUMN is absent but not when a per-unit VALUE
is, so ``set_sc_params`` asserts coverage element by element before the solve:
a fault level computed from half the fleet is a plausible-looking wrong answer.

The ext_grid is pandapower's grid feeder and needs S_k''/R-X, not machine
reactances. Its strength is DERIVED from the template machine on its bus
(the classic set's Gen 2 at BUS_31): S_k'' = mbase / x''d, R/X = rx_sc, and the
min case is set equal to the max. A real interconnection would be far stronger
than one machine; this is recorded in ``FAULT_LEDGER``.

``fault_levels`` works on a DEEP COPY. The fault-state mapping flips curtailed
sgens back in service; done on the caller's net that would flip STAT in a
later .raw export, which is precisely the kind of cross-stage leak the file
boundaries exist to prevent.

Allowed to import pandapower (``static/``); never pypsa.
"""
import copy
import math

import pandapower.shortcircuit as sc
import pandas as pd

from gridspine.schema.contingency import FAULT_CASES, validate_fault_levels
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch
from gridspine.static.loadflow import _apply_loads
from gridspine.templates.unit_params import UnitTemplates

#: Conductor temperature at the end of the fault, needed by the IEC 60909 MIN
#: case to derate line resistance (pandapower raises without it). 80 degC is the
#: standard-practice figure; the repo carries no conductor data. Ledgered.
LINE_ENDTEMP_DEGREE = 80.0

#: Template parameters each element class needs for IEC 60909, beyond mbase_mva.
SYNC_SC_PARAMS = ("xd_pp", "rx_sc", "cos_phi")
INVERTER_SC_PARAMS = ("k_sc", "rx_sc")

FAULT_LEDGER = (
    "fault levels: IEC 60909 three-phase via pandapower.shortcircuit.calc_sc, "
    "voltage factor c per case (max/min); pre-fault load ignored (60909 max case)",
    "RES units are energised in the fault state whether curtailed or not: a "
    "curtailed inverter still feeds fault current; the dispatch table cannot "
    "tell curtailment from outage, so no RES unit is ever taken out (assumed)",
    "decommitted synchronous machines (status 0) are out of the fault "
    "calculation: a synchronous unit with status 0 is disconnected",
    "synchronous machine short-circuit data from the template: x''d on mbase, "
    "R/X = rx_sc, rated cos_phi; terminal voltage taken as the connection bus "
    "voltage (machines sit directly on the transmission bus in case39) (assumed)",
    "ext_grid strength derived from the template machine on its bus: "
    "S_k'' = mbase / x''d, R/X = rx_sc; a real interconnection would be far "
    "stronger than one machine (assumed)",
    "s_sc_min_mva = s_sc_max_mva for the ext_grid: no separate minimum-strength "
    "figure exists for the equivalent (assumed)",
    "inverter short-circuit data from the template: k = k_sc (Ik''/In), "
    "R/X = rx_sc, sn_mva = installed MW read as MVA (assumed)",
    "line end-of-fault conductor temperature 80 degC for the 60909 minimum "
    "case (endtemp_degree): standard practice, no conductor data in the case "
    "(assumed)",
)


def _template_values(templates: UnitTemplates) -> dict:
    out = {}
    for r in templates.params.itertuples(index=False):
        out.setdefault(r.unit_id, {})[r.param] = float(r.value)
    return out


def _need(values: dict, unit_id: str, names) -> None:
    missing = [n for n in names if n not in values.get(unit_id, {})]
    if missing:
        raise ContractError(
            f"unit {unit_id} lacks short-circuit parameter(s) {missing} in the template; "
            "a machine without them would be silently skipped by the solver"
        )


def set_sc_params(net, registry: pd.DataFrame, templates: UnitTemplates) -> None:
    """Write IEC 60909 element data onto the net from the templates, asserting
    every machine on the net is covered. Mutates ``net``."""
    values = _template_values(templates)
    units = templates.units

    def check_row(unit_id):
        if unit_id not in units.index:
            raise ContractError(f"machine {unit_id} has no unit-parameter template row")
        if unit_id not in registry.index:
            raise ContractError(f"machine {unit_id} is on the net but not in the registry")

    for i in net.gen.index:
        uid = net.gen.at[i, "name"]
        check_row(uid)
        _need(values, uid, SYNC_SC_PARAMS)
        v, mbase = values[uid], float(units.at[uid, "mbase_mva"])
        vn_kv = float(net.bus.at[net.gen.at[i, "bus"], "vn_kv"])
        xdss_ohm = v["xd_pp"] * vn_kv ** 2 / mbase
        net.gen.at[i, "vn_kv"] = vn_kv
        net.gen.at[i, "sn_mva"] = mbase
        net.gen.at[i, "xdss_pu"] = v["xd_pp"]
        net.gen.at[i, "rdss_ohm"] = v["rx_sc"] * xdss_ohm
        net.gen.at[i, "cos_phi"] = v["cos_phi"]

    for i in net.ext_grid.index:
        uid = net.ext_grid.at[i, "name"]
        check_row(uid)
        _need(values, uid, SYNC_SC_PARAMS)
        v, mbase = values[uid], float(units.at[uid, "mbase_mva"])
        s_sc = mbase / v["xd_pp"]
        net.ext_grid.at[i, "s_sc_max_mva"] = s_sc
        net.ext_grid.at[i, "s_sc_min_mva"] = s_sc
        net.ext_grid.at[i, "rx_max"] = v["rx_sc"]
        net.ext_grid.at[i, "rx_min"] = v["rx_sc"]

    # The min case derates line resistance to the end-of-fault temperature.
    if "endtemp_degree" not in net.line.columns:
        net.line["endtemp_degree"] = LINE_ENDTEMP_DEGREE
    else:
        net.line["endtemp_degree"] = net.line["endtemp_degree"].fillna(LINE_ENDTEMP_DEGREE)

    sgen = getattr(net, "sgen", None)
    if sgen is not None:
        for i in sgen.index:
            uid = sgen.at[i, "name"]
            check_row(uid)
            _need(values, uid, INVERTER_SC_PARAMS)
            v, mbase = values[uid], float(units.at[uid, "mbase_mva"])
            net.sgen.at[i, "sn_mva"] = mbase
            net.sgen.at[i, "k"] = v["k_sc"]
            net.sgen.at[i, "rx"] = v["rx_sc"]
            net.sgen.at[i, "current_source"] = True


def apply_fault_state(net, dispatch, loads, hour, registry, templates) -> None:
    """Put the hour's FAULT state on the net: loads, synchronous commitment,
    every RES unit energised, and the 60909 element data. Mutates ``net``."""
    table = validate_dispatch(dispatch)
    snap = table[table["hour"] == int(hour)].set_index("unit_id")
    if snap.empty:
        raise ContractError(f"dispatch table has no rows for hour {hour}")
    _apply_loads(net, loads, int(hour))

    gen_idx = {net.gen.at[i, "name"]: i for i in net.gen.index}
    sgen = getattr(net, "sgen", None)
    sgen_idx = {sgen.at[i, "name"]: i for i in sgen.index} if sgen is not None else {}
    for unit_id, rec in registry.iterrows():
        if unit_id not in snap.index:
            raise ContractError(f"unit {unit_id} missing from the dispatch at hour {hour}")
        row = snap.loc[unit_id]
        if rec["kind"] == "gen":
            i = gen_idx[unit_id]
            net.gen.at[i, "p_mw"] = float(row["p_mw"])
            net.gen.at[i, "in_service"] = bool(int(row["status"]))
        elif rec["kind"] == "res":
            i = sgen_idx[unit_id]
            net.sgen.at[i, "p_mw"] = float(row["p_mw"])
            net.sgen.at[i, "in_service"] = True   # energised whether curtailed or not
    for i in net.ext_grid.index:
        net.ext_grid.at[i, "in_service"] = True
    set_sc_params(net, registry, templates)


def fault_levels(net, dispatch, loads, hour, registry, templates, case="max") -> pd.DataFrame:
    """Validated fault-level table for ``hour``: bus, ikss_ka, sk_mva, case."""
    if case not in FAULT_CASES:
        raise ContractError(f"case must be one of {sorted(FAULT_CASES)}, got {case!r}")
    work = copy.deepcopy(net)
    apply_fault_state(work, dispatch, loads, hour, registry, templates)
    sc.calc_sc(work, fault="3ph", case=case)
    res = work.res_bus_sc.reindex(work.bus.index)
    out = pd.DataFrame({
        "bus": work.bus["name"].values,
        "ikss_ka": res["ikss_ka"].values,
        "sk_mva": res["skss_mw"].values,   # pandapower's name; the quantity is MVA
        "case": case,
    })
    return validate_fault_levels(out)
