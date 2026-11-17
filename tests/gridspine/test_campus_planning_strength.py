"""
GC-03: the planning-criteria screen and SCR/ESCR/WSCR in the campus run (TRGC 15).

Strength values are checked against a hand calculation (IEC 60909 minimum case,
network transformers with K_T) on a small hand-built campus; the shared hub
fixture checks the run writes both CSVs and fails closed.
"""

import math
from types import SimpleNamespace

import pandas as pd
import pytest

from gridspine.drivers import campus_study
from gridspine.drivers.campus_study import (
    LF_PCC_CSV,
    LV_C_NOTE,
    NO_EMERGENCY_NOTE,
    PLANNING_CSV,
    STRENGTH_CSV,
    STRENGTH_GROUPS_CSV,
    _gc03_case_verdict,
    _gc03_hour_net,
    _gc03_hours,
    _gc03_source,
    _gc03_strength,
    _vs_period,
    campus_tables,
    draft_from_project,
    load_run_campus,
    planning_campus,
    prepare_campus,
    rank_campus,
    size_campus,
    strength_campus,
)
from gridspine.ingest.campus import build_campus
from gridspine.ingest.contingency_list import read_contingency_list
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_sc import campus_fault_levels
from tests.gridspine.test_campus_hourly import solved_hub

# --------------------------------------------------------------------------
# the hand-built campus: 110 kV PCC, two 20 kV PoCs
# --------------------------------------------------------------------------

SK_GRID = 1000.0  # MVA, S_k''min at the PCC, R/X 0
SN, VK = 50.0, 0.10  # each transformer: 50 MVA, 10 %
P_FC, P_PV, P_WT = 4.0, 50.0, 20.0
Q_CAP = 10.0  # Mvar at MV1


def _v(x):
    return {"value": x, "source": "assumed"}


def _trafo(hv, lv, kv=20.0, sn=SN, vk=VK * 100):
    return {
        "hv_bus": hv,
        "lv_bus": lv,
        "sn_mva": _v(sn),
        "vn_hv_kv": _v(110.0 if kv == 20.0 else 20.0),
        "vn_lv_kv": _v(kv),
        "vk_percent": _v(vk),
        "vkr_percent": _v(0.001),
        "pfe_kw": _v(0.0),
        "i0_percent": _v(0.0),
    }


def _ibr(kind, bus, p, k=1.2):
    return {
        "kind": kind,
        "bus": bus,
        "p_mw": _v(p),
        "s_mva": _v(p / 0.95),
        "k_sc": _v(k),
        "rx_sc": _v(0.1),
    }


def _spec(wt_k=1.2, caps=(("CAP1", "MV1"),), extra_bus=False):
    buses = {"MV1": {"vn_kv": 20.0}, "MV2": {"vn_kv": 20.0}}
    trafos = {
        "T1A": _trafo("GRID", "MV1"),
        "T1B": _trafo("GRID", "MV1"),
        "T2": _trafo("GRID", "MV2"),
    }
    units = {
        "FC": _ibr("fuel_cell", "GRID", P_FC),
        "PV": _ibr("pv", "MV1", P_PV),
        "WT": _ibr("wind", "MV2", P_WT, k=wt_k),
        "L1": {"kind": "load", "bus": "MV1", "p_mw": _v(10.0), "pf": _v(0.95)},
    }
    if extra_bus:
        buses["MV3"] = {"vn_kv": 20.0}
        trafos["T3"] = _trafo("GRID", "MV3")
        units["L3"] = {"kind": "load", "bus": "MV3", "p_mw": _v(5.0), "pf": _v(0.95)}
    return {
        "campus": {
            "name": "HAND",
            "f_hz": 50,
            "pcc": {
                "bus": "GRID",
                "vn_kv": 110.0,
                "vm_pu": _v(1.0),
                "sk_max_mva": _v(SK_GRID),
                "sk_min_mva": _v(SK_GRID),
                "rx_max": _v(0.0),
                "rx_min": _v(0.0),
            },
            "buses": buses,
            "transformers": trafos,
            "units": units,
            "compensation": [
                {
                    "name": n,
                    "kind": "capacitor_bank",
                    "bus": b,
                    "q_mvar": _v(Q_CAP),
                    "steps": 1,
                }
                for n, b in caps
            ],
        }
    }


def _hourly(campus):
    return pd.DataFrame(
        [
            {
                "unit_id": u,
                "period": 2030,
                "hour": 0,
                "p_mw": -5.0 if campus.units.at[u, "kind"] == "load" else 1.0,
                "status": 1,
            }
            for u in campus.units.index
        ]
    )


