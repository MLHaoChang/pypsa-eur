"""Issue 11: the SCR / ESCR / WSCR study under outages, per PoC and stage.

The arithmetic (``strength.strength_table``) is checked by hand on synthetic
fault levels; the outage reruns (``strength_outages.outage_fault_levels``)
on case39 with the minimum IEC 60909 case. Generic fixtures only.
"""
import copy
import math

import pandapower as pp
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.contracts import ContractError
from gridspine.static.contingency_set import branch_contingencies
from gridspine.static.shortcircuit import fault_levels
from gridspine.static.strength import (
    NO_COMPENSATION,
    NO_FILTERS,
    NOT_CONNECTED,
    STUDY_LABEL,
    band,
    escr,
    q_c_by_poc,
    strength_table,
    wscr,
)
from gridspine.static.strength_outages import (
    FULL_N1,
    PRESCREEN_N1,
    PRESCREEN_N2,
    _contributions,
    outage_fault_levels,
)
from gridspine.templates.unit_params import load_unit_templates
from tests.gridspine.test_shortcircuit import HOUR, _hour_tables

# --------------------------------------------------------------------------
# the arithmetic, by hand
# --------------------------------------------------------------------------

POCS = pd.DataFrame({"poc": ["A", "B"], "bus": ["BA", "BB"], "group": ["G", "G"]})
STAGES = pd.DataFrame({
    "stage": ["s1", "s1", "s2", "s2"],
    "poc": ["A", "B", "A", "B"],
    "p_mw": [100.0, 0.0, 200.0, 100.0],
})
INTACT = pd.Series({"BA": 1000.0, "BB": 600.0})
OUTAGES = pd.DataFrame(
    {"BA": [800.0, 900.0, float("nan")], "BB": [500.0, 250.0, 550.0]},
    index=pd.Index(["L1", "L2", "L3"], name="outage"),
)
COMP = pd.DataFrame(
    {"kind": ["capacitor_bank", "capacitor_bank", "shunt_reactor", "statcom"],
     "bus": ["CA1", "CA2", "CA1", "CA1"], "q_mvar": [30.0, 20.0, 40.0, 50.0]},
    index=pd.Index(["C1", "C2", "R1", "S1"], name="name"),
)


def _study(q_c=None):
    return strength_table(POCS, STAGES, INTACT, OUTAGES, FULL_N1, q_c)


def test_escr_and_wscr_formulas_by_hand():
    assert escr(1000.0, 50.0, 100.0) == pytest.approx(9.5)
    # (1000*200 + 600*100) / 300^2
    assert wscr([1000.0, 600.0], [200.0, 100.0]) == pytest.approx(260000.0 / 90000.0)


def test_q_c_counts_capacitor_banks_only_at_full_rating():
    q = q_c_by_poc(COMP, {"CA1": "A", "CA2": "A"})
    assert q.at["A", "q_c_mvar"] == pytest.approx(50.0)
    assert sorted(q.at["A", "banks"]) == ["C1", "C2"]


def test_a_bank_behind_no_poc_is_refused():
    with pytest.raises(ContractError, match="C2"):
        q_c_by_poc(COMP, {"CA1": "A"})


def test_poc_rows_by_hand():
    out = _study(q_c_by_poc(COMP, {"CA1": "A", "CA2": "A"})).pocs.set_index(["stage", "poc"])
    a2 = out.loc[("s2", "A")]
    assert a2["scr_intact"] == pytest.approx(1000.0 / 200.0)
    assert a2["escr_intact"] == pytest.approx((1000.0 - 50.0) / 200.0)
    # L3 disconnects BA, so the worst finite outage is L1 (800 MVA)
    assert a2["worst_outage"] == "L1"
    assert a2["scr_worst"] == pytest.approx(800.0 / 200.0)
    assert a2["escr_worst"] == pytest.approx(750.0 / 200.0)
    assert a2["islanding_outages"] == ["L3"]
    assert "1 outage(s) disconnect this PoC; see islanding_outages" in a2["note"]
    assert NO_FILTERS in a2["note"]
    b2 = out.loc[("s2", "B")]
    assert b2["worst_outage"] == "L2" and b2["scr_worst"] == pytest.approx(2.5)
    assert NO_COMPENSATION in b2["note"]
    assert b2["escr_intact"] == pytest.approx(b2["scr_intact"])


