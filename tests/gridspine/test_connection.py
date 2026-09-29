"""Increment 10: the connection-point assessment of one facility.

A facility is a LOAD plus a separate ON-SITE unit (BESS or generator) at one
connection point (the owner's decision). Checks, each against the grid-code
profile's tagged limits:

* connection  -- facility in, net import balanced pro-rata (increment 9's
                 rule): no new or worsened violation, intact and N-1;
* energisation -- facility off -> on with NO re-dispatch: the rapid voltage
                 change at the POC;
* load_trip   -- the load drops, the on-site unit STAYS, no re-dispatch;
* facility_trip -- load and on-site unit both drop;
* q_lead / q_lag -- the facility at +/- the DCC Art. 15(1)(a) reactive range;
* scr_onsite / scr_load -- reported, not gated.

Voltage steps are checked against pandapower solved directly (no assessment
code in the loop), on grids small enough to reason about by hand.
"""
import copy
import math

import pandapower as pp
import pandas as pd
import pytest
import yaml

from gridspine.schema.contracts import ContractError
from gridspine.static.connection import CHECKS, Facility, assess_connection
from gridspine.static.contingency_set import branch_contingencies
from gridspine.templates.grid_codes import band_for, load_grid_code

KV = 110.0


def _net(km=50.0, kv=KV, parallel=False):
    """BUS_01 (slack, 1.0 pu) -- km of line -- BUS_02, where the facility goes."""
    net = pp.create_empty_network(sn_mva=100.0)
    b1 = pp.create_bus(net, vn_kv=kv, name="BUS_01")
    b2 = pp.create_bus(net, vn_kv=kv, name="BUS_02")
    pp.create_ext_grid(net, bus=b1, vm_pu=1.0, name="SLK_BUS_01")
    for _ in range(2 if parallel else 1):
        pp.create_line_from_parameters(
            net, from_bus=b1, to_bus=b2, length_km=km, r_ohm_per_km=0.05,
            x_ohm_per_km=0.4, c_nf_per_km=0.0, max_i_ka=2.0,
        )
    pp.create_load(net, bus=b2, p_mw=5.0, q_mvar=1.0)
    return net


def _vm(net, bus="BUS_02"):
    pp.runpp(net)
    return float(net.res_bus.loc[net.bus.index[net.bus["name"] == bus][0], "vm_pu"])


def _with(net, load_mw=0.0, pf=0.98, onsite_mw=0.0):
    w = copy.deepcopy(net)
    b = w.bus.index[w.bus["name"] == "BUS_02"][0]
    if load_mw:
        pp.create_load(w, bus=b, p_mw=load_mw, q_mvar=load_mw * math.tan(math.acos(pf)))
    if onsite_mw:
        pp.create_sgen(w, bus=b, p_mw=onsite_mw, q_mvar=0.0)
    return w


def _row(df, check):
    rows = df[df["check"] == check]
    assert len(rows) == 1, (check, df)
    return rows.iloc[0]


PROFILE = load_grid_code("eu_rfg_dcc_ce")


# --------------------------------------------------------------------------
# the grid-code profile
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kv, band, source", [
    (345.0, (0.90, 1.05), "code"),      # case39: DCC Annex II / RfG Table 6.2
    (400.0, (0.90, 1.05), "code"),      # "from 300 kV to 400 kV (including)"
    (132.0, (0.90, 1.118), "code"),     # 110-300 kV: Table 6.1
    (110.0, (0.90, 1.118), "code"),
    (33.0, (0.90, 1.10), "assumed"),    # below 110 kV: not set by RfG/DCC
])
def test_the_voltage_band_follows_the_nominal_voltage(kv, band, source):
    b = band_for(PROFILE, kv)
    assert (b["v_min"], b["v_max"]) == band and b["source"] == source
    assert b["clause"]


def test_a_voltage_the_profile_does_not_cover_is_refused():
    with pytest.raises(ContractError, match="500"):
        band_for(PROFILE, 500.0)


def test_every_limit_in_the_shipped_profile_carries_a_clause_and_a_tag():
    assert PROFILE["q_range_demand"]["value"] == 0.48
    assert PROFILE["q_range_demand"]["source"] == "code"
    assert PROFILE["rvc_limit_pct"]["source"] == "assumed"      # not in RfG/DCC
    for lim in [*PROFILE["voltage_bands"], PROFILE["q_range_demand"], PROFILE["rvc_limit_pct"]]:
        assert lim["clause"] and lim["source"] in ("code", "assumed")