def _strength(spec):
    c = build_campus(spec)
    return _gc03_strength(c, _hourly(c))


def _at(table, poc):
    return table.set_index("poc").loc[poc]


# Hand calculation, per-MVA impedances (z = 1/S): the grid is 1/S_k at the PCC;
# a network transformer is K_T * vk / S_n with K_T = 0.95 c_max / (1 + 0.6 x_T)
# (IEC 60909-0 6.3.3, c_max = 1.1 above 1 kV); c_min = 1.0 above 1 kV (Table 1).
K_T = 0.95 * 1.1 / (1 + 0.6 * VK)
Z_G, Z_T = 1 / SK_GRID, K_T * VK / SN
SK_MV1 = 1 / (Z_G + Z_T / 2)  # two parallel transformers: 503.5629 MVA
SK_MV2 = 1 / (Z_G + Z_T)  # one transformer: 336.5079 MVA
SK_MV1_WORST = SK_MV2  # one of MV1's two transformers lost


def test_strength_matches_the_hand_calculation():
    per_poc, groups, method, _ = _strength(_spec())
    assert method == "full N-1"
    assert SK_MV1 == pytest.approx(503.5629, rel=1e-6) and SK_MV2 == pytest.approx(
        336.5079, rel=1e-6
    )
    mv1, mv2 = _at(per_poc, "MV1"), _at(per_poc, "MV2")
    assert mv1["sk_intact_mva"] == pytest.approx(SK_MV1, rel=1e-4)
    assert mv1["scr_intact"] == pytest.approx(SK_MV1 / P_PV, rel=1e-4)  # 10.0713
    assert mv1["escr_intact"] == pytest.approx(
        (SK_MV1 - Q_CAP) / P_PV, rel=1e-4
    )  # 9.8713
    assert mv1["scr_worst"] == pytest.approx(SK_MV1_WORST / P_PV, rel=1e-4)  # 6.7302
    assert mv2["scr_intact"] == pytest.approx(SK_MV2 / P_WT, rel=1e-4)  # 16.8254
    wscr = (SK_GRID * P_FC + SK_MV1 * P_PV + SK_MV2 * P_WT) / (P_FC + P_PV + P_WT) ** 2
    assert groups.iloc[0]["wscr_intact"] == pytest.approx(wscr, rel=1e-4)  # 6.5574


def test_worst_outage_scr_is_never_above_intact():
    spec = _spec()
    spec["campus"]["transformers"]["T2B"] = _trafo("GRID", "MV2")  # no PoC islands
    per_poc, _, _, _ = _strength(spec)
    assert set(per_poc["poc"]) == {"GRID", "MV1", "MV2"}
    assert per_poc["scr_worst"].notna().all()
    assert (per_poc["scr_worst"] <= per_poc["scr_intact"] + 1e-9).all()
    for poc, p in (("MV1", P_PV), ("MV2", P_WT)):  # each PoC keeps one transformer
        assert _at(per_poc, poc)["scr_worst"] == pytest.approx(SK_MV2 / p, rel=1e-4)
        assert _at(per_poc, poc)["scr_worst"] < _at(per_poc, poc)["scr_intact"]


def test_the_fault_state_does_not_depend_on_the_hour():
    c = build_campus(_spec())
    one = _hourly(c)
    # an earlier first hour with ten times the load and every converter idle
    first = one.assign(status=0, p_mw=one["p_mw"] * 10)
    two = pd.concat([first, one.assign(hour=1)], ignore_index=True)
    a, b = _gc03_strength(c, one)[0], _gc03_strength(c, two)[0]
    pd.testing.assert_series_equal(a["sk_intact_mva"], b["sk_intact_mva"])
    pd.testing.assert_series_equal(a["sk_worst_mva"], b["sk_worst_mva"])


def test_scr_at_the_pcc_is_the_entered_fault_level_over_rated_power():
    grid = _at(_strength(_spec())[0], "GRID")
    assert grid["sk_intact_mva"] == pytest.approx(SK_GRID, rel=1e-9)
    assert grid["scr_intact"] == pytest.approx(
        SK_GRID / P_FC, rel=1e-9
    )  # 250, rated P, not the hour's 1 MW


def test_another_poc_s_converter_does_not_feed_the_fault_level():
    base = _at(_strength(_spec())[0], "MV1")["sk_intact_mva"]
    assert _at(_strength(_spec(wt_k=5.0))[0], "MV1")["sk_intact_mva"] == pytest.approx(
        base, rel=1e-9
    )