def test_one_row_per_stage_and_poc_and_p_zero_is_not_connected():
    pocs = _study().pocs
    assert list(zip(pocs["stage"], pocs["poc"])) == [("s1", "A"), ("s1", "B"), ("s2", "A"), ("s2", "B")]
    b1 = pocs.set_index(["stage", "poc"]).loc[("s1", "B")]
    assert b1["note"].startswith(NOT_CONNECTED)
    for col in ("scr_intact", "escr_intact", "scr_worst", "escr_worst"):
        assert pd.isna(b1[col])
    connected = pocs[pocs["p_mw"] > 0]
    for col in ("scr_intact", "escr_intact", "scr_worst", "escr_worst"):
        assert connected[col].map(math.isfinite).all()


def test_group_wscr_intact_and_worst_named():
    groups = _study().groups.set_index("stage")
    s2 = groups.loc["s2"]
    assert s2["pocs"] == ["A", "B"]
    assert s2["wscr_intact"] == pytest.approx(260000.0 / 90000.0)
    # L3 islands BA: excluded and listed. L1: (800*200+500*100)/9e4; L2: (900*200+250*100)/9e4
    assert s2["islanding_outages"] == ["L3"]
    assert s2["worst_outage"] == "L2"
    assert s2["wscr_worst"] == pytest.approx(205000.0 / 90000.0)
    assert "disconnect a PoC of this group" in s2["note"]
    # stage 1: B not connected, so the group is A alone and WSCR = SCR(A)
    s1 = groups.loc["s1"]
    assert s1["wscr_intact"] == pytest.approx(1000.0 / 100.0)


def test_a_one_poc_group_has_wscr_equal_to_scr():
    pocs = POCS.assign(group=["G1", None])
    out = strength_table(pocs, STAGES, INTACT, OUTAGES, FULL_N1)
    g = out.groups.set_index("stage").loc["s2"]
    a = out.pocs.set_index(["stage", "poc"]).loc[("s2", "A")]
    assert g["wscr_intact"] == pytest.approx(a["scr_intact"])
    assert g["wscr_worst"] == pytest.approx(a["scr_worst"])
    assert g["worst_outage"] == a["worst_outage"]
    assert len(out.groups) == 2  # G1 per stage; the ungrouped PoC makes no group


def test_every_outage_islands_gives_null_worst_not_intact():
    out = strength_table(POCS.iloc[:1], STAGES[STAGES["poc"] == "A"], INTACT,
                         OUTAGES[["BA"]].assign(BA=float("nan")), FULL_N1)
    row = out.pocs.iloc[-1]
    assert row["worst_outage"] is None
    assert pd.isna(row["scr_worst"]) and pd.isna(row["escr_worst"]) and row["band_worst"] is None
    assert "3 outage(s) disconnect this PoC" in row["note"]


def test_bands_label_and_escr_le_scr_on_every_row():
    out = _study(q_c_by_poc(COMP, {"CA1": "A", "CA2": "B"}))
    for r in out.pocs.itertuples(index=False):
        assert r.label == STUDY_LABEL
        for ratio, b in ((r.scr_intact, r.band_intact), (r.escr_intact, r.escr_band_intact),
                         (r.scr_worst, r.band_worst), (r.escr_worst, r.escr_band_worst)):
            assert b == (None if pd.isna(ratio) else band(ratio))
        if not pd.isna(r.scr_intact):
            assert r.escr_intact <= r.scr_intact and r.escr_worst <= r.scr_worst
            assert r.scr_worst <= r.scr_intact
    for r in out.groups.itertuples(index=False):
        assert r.label == STUDY_LABEL
        assert r.band_intact == band(r.wscr_intact) and r.band_worst == band(r.wscr_worst)


@pytest.mark.parametrize("p", [-1.0, float("inf"), float("nan")])
def test_negative_or_non_finite_p_is_refused(p):
    with pytest.raises(ContractError, match="p_mw"):
        strength_table(POCS, STAGES.assign(p_mw=[p, 0.0, 1.0, 1.0]), INTACT, OUTAGES, FULL_N1)


# --------------------------------------------------------------------------
# the outage reruns, on case39
# --------------------------------------------------------------------------

