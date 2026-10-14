"""The island steady state and short circuit at the island critical hours
(plan I4, I5 and I5b, run at I3b's hours).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

``island_static_study(campus, hourly, hours, island_unit_hourly)`` runs, at
each ``(period, hour)``:

* **I4** (``island_flow``): the island steady state after every genset with
  island data has joined, with its per-unit consistency gate
  (``check_gate``);
* **I5** (``island_fault_levels``, ``relay_check``) and **I5b**
  (``unbalanced_fault_levels``, ``earth_reference``,
  ``earth_fault_relay_check``) in two ``case``s:
  * ``islanding``: just after the PCC opens, with the gensets that run at
    the hour (I3a's ``online``, else the dispatch);
  * ``steady``: after every genset with island data has joined.

The island holds the units built in the hour's period (``status`` 1) and no
others (``frequency.built``). Each table has fixed columns (the
``*_COLUMNS`` tuples), header only where it has nothing, so the panel reads
one shape. ``summary`` names what failed: hours not viable or off the
gate, blind relays, earth references lost on islanding, faults that cannot
be determined.

A screening, as each module says; pandapower through I4, I5 and I5b.
"""
import dataclasses

import pandas as pd

from gridspine.static import island_flow as IF
from gridspine.static import island_sc as SC
from gridspine.static import island_sc_seq as SEQ
from gridspine.static.frequency import built, cold_gensets

CASES = ("islanding", "steady")
_KEY = ("period", "hour")
_CASE_KEY = ("period", "hour", "case")

FLOW_COLUMNS = (*_KEY, "viable", "reason", "df_hz", "delta_p_ac_mw", "sync_df_hz", "sync_dv_pu", "gate_ok",
                "gate_problems", "assumed")
FLOW_UNIT_COLUMNS = (*_KEY, "unit_id", "kind", "control", "role", "p0_mw", "p_mw", "q_mvar", "k_mw_per_hz",
                     "saturated", "loading_pct")
FLOW_BUS_COLUMNS = (*_KEY, "bus", "vm_pu", "in_band")
#: ``kind`` is ``trafo`` or ``cable``.
FLOW_BRANCH_COLUMNS = (*_KEY, "kind", "branch", "loading_pct")
SC_COLUMNS = (*_CASE_KEY, "bus", "ikss_max_ka", "ikss_min_ka", "ikss_min_n1_ka", "largest_out", "method_min",
              "assumed")
RELAY_COLUMNS = (*_CASE_KEY, "relay", "branch", "fault_bus", "timing", "relay_ka", "initial_ka", "fault_total_ka",
                 "bypass_ka", "pickup_ka", "s", "verdict", "detail", "grid_ikss_max_ka", "grid_to_island",
                 "largest_out", "relay_n1_ka", "assumed")
#: The relay current at each GFM current limit of ``i_max_sensitivity_pu``
#: (plan Q8), long.
RELAY_IMAX_COLUMNS = (*_CASE_KEY, "relay", "i_max_pu", "relay_ka")
UNBALANCED_COLUMNS = (*_CASE_KEY, "bus", "ik1_max_ka", "ik2_max_ka", "ik3_max_ka", "ik1_min_ka", "ik2_min_ka",
                      "ik3_min_ka", "earth_path", "earth_assumed", "ic_e_ka", "note", "method", "assumed")
EARTH_COLUMNS = (*_CASE_KEY, "bus", "earthed_grid", "earthed_island", "lost_on_islanding", "island_assumed",
                 "finding")
EARTH_RELAY_COLUMNS = (*_CASE_KEY, "relay", "branch", "fault_bus", "i_pickup_e_ka", "relay_e_ka", "fault_e_ka",
                       "grid_e_ka", "sensitivity", "verdict", "detail", "assumed")
SUMMARY_KEYS = ("label", "ledger", "models", "island_hours", "not_viable_hours", "gate_failing_hours",
                "blind_relay_hours", "blind_earth_relay_hours", "earth_lost_hours", "not_determinable_hours")