def _write_profiles(tmp_path, profile):
    f = tmp_path / "codes.yaml"
    f.write_text(yaml.safe_dump({"profiles": {"p": profile}}))
    return f


@pytest.mark.parametrize("mutate, match", [
    (lambda p: p["q_range_demand"].pop("clause"), "clause"),
    (lambda p: p["rvc_limit_pct"].update(source="guessed"), "source"),
    (lambda p: p["voltage_bands"][1].update(v_min=1.2), "v_min"),
    (lambda p: p["voltage_bands"][2].update(kv_min=250.0), "overlap"),
    (lambda p: p.pop("rvc_limit_pct"), "rvc_limit_pct"),
])
def test_an_incomplete_or_impossible_profile_is_refused(tmp_path, mutate, match):
    profile = copy.deepcopy(load_grid_code("eu_rfg_dcc_ce", raw=True))
    mutate(profile)
    with pytest.raises(ContractError, match=match):
        load_grid_code("p", path=_write_profiles(tmp_path, profile))


def test_an_unknown_profile_is_refused():
    with pytest.raises(ContractError, match="no_such"):
        load_grid_code("no_such")


# --------------------------------------------------------------------------
# voltage steps against pandapower solved directly
# --------------------------------------------------------------------------

def test_a_load_trip_step_is_the_voltage_rise_pandapower_computes():
    net = _net()
    fac = Facility(bus="BUS_02", load_mw=40.0, load_pf=0.98)
    out = assess_connection(net, fac, branch_contingencies(net), PROFILE)
    expected = (_vm(_net()) - _vm(_with(_net(), load_mw=40.0))) * 100.0
    trip = _row(out, "load_trip")
    assert trip["value"] == pytest.approx(expected, abs=1e-6)
    # By hand, dV ~ (R P + X Q) / V^2 ~ 2.2 % for 40 MW alone; the 5 MW already
    # at the bus and the non-linearity add to it (50 MW measured 3.24 %).
    assert 2.0 < expected < 3.0
    assert trip["status"] == "pass"      # within the 3 % RVC limit
    assert _row(out, "energisation")["value"] == pytest.approx(-expected, abs=1e-6)


def test_a_bigger_load_breaks_the_rapid_voltage_change_limit():
    net = _net()
    out = assess_connection(net, Facility(bus="BUS_02", load_mw=100.0), branch_contingencies(net), PROFILE)
    trip = _row(out, "load_trip")
    assert trip["status"] == "fail" and trip["value"] > 3.0
    assert trip["limit"] == 3.0 and trip["source"] == "assumed"
    assert _row(out, "energisation")["status"] == "fail"


def test_a_strong_connection_barely_moves():
    net = _net(km=2.0)
    out = assess_connection(net, Facility(bus="BUS_02", load_mw=100.0), branch_contingencies(net), PROFILE)
    assert abs(_row(out, "load_trip")["value"]) < 0.5
    assert _row(out, "load_trip")["status"] == "pass"


def test_the_on_site_unit_stays_on_a_load_trip_and_drops_on_a_facility_trip():
    net = _net()
    fac = Facility(bus="BUS_02", load_mw=100.0, onsite_mw=60.0)
    out = assess_connection(net, fac, branch_contingencies(net), PROFILE)
    connected = _vm(_with(_net(), load_mw=100.0, onsite_mw=60.0))
    after_load_trip = _vm(_with(_net(), onsite_mw=60.0))
    after_facility_trip = _vm(_net())
    assert _row(out, "load_trip")["value"] == pytest.approx((after_load_trip - connected) * 100, abs=1e-6)
    assert _row(out, "facility_trip")["value"] == pytest.approx((after_facility_trip - connected) * 100, abs=1e-6)
    # the BESS left exporting 60 MW pushes the voltage further up than losing both
    assert _row(out, "load_trip")["value"] > _row(out, "facility_trip")["value"]


# --------------------------------------------------------------------------
# reactive range, connection, SCR
# --------------------------------------------------------------------------

def test_the_reactive_range_is_the_dcc_maximum_and_a_weak_line_cannot_deliver_it():
    net = _net(km=120.0)
    fac = Facility(bus="BUS_02", load_mw=60.0)
    out = assess_connection(net, fac, branch_contingencies(net), PROFILE)
    lead, lag = _row(out, "q_lead"), _row(out, "q_lag")
    # +/- 0.48 x 60 MW = 28.8 Mvar at the POC, the DCC Art. 15(1)(a) maximum
    assert "28.8 Mvar" in lead["detail"] and "28.8 Mvar" in lag["detail"]
    assert lead["clause"].startswith("DCC Art. 15(1)(a)")
    assert "fail" in (lead["status"], lag["status"])       # 120 km at 110 kV: one end leaves the band
    assert _row(_ok := assess_connection(_net(km=5.0), fac, branch_contingencies(_net(km=5.0)), PROFILE),
                "q_lead")["status"] == "pass"
    assert _row(_ok, "q_lag")["status"] == "pass"


