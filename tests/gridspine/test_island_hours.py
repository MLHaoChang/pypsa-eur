"""I4, I5 and I5b at the island critical hours (plan 2026-10-07-campus-island-operation).

Oracles:
* the two cases: ``islanding`` holds the gensets running at the hour (I3a's
  ``online``), ``steady`` every built unit; a unit not built in the period
  is in neither;
* every table is the module's own function run by hand on that case's
  units, tagged with the hour and the case;
* the summary, on tables written by hand: each failure lands in its list;
* fixed columns, whatever ran;
* through the driver: the files, the island hours and the refusal.
"""
import json
import math

import pandas as pd
import pytest
import yaml

from gridspine.drivers import campus_study as D
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import island_flow as IF
from gridspine.static import island_hours as IH
from gridspine.static import island_sc as SC
from gridspine.static import island_sc_seq as SEQ
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_island_schema import requirements, t


def campus_spec(relays=True, island_req=True):
    s = spec()
    if relays:
        s["campus"]["relays"] = [{"name": "R1", "branch": "CB1", "fault_bus": "MV2", "timing": "time_delayed",
                                  "i_pickup_ka": t(0.5, "datasheet"), "i_pickup_e_ka": t(0.05, "datasheet")}]
    if island_req:
        req = requirements()
        req.update(scenarios=["seamless"], genset_redundancy_n=t(0))
        s["campus"]["island"] = req
    return s


def unit_table(gen_online=1.0, hours=(0,)):
    return pd.DataFrame([{"period": 2030, "hour": h, "unit_id": u, "soc_start_mwh": soc, "online": on,
                          "headroom_up_mw": 5.0, "headroom_down_mw": 5.0, "p_ups_it_mw": math.nan}
                         for h in hours for u, soc, on in (("GEN1", math.nan, gen_online), ("BESS1", 10.0, 1.0))])


def hourly(hours=(0,), **kw):
    return pd.concat([rows(**kw).assign(period=2030, hour=h) for h in hours], ignore_index=True)


KEY = {"period": 2030, "hour": 0}


def tagged(df, cols, **key):
    return pd.DataFrame([{**KEY, **key, **{c: r.get(c) for c in cols if c not in ("period", "hour", "case")}}
                         for r in df.to_dict("records")], columns=list(cols))


def test_the_islanding_case_holds_the_running_gensets_and_steady_every_built_unit():
    camp = build_campus(campus_spec())
    r = rows()
    assert IH.island_cases(camp, r, unit_table(gen_online=0.0)) == {
        "islanding": {"DC_LOAD", "BESS1", "PV1"}, "steady": {"DC_LOAD", "BESS1", "PV1", "GEN1"}}
    assert IH.island_cases(camp, r, unit_table(gen_online=1.0))["islanding"] == {"DC_LOAD", "BESS1", "PV1", "GEN1"}
    # without I3a, the dispatch says whether the genset runs; a unit not built in the period is in neither
    idle = r.assign(p_mw=[-25.0, 0.0, 10.0, 0.0], status=[1, 0, 1, 1])
    assert IH.island_cases(camp, idle) == {"islanding": {"DC_LOAD", "PV1"}, "steady": {"DC_LOAD", "PV1", "GEN1"}}


def test_the_flow_is_i4s_after_the_gensets_join_on_the_built_units():
    camp = build_campus(campus_spec())
    st = IH.island_static_study(camp, hourly(), [(2030, 0)], unit_table(gen_online=0.0))
    res = IF.island_flow(camp, rows(), online={"DC_LOAD", "BESS1", "PV1", "GEN1"})
    f = st.flow.iloc[0]
    assert (f["viable"], f["df_hz"], f["delta_p_ac_mw"]) == (res.viable, res.df_hz, res.delta_p_ac_mw)
    assert (f["sync_df_hz"], f["sync_dv_pu"]) == (res.sync["df_hz"], res.sync["dv_pu"])
    assert f["gate_ok"] and f["gate_problems"] == ""
    pd.testing.assert_frame_equal(st.flow_units, tagged(res.units, IH.FLOW_UNIT_COLUMNS))
    pd.testing.assert_frame_equal(st.flow_bus, tagged(res.bus, IH.FLOW_BUS_COLUMNS))
    branch = st.flow_branch.set_index(["kind", "branch"])["loading_pct"]
    assert branch.loc[("cable", "CB1")] == res.line.set_index("cable").at["CB1", "loading_pct"]
    assert branch.loc[("trafo", "TR2")] == res.trafo.set_index("trafo").at["TR2", "loading_pct"]