def test_fault_level_agrees_with_the_campus_short_circuit_engine():
    c = build_campus(_spec())
    per_poc = _gc03_strength(c, _hourly(c))[0]
    sc = campus_fault_levels(c, set()).set_index("bus")
    for bus in ("GRID", "MV1", "MV2"):
        sk = (
            math.sqrt(3) * float(sc.at[bus, "vn_kv"]) * float(sc.at[bus, "ikss_min_ka"])
        )
        assert _at(per_poc, bus)["sk_intact_mva"] == pytest.approx(sk, rel=5e-3)


def test_a_poc_below_1_kv_carries_c_min_and_says_so():
    spec = _spec(caps=())
    spec["campus"]["buses"]["LV"] = {"vn_kv": 0.4}
    spec["campus"]["transformers"]["TL"] = _trafo(
        "MV2", "LV", kv=0.4, sn=1000.0, vk=1.0
    )
    spec["campus"]["units"]["PVL"] = _ibr("pv", "LV", 1.0)
    per_poc = _strength(spec)[0]
    lv, mv2 = _at(per_poc, "LV"), _at(per_poc, "MV2")
    # a near-ideal transformer (1 % on 1000 MVA): the LV fault level is c_min = 0.95 over MV2's impedance
    z_tl = 0.01 / 1000.0 * 0.95 * 1.1 / (1 + 0.6 * 0.01)
    assert lv["sk_intact_mva"] == pytest.approx(0.95 / (1 / SK_MV2 + z_tl), rel=5e-4)
    assert str(lv["note"]).count(LV_C_NOTE) == 1
    assert LV_C_NOTE not in str(mv2["note"])


def test_a_bank_off_every_poc_is_subtracted_at_every_poc_and_named():
    per_poc, _, _, notes = _strength(
        _spec(caps=(("CAP1", "MV1"), ("CAP3", "MV3")), extra_bus=True)
    )
    assert _at(per_poc, "MV1")["q_c_mvar"] == pytest.approx(2 * Q_CAP)
    assert _at(per_poc, "MV2")["q_c_mvar"] == pytest.approx(Q_CAP)
    assert _at(per_poc, "MV2")["escr_intact"] == pytest.approx(
        (SK_MV2 - Q_CAP) / P_WT, rel=1e-4
    )
    assert sum("CAP3" in n and "subtracted at every PoC" in n for n in notes) == 1


def test_load_q_reaches_the_net_and_the_tables():
    c = build_campus(_spec())
    net, dispatch, loads, _ = _gc03_hour_net(
        c, _hourly(c)[["unit_id", "p_mw", "status"]], 0
    )
    q = 5.0 * math.tan(math.acos(0.95))
    assert float(net.load["q_mvar"].sum()) == pytest.approx(q)
    assert loads.set_index("bus").at["MV1", "q_mvar"] == pytest.approx(q)


# --------------------------------------------------------------------------
# the campus run on the shared hub fixture
# --------------------------------------------------------------------------


@pytest.fixture
def sized(tmp_path):
    path = tmp_path / "network.nc"
    solved_hub(periods=[2030, 2040]).export_to_netcdf(str(path))
    run = tmp_path / "run"
    prepare_campus(run, draft_from_project(path).spec, path)
    rank_campus(run, k=1)
    size_campus(run)
    return run


def test_the_run_writes_both_csvs_and_islanding_is_never_skipped(sized):
    summary = strength_campus(sized)
    assert summary["basis"] == "static screen; binding answer is the RMS/EMT study"
    per_poc = pd.read_csv(sized / STRENGTH_CSV)
    assert (sized / STRENGTH_GROUPS_CSV).exists()
    assert set(per_poc["stage"].astype(str)) == {"2030", "2040"}
    # one transformer: its loss islands the PoC, reported and not evaluated, never dropped
    assert per_poc["islanding_outages"].str.contains("GRID-MV-1").all()
    assert per_poc["scr_worst"].isna().all() and (per_poc["scr_intact"] > 0).all()
    # rated P of the converters built: PV in 2030, PV + BESS in 2040
    assert per_poc.set_index("stage")["p_mw"].to_dict() == {2030: 20.0, 2040: 30.0}

    planning = planning_campus(sized)
    table = pd.read_csv(sized / PLANNING_CSV)
    assert set(table["hour_kind"]) == {"max_import", "max_export"}
    trafo = table[table["contingency_id"] == "GRID-MV-1"]
    assert len(trafo) and (trafo["verdict"] == "fail").all() and trafo["islanded"].all()
    assert planning["verdict"] == "fail" and planning["failures"] == len(trafo)
    assert planning["notes"].count(NO_EMERGENCY_NOTE) == 1
    # H5: and once in every row note
    assert (table["note"].fillna("").str.count(NO_EMERGENCY_NOTE) == 1).all()