#: The model behind each table (the panel names it from this text).
MODELS = {
    "flow": "steady-state island load flow (pandapower), droop-weighted distributed slack, P and current limits "
            "in an outer loop (plan I4)",
    "sc": "IEC 60909 max case (synchronous machines behind x''d); converter-aware min case, not IEC 60909 (plan I5)",
    "relays": "the I5 min case through each relay's branch, against s x pickup (plan I5)",
    "unbalanced": "symmetrical components on the campus's own sequence networks, IEC 60909 equivalent source "
                  "(plan I5b)",
    "earth": "earth reference by zero-sequence path, island against grid-connected (plan I5b)",
    "earth_relays": "residual current 3 I0 through each relay's branch for a 1-phase fault, island min case "
                    "(plan I5b)",
}
for _name, _same in (("flow_units", "flow"), ("flow_bus", "flow"), ("flow_branch", "flow"),
                     ("relays_imax", "relays")):
    MODELS[_name] = MODELS[_same]
CASE_NOTE = ("island_hours: the islanding case is the instant the PCC opens with the gensets that run at the hour "
             "(the seamless and planned scenarios); under ups_bridged the bus is dead at 0+ and the gensets start "
             "cold, so only the steady case applies there")
#: The inputs each model reads, by unit field (``campus.params`` names). A
#: row's ``assumed`` names those of its island's units, branches and
#: requirements whose source is ``assumed``, and the defaults the model
#: drafts where an input is missing.
_FLOW_FIELDS = frozenset({"p_mw", "s_mva", "pf", "pf_rated", "cos_phi", "cosphi_p_points", "qu_points", "droop_pct",
                          "q_droop_pct", "i_max_pu", "cos_phi_r", "p_min_pu", "fw_k_mw_per_hz", "fw_deadband_hz",
                          "fw_p_limit_mw"})
_SC_FIELDS = frozenset({"s_mva", "k_sc", "rx_sc", "xd_pp", "xd_sat", "ik_sustained_pu", "i_max_pu", "k_gfm_min",
                        "cos_phi_r", "neg_seq"})
_SEQ_FIELDS = _SC_FIELDS | {"x2_pu", "x0_pu", "r_n_ohm"}
_LOAD_FIELDS = {"flow": frozenset({"pf"})}           # a load's draw is the hour's dispatch
_CABLE_ZERO = ("r0_ohm_per_km", "x0_ohm_per_km", "c0_nf_per_km")
#: Branch inputs by model: the flow reads them all; the fault levels the
#: series impedance only (magnetising neglected); the sequence networks also
#: the zero sequence and the cable capacitance (the unearthed zone's current).
_BRANCH_SC = frozenset({"length_km", "r_ohm_per_km", "x_ohm_per_km", "sn_mva", "vk_percent", "vkr_percent",
                        "vn_hv_kv", "vn_lv_kv"})
_BRANCH_SEQ = _BRANCH_SC | {"c_nf_per_km", *_CABLE_ZERO}
#: The grid's inputs to the grid-connected comparison of a relay.
_PCC_SC = frozenset({"sk_max_mva", "rx_max"})
_PCC_SEQ = _PCC_SC | {"x0x_max", "r0x0_max"}
LABEL = "island steady state and short circuit at the island hours (screening; I4, I5, I5b)"
BLIND = "protection blind in island"
NOT_DETERMINABLE = "not determinable"


@dataclasses.dataclass
class IslandStatic:
    """``island_static_study``'s tables, one per ``*_COLUMNS``."""
    flow: pd.DataFrame
    flow_units: pd.DataFrame
    flow_bus: pd.DataFrame
    flow_branch: pd.DataFrame
    sc: pd.DataFrame
    relays: pd.DataFrame
    relays_imax: pd.DataFrame
    unbalanced: pd.DataFrame
    earth: pd.DataFrame
    earth_relays: pd.DataFrame


TABLES = (("flow", FLOW_COLUMNS), ("flow_units", FLOW_UNIT_COLUMNS), ("flow_bus", FLOW_BUS_COLUMNS),
          ("flow_branch", FLOW_BRANCH_COLUMNS), ("sc", SC_COLUMNS), ("relays", RELAY_COLUMNS),
          ("relays_imax", RELAY_IMAX_COLUMNS), ("unbalanced", UNBALANCED_COLUMNS), ("earth", EARTH_COLUMNS),
          ("earth_relays", EARTH_RELAY_COLUMNS))


def island_cases(campus, rows, unit_rows=None) -> dict:
    """``{case: the units in the island}`` at one hour: ``steady`` every
    unit built in the period, ``islanding`` less the gensets that are not
    running (``frequency.cold_gensets``)."""
    steady = built(rows)
    return {"islanding": steady - cold_gensets(campus, rows, unit_rows), "steady": steady}