def test_a_unit_not_built_is_not_in_the_island_flow():
    camp = build_campus(campus_spec())
    h = hourly()
    h.loc[h["unit_id"] == "GEN1", ["p_mw", "status"]] = [0.0, 0]
    st = IH.island_static_study(camp, h, [(2030, 0)])
    assert st.flow_units.set_index("unit_id").at["GEN1", "role"] == "offline"


@pytest.mark.parametrize("case, online", [("islanding", {"DC_LOAD", "BESS1", "PV1"}),
                                          ("steady", {"DC_LOAD", "BESS1", "PV1", "GEN1"})])
def test_each_short_circuit_table_is_its_function_on_that_cases_units(case, online):
    camp = build_campus(campus_spec())
    st = IH.island_static_study(camp, hourly(), [(2030, 0)], unit_table(gen_online=0.0))

    def mine(df):                                      # its ``assumed`` is tested on its own below
        out = df[df["case"] == case].reset_index(drop=True)
        return out.assign(assumed=None) if "assumed" in out else out

    pd.testing.assert_frame_equal(mine(st.sc), tagged(SC.island_fault_levels(camp, online=online), IH.SC_COLUMNS,
                                                       case=case))
    relays = SC.relay_check(camp, online=online)
    pd.testing.assert_frame_equal(mine(st.relays), tagged(relays, IH.RELAY_COLUMNS, case=case))
    imax = mine(st.relays_imax).set_index("i_max_pu")["relay_ka"]
    assert list(imax.index) == [1.2, 1.5, 2.0]
    for v in (1.2, 1.5, 2.0):
        assert imax.loc[v] == relays.iloc[0][f"relay_ka_at_{v:g}"]
    pd.testing.assert_frame_equal(mine(st.unbalanced), tagged(SEQ.unbalanced_fault_levels(camp, online=online),
                                                               IH.UNBALANCED_COLUMNS, case=case))
    pd.testing.assert_frame_equal(mine(st.earth), tagged(SEQ.earth_reference(camp, online=online), IH.EARTH_COLUMNS,
                                                          case=case))
    pd.testing.assert_frame_equal(mine(st.earth_relays), tagged(SEQ.earth_fault_relay_check(camp, online=online),
                                                                 IH.EARTH_RELAY_COLUMNS, case=case))


def test_the_genset_raises_the_steady_fault_level_above_the_islanding_one():
    camp = build_campus(campus_spec())
    sc = IH.island_static_study(camp, hourly(), [(2030, 0)], unit_table(gen_online=0.0)).sc
    by = sc.set_index(["case", "bus"])["ikss_max_ka"]
    assert by.loc[("steady", "MV2")] > by.loc[("islanding", "MV2")]


def test_every_table_carries_its_fixed_columns_even_empty():
    camp = build_campus(campus_spec(relays=False, island_req=False))
    st = IH.island_static_study(camp, hourly(), [(2030, 0)])
    for name, cols in IH.TABLES:
        assert tuple(getattr(st, name).columns) == cols, name
    assert st.relays.empty and st.relays_imax.empty and st.earth_relays.empty
    empty = IH.island_static_study(camp, hourly(), [])
    assert all(getattr(empty, name).empty and tuple(getattr(empty, name).columns) == cols for name, cols in IH.TABLES)