def test_the_import_hour_is_the_voltage_stability_pick(sized):
    lf = {"pcc": pd.read_csv(sized / LF_PCC_CSV)}
    hours = _gc03_hours(sized, [2030, 2040])
    for period, picks in hours.items():
        assert (
            dict(picks)["max_import"]
            == _vs_period(
                {
                    **lf,
                    "bus": pd.DataFrame(
                        columns=["period", "case", "bus", "hour", "vm_pu"]
                    ),
                },
                "GRID",
                period,
                1e9,
            )[-1]
        )


def test_an_empty_set_is_not_evaluated_never_pass(sized):
    empty = pd.DataFrame(columns=["contingency_id", "kind", "element_ids", "order"])
    assert planning_campus(sized, contingencies=empty)["verdict"] == "not_evaluated"


def test_an_imported_list_with_an_excluded_contingency_never_reads_pass(sized):
    net = load_run_campus(sized).net
    text = (
        "CONTINGENCY PV_OUT\n REMOVE MACHINE 1 FROM BUS 2\nEND\n"
        "CONTINGENCY GHOST\n OPEN BRANCH FROM BUS 1 TO BUS 2 CKT 9\nEND\nEND\n"
    )
    imported = read_contingency_list(text, net, fmt="con")
    assert imported.excluded == ["GHOST"]
    summary = planning_campus(sized, contingencies=imported)
    table = pd.read_csv(sized / PLANNING_CSV)
    assert (
        set(table["contingency_id"]) == {"PV_OUT"}
        and (table["verdict"] == "pass").all()
    )
    assert summary["verdict"] == "incomplete" and summary["excluded"] == ["GHOST"]


@pytest.mark.parametrize(
    "row, verdict",
    [
        (
            {"stage2_verdict": "pass", "angle_verdict": "pass", "load_lost_mw": 0.0},
            "pass",
        ),
        (
            {
                "stage2_verdict": "not_solved",
                "angle_verdict": "not_solved",
                "load_lost_mw": 0.0,
            },
            "fail",
        ),
        (
            {"stage2_verdict": "fail", "angle_verdict": "pass", "load_lost_mw": 0.0},
            "fail",
        ),
        (
            {"stage2_verdict": "pass", "angle_verdict": "fail", "load_lost_mw": 0.0},
            "fail",
        ),
        (
            {"stage2_verdict": "pass", "angle_verdict": "unknown", "load_lost_mw": 0.5},
            "fail",
        ),
        (
            {"stage2_verdict": "pass", "angle_verdict": "pass", "load_lost_mw": 1e-3},
            "pass",
        ),
    ],
    ids=[
        "passes",
        "not-converged-fails",
        "stage2-fails",
        "angle-fails",
        "load-lost-fails",
        "load-lost-at-tolerance-passes",
    ],
)
def test_case_verdict_fails_closed(row, verdict):
    assert _gc03_case_verdict(row) == verdict


def test_hourly_tables_are_unchanged_by_the_steps(sized):
    before = campus_tables(sized)[0].copy()
    planning_campus(sized)
    strength_campus(sized)
    pd.testing.assert_frame_equal(campus_tables(sized)[0], before)


def test_a_base_case_that_does_not_converge_fails(sized, monkeypatch):
    from gridspine.static import planning_criteria
    from gridspine.static.contingency import BaseCaseNotConverged

    def boom(*a, **k):
        raise BaseCaseNotConverged("base case does not converge")

    monkeypatch.setattr(planning_criteria, "screen_planning", boom)
    out = planning_campus(sized)
    assert out["verdict"] == "fail" and out["cases"] == 0 and out["not_converged"]


def test_a_period_with_no_converged_hour_fails(sized):
    pcc = pd.read_csv(sized / LF_PCC_CSV)
    pcc.loc[pcc["period"] == 2040, "converged"] = False
    pcc.to_csv(sized / LF_PCC_CSV, index=False)
    out = planning_campus(
        sized,
        contingencies=pd.DataFrame(
            columns=["contingency_id", "kind", "element_ids", "order"]
        ),
    )
    # nothing screened at all, but the failed intact flow is a violation
    assert out["verdict"] == "fail" and out["not_converged"] == ["period 2040"]


