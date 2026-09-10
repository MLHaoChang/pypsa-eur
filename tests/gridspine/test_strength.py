"""Task 7: the short-circuit-ratio pre-check.

SCR = S_k'' at the connection bus / installed inverter MVA at that bus. Plain
SCR only — WSCR, ESCR, the impedance screen and the RoCoF flag are spec phase
4, and a pre-check that quietly becomes a grid-strength study is scope creep
with a compliance-shaped tail.

Two rulings the tests pin:

* The denominator is INSTALLED capacity from the templates (the ledgered
  "installed MW read as MVA"), never the dispatched hour. SCR is a property of
  the network's strength at the bus; dividing by a curtailed output would
  report a weak bus as strong. A curtailed hour and a full-output hour must
  therefore give the SAME table.
* The bands are REPORTED thresholds, not pass/fail gates. A screen that fails a
  bus decides something this stage has no standing to decide.
"""
import pandas as pd
import pytest

from gridspine.ingest.pandapower_source import RES_LEDGER, load_case39_res, registry_from_net
from gridspine.schema.contracts import ContractError
from gridspine.static.shortcircuit import fault_levels
from gridspine.static.strength import SCR_BANDS, SCR_LEDGER, scr
from gridspine.templates.unit_params import load_unit_templates

from tests.gridspine.test_shortcircuit import HOUR, _hour_tables

RES_BUSES = {e["bus"] for e in RES_LEDGER}
CAP = {e["bus"]: e["p_mw"] for e in RES_LEDGER}


@pytest.fixture(scope="module")
def case39():
    net = load_case39_res()
    reg = registry_from_net(net)
    t = load_unit_templates()
    d, l = _hour_tables(net, reg)
    return net, reg, t, fault_levels(net, d, l, HOUR, reg, t)


def test_scr_is_fault_level_over_installed_capacity(case39):
    _net, reg, t, fl = case39
    out = scr(fl, reg, t).set_index("bus")
    sk = fl.set_index("bus")["sk_mva"]
    for bus in RES_BUSES:
        assert out.at[bus, "ibr_mva"] == pytest.approx(CAP[bus])
        assert out.at[bus, "sk_mva"] == pytest.approx(sk[bus])
        assert out.at[bus, "scr"] == pytest.approx(sk[bus] / CAP[bus])


def test_only_res_buses_are_reported_never_inf_for_the_rest(case39):
    _net, reg, t, fl = case39
    out = scr(fl, reg, t)
    assert set(out["bus"]) == RES_BUSES
    assert len(out) == len(RES_BUSES)
    assert out["scr"].map(lambda v: v == v and abs(v) != float("inf")).all()


def test_a_curtailed_hour_and_a_full_output_hour_give_the_same_scr(case39):
    """Installed, not dispatched — and the fault state keeps a curtailed inverter
    energised, so the numerator is unchanged too."""
    net, reg, t, _fl = case39
    d_full, l_full = _hour_tables(net, reg)
    d_curt, l_curt = _hour_tables(net, reg, curtailed=("W_BUS_33", "S_BUS_34"))
    a = scr(fault_levels(net, d_full, l_full, HOUR, reg, t), reg, t)
    b = scr(fault_levels(net, d_curt, l_curt, HOUR, reg, t), reg, t)
    pd.testing.assert_frame_equal(a, b)


def test_bands_are_reported_from_the_declared_thresholds():
    """Synthetic levels placed inside each band and exactly on each boundary."""
    reg = pd.DataFrame(
        {"bus": ["B1", "B2", "B3", "B4", "B5"], "kind": "res"},
        index=pd.Index(["W_1", "W_2", "W_3", "W_4", "W_5"], name="unit_id"),
    )
    from gridspine.templates.unit_params import UnitTemplates
    units = pd.DataFrame(
        {"model": "inverter", "mbase_mva": 100.0, "include_in_inertia": False},
        index=pd.Index(["W_1", "W_2", "W_3", "W_4", "W_5"], name="unit_id"),
    )
    t = UnitTemplates(units=units, params=pd.DataFrame(columns=["unit_id", "param", "value", "source"]))
    lo, mid, hi = SCR_BANDS  # ascending thresholds
    fl = pd.DataFrame({
        "bus": ["B1", "B2", "B3", "B4", "B5"],
        "ikss_ka": 1.0,
        "sk_mva": [100.0 * (lo - 0.5), 100.0 * lo, 100.0 * (mid - 0.1), 100.0 * mid, 100.0 * (hi + 1)],
        "case": "max",
    })
    out = scr(fl, reg, t).set_index("bus")["band"]
    assert out["B1"] == "very_weak"
    assert out["B2"] == "weak"        # boundary belongs to the upper band
    assert out["B3"] == "weak"
    assert out["B4"] == "moderate"
    assert out["B5"] == "strong"


def test_two_inverters_on_one_bus_are_summed(case39):
    _net, reg, t, fl = case39
    reg2 = reg.copy()
    reg2.loc["W_BUS_33b"] = {"bus": "BUS_33", "kind": "res"}
    from gridspine.templates.unit_params import UnitTemplates
    units = pd.concat([t.units, pd.DataFrame(
        {"model": ["inverter"], "mbase_mva": [400.0], "include_in_inertia": [False]},
        index=pd.Index(["W_BUS_33b"], name="unit_id"))])
    t2 = UnitTemplates(units=units, params=t.params)
    out = scr(fl, reg2, t2).set_index("bus")
    assert out.at["BUS_33", "ibr_mva"] == pytest.approx(600.0 + 400.0)


def test_a_res_bus_without_a_fault_level_is_a_contract_error(case39):
    _net, reg, t, fl = case39
    with pytest.raises(ContractError, match="BUS_33"):
        scr(fl[fl["bus"] != "BUS_33"], reg, t)


def test_a_res_unit_without_a_template_row_is_a_contract_error(case39):
    _net, reg, t, fl = case39
    from gridspine.templates.unit_params import UnitTemplates
    t2 = UnitTemplates(units=t.units.drop(index="S_BUS_36"), params=t.params)
    with pytest.raises(ContractError, match="S_BUS_36"):
        scr(fl, reg, t2)


def test_input_fault_levels_are_validated(case39):
    _net, reg, t, fl = case39
    bad = fl.copy()
    bad.loc[0, "sk_mva"] = 0.0
    with pytest.raises(ContractError, match="sk_mva"):
        scr(bad, reg, t)


def test_a_mixed_case_table_is_rejected_and_the_case_is_carried(case39):
    """SCR at one bus from two 60909 cases is two different numbers."""
    net, reg, t, fl_max = case39
    d, l = _hour_tables(net, reg)
    fl_min = fault_levels(net, d, l, HOUR, reg, t, case="min")
    assert (scr(fl_max, reg, t)["case"] == "max").all()
    assert (scr(fl_min, reg, t)["case"] == "min").all()
    with pytest.raises(ContractError, match="single 60909 case"):
        scr(pd.concat([fl_max, fl_min], ignore_index=True), reg, t)


def test_scr_ledger_records_installed_not_dispatched():
    text = " ".join(SCR_LEDGER).lower()
    assert "installed" in text and "dispatch" in text and "gate" in text


def test_strength_imports_no_engine():
    import gridspine.static.strength as mod

    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("import pypsa", "import pandapower", "gridspine.producers"):
        assert banned not in src