def frames(**over):
    """An ``IslandStatic`` written by hand: one failure of each kind."""
    base = {name: pd.DataFrame(columns=list(cols)) for name, cols in IH.TABLES}
    base["flow"] = pd.DataFrame([{**KEY, "viable": True, "gate_ok": True},
                                 {"period": 2030, "hour": 5, "viable": False, "reason": "no reference",
                                  "gate_ok": False}], columns=list(IH.FLOW_COLUMNS))
    base["relays"] = pd.DataFrame([{**KEY, "case": "islanding", "relay": "R1", "verdict": IH.BLIND},
                                   {**KEY, "case": "steady", "relay": "R1", "verdict": "sees the fault"}],
                                  columns=list(IH.RELAY_COLUMNS))
    base["earth_relays"] = pd.DataFrame([{**KEY, "case": "steady", "relay": "RE", "verdict": SEQ.BLIND_E},
                                         {**KEY, "case": "steady", "relay": "RX", "verdict": IH.NOT_DETERMINABLE},
                                         {**KEY, "case": "islanding", "relay": "RE", "verdict": SEQ.SEES_E}],
                                        columns=list(IH.EARTH_RELAY_COLUMNS))
    base["earth"] = pd.DataFrame([{**KEY, "case": "islanding", "bus": "PCC", "lost_on_islanding": True},
                                  {**KEY, "case": "islanding", "bus": "MV1", "lost_on_islanding": False}],
                                 columns=list(IH.EARTH_COLUMNS))
    base["unbalanced"] = pd.DataFrame([{**KEY, "case": "islanding", "bus": "MV1", "note": SEQ.NO_NEG},
                                       {**KEY, "case": "islanding", "bus": "MV2", "note": SEQ.NO_EARTH},
                                       {**KEY, "case": "steady", "bus": "MV2", "note": math.nan}],
                                      columns=list(IH.UNBALANCED_COLUMNS))
    base.update(over)
    return IH.IslandStatic(**base)


def test_the_summary_lands_each_failure_in_its_list():
    s = IH.summary(frames(), [(2030, 0), (2030, 5)])
    assert tuple(s) == IH.SUMMARY_KEYS
    assert s["island_hours"] == [[2030, 0], [2030, 5]]
    assert s["not_viable_hours"] == [[2030, 5, "no reference"]]
    assert s["gate_failing_hours"] == [[2030, 5]]
    assert s["blind_relay_hours"] == [[2030, 0, "islanding", "R1"]]
    assert s["blind_earth_relay_hours"] == [[2030, 0, "steady", "RE"]]
    assert s["earth_lost_hours"] == [[2030, 0, "islanding", "PCC"]]
    assert s["not_determinable_hours"] == [[2030, 0, "islanding", "MV1"], [2030, 0, "steady", "RX"]]
    assert s["label"] == IH.LABEL and s["ledger"] == [*SC.LEDGER, *SEQ.LEDGER, IH.CASE_NOTE]


def test_an_empty_study_summarises_to_empty_lists():
    s = IH.summary(frames(**{name: pd.DataFrame(columns=list(cols)) for name, cols in IH.TABLES}), [])
    assert all(s[k] == [] for k in IH.SUMMARY_KEYS if k not in ("label", "ledger", "models"))


# ── through the driver ───────────────────────────────────────────────────────

def write_run(run, s, selected=((0, "max_island_dp_up"), (1, "max_import")), island_tables=True, gen_online=0.0):
    from gridspine.schema.island import ISLAND_HOURLY_CSV, ISLAND_UNIT_HOURLY_CSV

    run.mkdir(parents=True, exist_ok=True)
    (run / D.CAMPUS_YAML).write_text(yaml.safe_dump(s, sort_keys=False))
    h = hourly(hours=(0, 1))
    h[["unit_id", "period", "hour", "p_mw", "status"]].to_csv(run / "campus_hourly.csv", index=False)
    pd.DataFrame({"period": 2030, "hour": [0, 1], "weight": 1.0, "import_mw": 10.0}).to_csv(
        run / "campus_pcc.csv", index=False)
    if island_tables:
        unit_table(gen_online, hours=(0, 1)).to_csv(run / ISLAND_UNIT_HOURLY_CSV, index=False)
        pd.DataFrame({"period": 2030, "hour": [0, 1], "scenario": "seamless", "dp_up_mw": 10.0, "dp_down_mw": -1.0,
                      "inertia_mws": 1.0, "k_mw_per_hz": 1.0, "critical_load_mw": 1.0}).to_csv(
            run / ISLAND_HOURLY_CSV, index=False)
    pd.DataFrame([{"period": 2030, "hour": h, "reasons": r} for h, r in selected]).to_csv(
        run / D.SELECTED_CSV, index=False)
    return run