def test_a_poc_at_exactly_1_kv_carries_no_c_min():
    spec = _spec(caps=())
    spec["campus"]["buses"]["LV"] = {"vn_kv": 1.0}
    spec["campus"]["transformers"]["TL"] = _trafo(
        "MV2", "LV", kv=1.0, sn=1000.0, vk=1.0
    )
    spec["campus"]["units"]["PVL"] = _ibr("pv", "LV", 1.0)
    lv = _at(_strength(spec)[0], "LV")
    # pandapower applies c_min = 0.95 strictly below 1 kV only
    z_tl = 0.01 / 1000.0 * 0.95 * 1.1 / (1 + 0.6 * 0.01)
    assert lv["sk_intact_mva"] == pytest.approx(1 / (1 / SK_MV2 + z_tl), rel=5e-4)
    assert LV_C_NOTE not in str(lv["note"])


def test_source_tags_come_from_the_spec():
    params = pd.DataFrame(
        [
            ("WT", "k_sc", "measured"),
            ("WT", "rx_sc", "vendor sheet"),
            ("PV", "k_sc", "assumed"),
        ],
        columns=["element", "param", "source"],
    )
    c = SimpleNamespace(params=params)
    assert _gc03_source(c, "WT", "k_sc") == "measured"  # a template tag is kept
    assert _gc03_source(c, "WT", "rx_sc") == "datasheet"  # another entered value
    assert _gc03_source(c, "PV", "rx_sc") == "assumed"  # not given
    assert _gc03_source(c, "XX", "k_sc") == "assumed"


def test_strength_refuses_a_normally_open_tie_and_a_campus_with_no_converter():
    with pytest.raises(ContractError, match="normally open"):
        campus_study._gc03_strength(SimpleNamespace(tie_normally_open=True), None)
    units = pd.DataFrame({"kind": ["load", "genset"], "bus": ["MV1", "MV1"]})
    with pytest.raises(ContractError, match="no converter-interfaced unit"):
        campus_study._gc03_strength(
            SimpleNamespace(tie_normally_open=False, units=units), None
        )


def test_the_strength_step_takes_a_str_run_dir(sized):
    assert strength_campus(str(sized))["pocs"]


def _patched_hour_net(monkeypatch, ratings):
    """Each call of ``_gc03_hour_net`` gives the trafos the next emergency rating column."""
    orig, calls = campus_study._gc03_hour_net, []

    def wrapped(*a, **k):
        out = orig(*a, **k)
        net = out[0]
        rated = ratings(len(calls))
        net.trafo["sn_mva_emergency"] = net.trafo["sn_mva"] if rated else float("nan")
        calls.append(rated)
        return out

    monkeypatch.setattr(campus_study, "_gc03_hour_net", wrapped)
    return calls


def test_an_all_nan_emergency_column_is_no_emergency_rating(sized, monkeypatch):
    _patched_hour_net(monkeypatch, lambda i: False)
    out = planning_campus(sized)
    assert out["notes"].count(NO_EMERGENCY_NOTE) == 1
    table = pd.read_csv(sized / PLANNING_CSV)
    assert (table["note"].fillna("").str.count(NO_EMERGENCY_NOTE) == 1).all()


def test_the_emergency_note_follows_each_hour_s_ratings(sized, monkeypatch):
    calls = _patched_hour_net(
        monkeypatch, lambda i: i == 0
    )  # only the first hour is rated
    out = planning_campus(sized)
    assert len(calls) > 1
    assert NO_EMERGENCY_NOTE not in out["notes"]  # some hour had a rating
    table = pd.read_csv(sized / PLANNING_CSV)
    first = (table["period"] == table["period"].min()) & (
        table["hour_kind"] == "max_import"
    )
    counts = table["note"].fillna("").str.count(NO_EMERGENCY_NOTE)
    assert (counts[first] == 0).all() and (counts[~first] == 1).all()


def test_an_hour_with_no_contingency_makes_an_otherwise_passing_screen_incomplete(
    sized, monkeypatch
):
    from gridspine.static import contingency_set

    orig, n = contingency_set.branch_contingencies, []

    def first_empty(net):
        n.append(1)
        return orig(net).iloc[0:0] if len(n) == 1 else orig(net)

    monkeypatch.setattr(contingency_set, "branch_contingencies", first_empty)
    monkeypatch.setattr(
        contingency_set, "unit_contingencies", lambda reg: pd.DataFrame()
    )
    monkeypatch.setattr(campus_study, "_gc03_case_verdict", lambda r: "pass")
    out = planning_campus(sized)
    assert out["cases"] > 0 and out["failures"] == 0
    assert out["verdict"] == "incomplete" and len(out["unscreened"]) == 1


def test_the_planning_csv_leads_with_its_key_columns(sized):
    planning_campus(sized)
    cols = pd.read_csv(sized / PLANNING_CSV).columns
    assert list(cols[:6]) == [
        "period",
        "hour_kind",
        "contingency_id",
        "kind",
        "hour",
        "verdict",
    ]