def _assumed_params(campus, elements, fields=None):
    p = campus.params
    sel = p[(p["source"] == "assumed") & p["element"].astype(str).isin({str(e) for e in elements})]
    if fields is not None:
        sel = sel[sel["param"].isin(fields)]
    return {f"{e}.{f}" for e, f in zip(sel["element"], sel["param"])}


def assumed_inputs(campus, online, model) -> list:
    """The assumed inputs a ``model`` (``flow``, ``sc``, ``relays``,
    ``unbalanced``, ``earth_relays``) rests on in an island of ``online``
    units, as ``ELEMENT.param`` (sorted). A relay's own pickup is added per
    row by the caller."""
    branches = set(campus.net.line["name"].astype(str)) | set(campus.net.trafo["name"].astype(str))
    kinds = campus.units["kind"]
    units = set(online) & set(kinds.index[kinds != "load"])
    loads = set(online) & set(kinds.index[kinds == "load"])
    seq = model in ("unbalanced", "earth_relays")
    fields = {"flow": _FLOW_FIELDS, "sc": _SC_FIELDS, "relays": _SC_FIELDS}.get(model, _SEQ_FIELDS)
    out = (_assumed_params(campus, units, fields) | _assumed_params(campus, loads, _LOAD_FIELDS.get(model, ()))
           | _assumed_params(campus, branches, None if model == "flow" else _BRANCH_SEQ if seq else _BRANCH_SC))
    if model in ("relays", "earth_relays"):
        pcc = campus.net.bus.at[int(campus.net.ext_grid["bus"].iloc[0]), "name"]
        out |= _assumed_params(campus, {"island"}, {"relay_sensitivity"})
        out |= _assumed_params(campus, {pcc}, _PCC_SEQ if seq else _PCC_SC)
    if model in ("unbalanced", "earth_relays"):
        given = set(zip(campus.params["element"].astype(str), campus.params["param"]))
        for uid in sorted(units & set(campus.units.index[campus.units["kind"] == "genset"])):
            values = (campus.island["units"].get(uid) or {}).get("values", {})
            out |= {f"{uid}.{f}" for f in ("x2_pu", "x0_pu") if f not in values}
        for cable in sorted(set(campus.net.line["name"].astype(str))):
            out |= {f"{cable}.{f}" for f in _CABLE_ZERO if (cable, f) not in given}
        for note in campus.assumptions:
            element = note.split(":", 1)[0]
            out.add(f"{element}.vector_group" if "vector_group" in note else f"{element}.neutral_earthing")
    return sorted(out)


def _tagged(df, key, cols):
    """``df``'s rows with ``key`` in front, in ``cols``."""
    if df is None or not len(df):
        return []
    return [{**key, **{c: r.get(c) for c in cols if c not in key}} for r in df.to_dict("records")]


def island_static_hour(campus, rows, unit_rows=None, key=None) -> dict:
    """``{table: [rows]}`` for one hour (see the module docstring); ``key``
    (``period``, ``hour``) goes in front of every row."""
    key = dict(key or {})
    cases = island_cases(campus, rows, unit_rows)
    out = {name: [] for name, _ in TABLES}
    res = IF.island_flow(campus, rows, online=cases["steady"])
    gate = IF.check_gate(res)
    out["flow"].append({**key, "viable": res.viable, "reason": res.reason, "df_hz": res.df_hz,
                        "delta_p_ac_mw": res.delta_p_ac_mw, "sync_df_hz": res.sync.get("df_hz"),
                        "sync_dv_pu": res.sync.get("dv_pu"), "gate_ok": gate["ok"],
                        "gate_problems": "; ".join(gate["problems"]),
                        "assumed": "; ".join(assumed_inputs(campus, cases["steady"], "flow"))})
    out["flow_units"] += _tagged(res.units, key, FLOW_UNIT_COLUMNS)
    out["flow_bus"] += _tagged(res.bus, key, FLOW_BUS_COLUMNS)
    out["flow_branch"] += _tagged(res.trafo.rename(columns={"trafo": "branch"}).assign(kind="trafo"), key,
                                  FLOW_BRANCH_COLUMNS)
    out["flow_branch"] += _tagged(res.line.rename(columns={"cable": "branch"}).assign(kind="cable"), key,
                                  FLOW_BRANCH_COLUMNS)
    for case in CASES:
        online = cases[case]
        ck = {**key, "case": case}
        out["sc"] += _tagged(SC.island_fault_levels(campus, online=online).assign(
            assumed="; ".join(assumed_inputs(campus, online, "sc"))), ck, SC_COLUMNS)
        relays = SC.relay_check(campus, online=online)
        out["relays"] += _tagged(_with_pickup(campus, relays, assumed_inputs(campus, online, "relays"),
                                              "i_pickup_ka"), ck, RELAY_COLUMNS)
        for r in relays.to_dict("records"):
            for col, v in r.items():
                if col.startswith("relay_ka_at_"):
                    out["relays_imax"].append({**ck, "relay": r["relay"], "i_max_pu": float(col[len("relay_ka_at_"):]),
                                               "relay_ka": v})
        out["unbalanced"] += _tagged(SEQ.unbalanced_fault_levels(campus, online=online).assign(
            assumed="; ".join(assumed_inputs(campus, online, "unbalanced"))), ck, UNBALANCED_COLUMNS)
        out["earth"] += _tagged(SEQ.earth_reference(campus, online=online), ck, EARTH_COLUMNS)
        out["earth_relays"] += _tagged(_with_pickup(campus, SEQ.earth_fault_relay_check(campus, online=online),
                                                    assumed_inputs(campus, online, "earth_relays"), "i_pickup_e_ka"),
                                       ck, EARTH_RELAY_COLUMNS)
    return out