def test_the_driver_writes_every_table_and_the_summary_at_the_island_hours(tmp_path):
    run = write_run(tmp_path / "run", campus_spec())
    summary = D.island_static_campus(run)
    for name, cols in IH.TABLES:
        df = pd.read_csv(run / D.ISLAND_STATIC_CSV[name])
        assert tuple(df.columns) == cols, name
    assert set(pd.read_csv(run / D.ISLAND_STATIC_CSV["flow"])["hour"]) == {0}     # hour 1: a base criterion only
    assert json.loads((run / D.ISLAND_STATIC_SUMMARY_JSON).read_text()) == summary
    assert summary["island_hours"] == [[2030, 0]] and [2030, 0, "islanding", "R1"] in summary["blind_relay_hours"]
    assert [2030, 0, "steady", "PCC"] in summary["earth_lost_hours"]


def test_the_driver_takes_the_running_gensets_from_i3a(tmp_path):
    off = D.island_static_campus(write_run(tmp_path / "off", campus_spec(), gen_online=0.0))
    on = D.island_static_campus(write_run(tmp_path / "on", campus_spec(), gen_online=1.0))
    sc_off = pd.read_csv(tmp_path / "off" / D.ISLAND_STATIC_CSV["sc"]).set_index(["case", "bus"])["ikss_max_ka"]
    sc_on = pd.read_csv(tmp_path / "on" / D.ISLAND_STATIC_CSV["sc"]).set_index(["case", "bus"])["ikss_max_ka"]
    assert sc_off.loc[("islanding", "MV2")] < sc_on.loc[("islanding", "MV2")] == sc_on.loc[("steady", "MV2")]
    assert off["island_hours"] == on["island_hours"]


def test_without_i3a_tables_the_dispatch_says_which_gensets_run(tmp_path):
    run = write_run(tmp_path / "run", campus_spec(), island_tables=False)
    D.island_static_campus(run)
    sc = pd.read_csv(run / D.ISLAND_STATIC_CSV["sc"]).set_index(["case", "bus"])["ikss_max_ka"]
    assert sc.loc[("islanding", "MV2")] == sc.loc[("steady", "MV2")]          # GEN1 runs at 5 MW


def test_the_driver_refuses_a_campus_without_island_data(tmp_path):
    s = campus_spec(island_req=False)
    for u in s["campus"]["units"].values():
        u.pop("island", None)
    with pytest.raises(ContractError, match="no island data"):
        D.island_static_campus(write_run(tmp_path / "run", s))


# ── what each row rests on (the I9 report's assumed marking) ─────────────────

ALL = {"DC_LOAD", "BESS1", "PV1", "GEN1"}


def test_a_given_input_is_not_assumed_and_a_unit_outside_the_island_adds_nothing():
    s = campus_spec()
    s["campus"]["units"]["GEN1"]["xd_pp"] = t(0.15, "datasheet")
    camp = build_campus(s)
    sc = IH.assumed_inputs(camp, ALL, "sc")
    assert "GEN1.xd_pp" not in sc and "GEN1.xd_sat" in sc and "BESS1.k_gfm_min" in sc
    assert not [x for x in IH.assumed_inputs(camp, ALL - {"GEN1"}, "sc") if x.startswith("GEN1.")]


def test_each_model_names_only_the_inputs_it_reads():
    camp = build_campus(campus_spec())
    flow, sc = IH.assumed_inputs(camp, ALL, "flow"), IH.assumed_inputs(camp, ALL, "sc")
    seq = IH.assumed_inputs(camp, ALL, "unbalanced")
    # a load's draw is the dispatch: only its power factor; nothing in the fault levels
    assert [x for x in flow if x.startswith("DC_LOAD.")] == ["DC_LOAD.pf"]
    assert not [x for x in sc if x.startswith("DC_LOAD.")]
    # the flow reads the droops and the cable rating, the fault levels neither; dynamics nowhere
    assert {"BESS1.droop_pct", "GEN1.droop_pct", "CB1.max_i_ka", "TR1.pfe_kw"} <= set(flow)
    assert not {"BESS1.droop_pct", "CB1.max_i_ka", "TR1.pfe_kw", "CB1.c_nf_per_km"} & set(sc)
    assert not {"GEN1.H_s", "GEN1.T_g_s", "BESS1.tau_f_s"} & set(flow + sc + seq)
    assert "BESS1.k_gfm_min" not in flow and {"BESS1.k_gfm_min", "GEN1.xd_pp"} <= set(sc)
    # the sequence networks add the cable capacitance, the drafted zero sequence and the assumed windings
    assert {"CB1.c_nf_per_km", "CB1.r0_ohm_per_km", "GEN1.x2_pu", "GEN1.x0_pu", "TR1.vector_group",
            "TR2.neutral_earthing"} <= set(seq) and set(sc) < set(seq)
    # the relays add the sensitivity and the grid's max case; the earth-fault relays the grid's zero sequence too
    relays, erelays = IH.assumed_inputs(camp, ALL, "relays"), IH.assumed_inputs(camp, ALL, "earth_relays")
    assert set(relays) - set(sc) == {"island.relay_sensitivity", "PCC.rx_max"}
    assert set(erelays) - set(seq) == {"island.relay_sensitivity", "PCC.rx_max"}