SPUR = "SPUR_1"


@pytest.fixture(scope="module")
def case39():
    net = load_case39_res()
    reg = registry_from_net(net)
    t = load_unit_templates()
    d, l = _hour_tables(net, reg)
    return net, reg, t, d, l


@pytest.fixture(scope="module")
def spur_net(case39):
    """case39 with a load PoC on a radial spur off BUS_04: its feeder outage islands it."""
    net = copy.deepcopy(case39[0])
    b4 = int(net.bus.index[net.bus["name"] == "BUS_04"][0])
    nb = pp.create_bus(net, vn_kv=345.0, name=SPUR)
    pp.create_line_from_parameters(net, b4, nb, 20.0, 0.03, 0.3, 10.0, 1.0, name="SPUR_LINE")
    return net


@pytest.fixture(scope="module")
def full(case39):
    net, reg, t, d, l = case39
    return outage_fault_levels(net, d, l, HOUR, reg, t, ["BUS_04", "BUS_08", "BUS_33"], cap=100)


def test_intact_sk_equals_fault_levels_min(case39, full):
    """Load PoCs and the RES PoC alike: the minimum case has no converter
    contribution, so switching the PoC's own converter off changes nothing.
    The intact case is solved after every outage, so this also pins intact ->
    outages -> intact: no outage leaks into the next solve."""
    net, reg, t, d, l = case39
    fl = fault_levels(net, d, l, HOUR, reg, t, case="min").set_index("bus")["sk_mva"]
    for bus in ("BUS_04", "BUS_08", "BUS_33"):
        assert full.intact[bus] == pytest.approx(fl[bus])


def test_full_n1_covers_the_contingency_set_and_worst_le_intact(case39, full):
    n1 = branch_contingencies(case39[0])
    assert full.method == FULL_N1
    assert list(full.outages.index) == list(n1["contingency_id"])
    for bus in full.intact.index:
        # an outage may raise S_sc by a solver-noise amount (line charging), never more
        finite = full.outages[bus].dropna()
        assert (finite <= full.intact[bus] * (1 + 1e-6)).all()
        assert finite.min() < full.intact[bus]


def test_runs_are_independent_and_the_net_is_untouched(case39, full):
    net, reg, t, d, l = case39
    before = copy.deepcopy(net)
    again = outage_fault_levels(net, d, l, HOUR, reg, t, ["BUS_04", "BUS_08", "BUS_33"], cap=100)
    pd.testing.assert_series_equal(full.intact, again.intact)
    pd.testing.assert_frame_equal(full.outages, again.outages)
    pd.testing.assert_frame_equal(net.line, before.line)
    pd.testing.assert_frame_equal(net.sgen, before.sgen)


def test_case39_study_names_the_worst_outage_per_poc(case39, full):
    pocs = pd.DataFrame({"poc": ["P4", "P8", "P33"], "bus": ["BUS_04", "BUS_08", "BUS_33"],
                         "group": ["near", "near", None]})
    stages = pd.DataFrame({"stage": ["1", "1", "1", "2", "2", "2"],
                           "poc": ["P4", "P8", "P33"] * 2,
                           "p_mw": [200.0, 100.0, 600.0, 400.0, 300.0, 600.0]})
    out = strength_table(pocs, stages, full.intact, full.outages, full.method)
    n1 = set(branch_contingencies(case39[0])["contingency_id"])
    for r in out.pocs.itertuples(index=False):
        assert r.worst_outage in n1
        assert r.scr_worst <= r.scr_intact
        assert r.sk_worst_mva == pytest.approx(full.outages[r.bus].min())
        assert r.label == STUDY_LABEL and r.outage_method == FULL_N1
    assert set(out.groups["worst_outage"]) <= n1
    # S_sc is computed once: only P changes between stages
    p4 = out.pocs[out.pocs["poc"] == "P4"]
    assert p4["sk_intact_mva"].nunique() == 1 and p4["scr_intact"].iloc[0] == 2 * p4["scr_intact"].iloc[1]