def test_the_connection_check_uses_the_no_new_no_worse_rule():
    net = _net(parallel=True)
    ok = assess_connection(net, Facility(bus="BUS_02", load_mw=50.0), branch_contingencies(net), PROFILE)
    assert _row(ok, "connection")["status"] == "pass"
    for i in net.line.index:                      # make the circuits small: N-1 overloads
        net.line.at[i, "max_i_ka"] = 0.3
    bad = assess_connection(net, Facility(bus="BUS_02", load_mw=50.0), branch_contingencies(net), PROFILE)
    row = _row(bad, "connection")
    assert row["status"] == "fail" and "after losing" in row["detail"]


def test_scr_is_reported_not_gated():
    net = _net()
    fac = Facility(bus="BUS_02", load_mw=100.0, onsite_mw=50.0, onsite_converter=True)
    out = assess_connection(net, fac, branch_contingencies(net), PROFILE, sk_min_mva=400.0)
    assert _row(out, "scr_onsite")["value"] == pytest.approx(8.0)          # 400 / 50
    assert _row(out, "scr_onsite")["status"] == "reported"
    assert _row(out, "scr_load")["value"] == pytest.approx(400.0 / (100.0 / 0.98))
    no_sk = assess_connection(net, fac, branch_contingencies(net), PROFILE)
    assert (no_sk["check"] != "scr_onsite").all()                          # nothing to report without Sk


def test_every_check_is_present_once_and_the_net_is_not_mutated():
    net = _net()
    before = (net.load.copy(), len(net.sgen), net.gen.copy())
    out = assess_connection(net, Facility(bus="BUS_02", load_mw=30.0, onsite_mw=10.0),
                            branch_contingencies(net), PROFILE, sk_min_mva=300.0)
    assert sorted(out["check"]) == sorted(CHECKS)
    pd.testing.assert_frame_equal(net.load, before[0])
    assert len(net.sgen) == before[1]


@pytest.mark.parametrize("fac, match", [
    (Facility(bus="BUS_99", load_mw=10.0), "BUS_99"),
    (Facility(bus="BUS_02", load_mw=-1.0), "load_mw"),
    (Facility(bus="BUS_02", load_mw=10.0, load_pf=1.5), "load_pf"),
    (Facility(bus="BUS_02", load_mw=0.0, onsite_mw=0.0), "empty"),
])
def test_an_impossible_facility_is_refused(fac, match):
    net = _net()
    with pytest.raises(ContractError, match=match):
        assess_connection(net, fac, branch_contingencies(net), PROFILE)


def _with_gen():
    """BUS_01 (slack) -- BUS_02 (POC) -- BUS_03 with a committed 200 MW unit
    at 50 MW. Re-dispatching that unit changes the flow into the POC."""
    net = _net()
    b3 = pp.create_bus(net, vn_kv=KV, name="BUS_03")
    pp.create_line_from_parameters(net, from_bus=1, to_bus=b3, length_km=40.0, r_ohm_per_km=0.05,
                                   x_ohm_per_km=0.4, c_nf_per_km=0.0, max_i_ka=2.0)
    pp.create_gen(net, bus=b3, p_mw=50.0, max_p_mw=200.0, min_p_mw=0.0, vm_pu=1.0, name="G_BUS_03")
    return net


def test_energisation_is_an_instantaneous_step_the_slack_takes_not_a_re_dispatch():
    # Found by mutation: every earlier grid had no generator, so a re-dispatch
    # on energisation went unnoticed. The step a customer causes is the one
    # BEFORE any unit responds; the slack picks it all up.
    net = _with_gen()
    out = assess_connection(net, Facility(bus="BUS_02", load_mw=60.0), branch_contingencies(net), PROFILE)
    instant = (_vm(_with(_with_gen(), load_mw=60.0)) - _vm(_with_gen())) * 100.0
    assert _row(out, "energisation")["value"] == pytest.approx(instant, abs=1e-6)
    redispatched = _with(_with_gen(), load_mw=60.0)
    redispatched.gen.at[redispatched.gen.index[0], "p_mw"] = 110.0      # what pro-rata would do
    assert abs((_vm(redispatched) - _vm(_with_gen())) * 100.0 - instant) > 0.05   # the test can tell them apart