def _with_pickup(campus, df, base, pickup):
    """``df`` with ``assumed``: ``base`` and the relay's own ``pickup`` when
    it is assumed."""
    if not len(df):
        return df
    return df.assign(assumed=["; ".join(sorted(set(base) | _assumed_params(campus, {r}, {pickup})))
                              for r in df["relay"]])


def island_static_study(campus, hourly, hours, island_unit_hourly=None) -> IslandStatic:
    """I4, I5 and I5b at ``hours``, ``[(period, hour)]``, from the campus
    hourly table and, when given, I3a's per-unit table (validated)."""
    from gridspine.schema.island import validate_island_unit_hourly

    iu = validate_island_unit_hourly(island_unit_hourly) if island_unit_hourly is not None else None
    acc = {name: [] for name, _ in TABLES}
    for period, hour in hours:
        sel = hourly[(hourly["period"] == period) & (hourly["hour"] == hour)]
        rows = sel[["unit_id", "p_mw", "status"]].reset_index(drop=True)
        urows = iu[(iu["period"] == period) & (iu["hour"] == hour)] if iu is not None else None
        for name, part in island_static_hour(campus, rows, urows, {"period": int(period), "hour": int(hour)}).items():
            acc[name] += part
    return IslandStatic(**{name: pd.DataFrame(acc[name], columns=list(cols)) for name, cols in TABLES})


def _pairs(df, cols, mask):
    if not len(df):
        return []
    sel = df[mask(df)]
    return sorted({tuple(int(v) if c in _KEY else str(v) for c, v in zip(cols, r)) for r in sel[list(cols)].values})


def summary(study: IslandStatic, hours) -> dict:
    """What failed, by hour (``SUMMARY_KEYS``); each entry a list.
    ``models`` maps each table to its model (``MODELS``); the driver keys it
    by file name."""
    f = study.flow
    return {
        "label": LABEL,
        "ledger": [*SC.LEDGER, *SEQ.LEDGER, CASE_NOTE],
        "models": dict(MODELS),
        "island_hours": [[int(p), int(h)] for p, h in hours],
        "not_viable_hours": [list(x) for x in _pairs(f, ("period", "hour", "reason"), lambda d: ~d["viable"].astype(bool))],
        "gate_failing_hours": [list(x) for x in _pairs(f, _KEY, lambda d: ~d["gate_ok"].astype(bool))],
        "blind_relay_hours": [list(x) for x in _pairs(study.relays, (*_CASE_KEY, "relay"),
                                                       lambda d: d["verdict"] == BLIND)],
        "blind_earth_relay_hours": [list(x) for x in _pairs(study.earth_relays, (*_CASE_KEY, "relay"),
                                                             lambda d: d["verdict"] == SEQ.BLIND_E)],
        "earth_lost_hours": [list(x) for x in _pairs(study.earth, (*_CASE_KEY, "bus"),
                                                      lambda d: d["lost_on_islanding"].astype(bool))],
        "not_determinable_hours": sorted(
            [list(x) for x in _pairs(study.earth_relays, (*_CASE_KEY, "relay"),
                                     lambda d: d["verdict"] == NOT_DETERMINABLE)]
            + [list(x) for x in _pairs(study.unbalanced, (*_CASE_KEY, "bus"),
                                       lambda d: d["note"].fillna("").str.startswith(NOT_DETERMINABLE))]),
    }