def test_prescreen_above_the_cap_outages_the_top_contributor(case39, full):
    net, reg, t, d, l = case39
    pre = outage_fault_levels(net, d, l, HOUR, reg, t, ["BUS_04"], cap=10)
    assert pre.method == PRESCREEN_N1
    assert len(pre.outages) == 1
    top = pre.outages.index[0]
    incident = branch_contingencies(net)
    assert "BUS_04" in (incident.set_index("contingency_id").loc[top, ["from_bus", "to_bus"]].tolist())
    assert pre.intact["BUS_04"] == pytest.approx(full.intact["BUS_04"])
    assert pre.outages.at[top, "BUS_04"] >= full.outages["BUS_04"].min() - 1e-6
    assert pre.outages.at[top, "BUS_04"] == pytest.approx(full.outages.at[top, "BUS_04"])


def test_contributions_rank_the_incident_branches_first(case39):
    import pandapower.shortcircuit as sc

    from gridspine.static.shortcircuit import apply_fault_state
    net, reg, t, d, l = case39
    work = copy.deepcopy(net)
    apply_fault_state(work, d, l, HOUR, reg, t)
    b = int(work.bus.index[work.bus["name"] == "BUS_04"][0])
    sc.calc_sc(work, case="min", bus=b, branch_results=True)
    etype, eidx = _contributions(work, b).index[0]
    row = getattr(work, etype).loc[eidx]
    ends = (row["from_bus"], row["to_bus"]) if etype == "line" else (row["hv_bus"], row["lv_bus"])
    assert b in ends


def test_n1_full_and_n2_prescreen_are_decided_per_order(case39):
    net, reg, t, d, l = case39
    out = outage_fault_levels(net, d, l, HOUR, reg, t, ["BUS_04"], cap=50, n2=True)
    assert out.method == f"{FULL_N1}; {PRESCREEN_N2}"
    pairs = [c for c in out.outages.index if "--" in c]
    assert len(pairs) == 1
    a, b = pairs[0].split("--")
    assert a < b
    assert out.outages.at[pairs[0], "BUS_04"] <= min(out.outages.at[a, "BUS_04"],
                                                     out.outages.at[b, "BUS_04"]) + 1e-6


def test_islanding_outage_is_nan_and_listed(case39, spur_net):
    _net, reg, t, d, l = case39
    out = outage_fault_levels(spur_net, d, l, HOUR, reg, t, [SPUR], cap=100)
    assert out.outages[SPUR].isna().sum() == 1
    feeder = out.outages.index[out.outages[SPUR].isna()][0]
    assert "SPUR_1" in feeder
    study = strength_table(pd.DataFrame({"poc": ["S"], "bus": [SPUR]}),
                           pd.DataFrame({"stage": ["1"], "poc": ["S"], "p_mw": [100.0]}),
                           out.intact, out.outages, out.method)
    row = study.pocs.iloc[0]
    assert row["islanding_outages"] == [feeder]
    assert row["worst_outage"] != feeder and math.isfinite(row["scr_worst"])
    assert "1 outage(s) disconnect this PoC" in row["note"]


def test_prescreen_is_said_in_the_row_note():
    out = strength_table(POCS, STAGES, INTACT, OUTAGES, PRESCREEN_N1)
    assert out.pocs["note"].str.contains("pre-screen used").all()
    assert out.groups["note"].str.contains("pre-screen used").all()
    assert "pre-screen" not in "".join(_study().pocs["note"])


def test_a_solver_failure_names_the_outage_not_islanding(case39, monkeypatch):
    import gridspine.static.strength_outages as so
    net, reg, t, d, l = case39
    real = so.sc.calc_sc

    def failing(work, **kw):
        if not work.line["in_service"].all():
            raise RuntimeError("singular")
        return real(work, **kw)

    monkeypatch.setattr(so.sc, "calc_sc", failing)
    with pytest.raises(ContractError, match=r"outage BUS_\d+-BUS_\d+-1.*singular"):
        outage_fault_levels(net, d, l, HOUR, reg, t, ["BUS_04"], cap=100)


def test_unknown_poc_bus_and_bad_cap_are_refused(case39):
    net, reg, t, d, l = case39
    with pytest.raises(ContractError, match="NOPE"):
        outage_fault_levels(net, d, l, HOUR, reg, t, ["NOPE"], cap=10)
    with pytest.raises(ContractError, match="cap"):
        outage_fault_levels(net, d, l, HOUR, reg, t, ["BUS_04"], cap=0)