def test_given_sequence_data_is_not_drafted():
    s = campus_spec()
    s["campus"]["units"]["GEN1"]["island"].update(x2_pu=t(0.15, "datasheet"), x0_pu=t(0.06, "datasheet"))
    s["campus"]["transformers"]["TR1"]["vector_group"] = "Dyn11"
    s["campus"]["transformers"]["TR1"]["earthing_lv"] = "solid"
    seq = IH.assumed_inputs(build_campus(s), ALL, "unbalanced")
    assert not {"GEN1.x2_pu", "GEN1.x0_pu", "TR1.vector_group", "TR1.neutral_earthing"} & set(seq)
    assert {"TR2.vector_group", "TR2.neutral_earthing"} <= set(seq)


def test_the_tables_carry_the_assumed_inputs_and_a_relays_own_assumed_pickup():
    s = campus_spec()
    s["campus"]["relays"][0]["i_pickup_ka"] = t(0.5)                     # assumed; the earth pickup is not
    camp = build_campus(s)
    st = IH.island_static_study(camp, hourly(), [(2030, 0)], unit_table(gen_online=0.0))
    isl = ALL - {"GEN1"}
    assert st.flow.iloc[0]["assumed"] == "; ".join(IH.assumed_inputs(camp, ALL, "flow"))
    by_case = st.sc.groupby("case")["assumed"].unique()
    assert list(by_case["islanding"]) == ["; ".join(IH.assumed_inputs(camp, isl, "sc"))]
    assert list(by_case["steady"]) == ["; ".join(IH.assumed_inputs(camp, ALL, "sc"))]
    relay = st.relays.set_index("case").loc["islanding", "assumed"].split("; ")
    assert relay == sorted(IH.assumed_inputs(camp, isl, "relays") + ["R1.i_pickup_ka"])
    erelay = st.earth_relays.set_index("case").loc["steady", "assumed"].split("; ")
    assert erelay == IH.assumed_inputs(camp, ALL, "earth_relays")
    assert set(st.unbalanced[st.unbalanced["case"] == "steady"]["assumed"]) == {
        "; ".join(IH.assumed_inputs(camp, ALL, "unbalanced"))}


def test_the_gate_problems_are_joined_into_the_flow_row(monkeypatch):
    monkeypatch.setattr(IF, "check_gate", lambda res: {"ok": False, "problems": ["BESS1: off", "GEN1: off"]})
    st = IH.island_static_study(build_campus(campus_spec()), hourly(), [(2030, 0)])
    f = st.flow.iloc[0]
    assert not f["gate_ok"] and f["gate_problems"] == "BESS1: off; GEN1: off"
    assert IH.summary(st, [(2030, 0)])["gate_failing_hours"] == [[2030, 0]]


def test_the_summary_names_each_tables_model_and_the_case_rule():
    s = IH.summary(frames(), [])
    assert set(s["models"]) == {name for name, _ in IH.TABLES} and s["models"] == IH.MODELS
    assert IH.CASE_NOTE in s["ledger"] and "ups_bridged" in IH.CASE_NOTE


def test_the_driver_keys_the_models_by_file_name(tmp_path):
    summary = D.island_static_campus(write_run(tmp_path / "run", campus_spec()))
    assert tuple(summary) == IH.SUMMARY_KEYS
    assert summary["models"] == {D.ISLAND_STATIC_CSV[name]: text for name, text in IH.MODELS.items()}
    assert set(summary["models"]) == set(D.ISLAND_STATIC_CSV.values())
