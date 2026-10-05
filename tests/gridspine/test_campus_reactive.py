"""Reactive power at the PCC and the compensation it needs (C4b).

The requirement keeps the PCC reactive exchange within +-q_limit Mvar, where
q_limit = q_frac * P_ref:
* q_frac is the profile's DCC Art. 15(1)(a) range (0.48 Q/Pmax), or
  tan(acos(pf)) for a power factor the study sets;
* P_ref is the connection capacity.

In an hour outside the band, the campus must supply the difference:
* the inverters first, up to sqrt(S^2 - P^2) each, shared in proportion
  to their headroom;
* the remainder as compensation at the main MV busbar (+ capacitive,
  - inductive).

Load flows are iterated until the PCC sits on the band edge, because
transformer and cable reactive losses move with the flow. The oracle
re-applies the reported Q dispatch to the same network written directly
in pandapower and reads the PCC Q.
"""
import math

import pandas as pd
import pandapower as pp
import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_reactive import (
    ReactiveRequirement,
    compensation_bus,
    reactive_need,
    requirement_from,
    size_compensation,
)
from gridspine.static.campus_flow import SizingCriteria
from gridspine.templates.grid_codes import load_grid_code
from tests.gridspine.test_campus import campus_spec, direct_net
from tests.gridspine.test_campus_flow import HOUR, rows


def lowpf_campus(pf=0.85):
    spec = campus_spec()
    spec["campus"]["units"]["DC_LOAD"]["pf"]["value"] = pf
    return build_campus(spec)


def oracle_q(result, hour=HOUR, pf=0.85):
    """PCC Q of the hand-built network with the reported Q dispatch applied."""
    net = direct_net()
    li = net.load.index[net.load["name"] == "DC_LOAD"][0]
    net.load.at[li, "p_mw"] = -hour["DC_LOAD"]
    net.load.at[li, "q_mvar"] = -hour["DC_LOAD"] * math.tan(math.acos(pf))
    for name in ("BESS1", "PV1", "GEN1"):
        i = net.sgen.index[net.sgen["name"] == name][0]
        net.sgen.at[i, "p_mw"] = hour[name]
        net.sgen.at[i, "q_mvar"] = result.unit_q.get(name, 0.0)
    if result.q_comp_mvar:
        mv1 = net.bus.index[net.bus["name"] == "MV1"][0]
        pp.create_sgen(net, mv1, p_mw=0.0, q_mvar=result.q_comp_mvar)
    pp.runpp(net)
    return float(net.res_ext_grid["q_mvar"].iloc[0])


def test_the_requirement_comes_from_the_profile_or_a_study_power_factor():
    profile = load_grid_code("eu_rfg_dcc_ce")
    req = requirement_from(profile, p_ref_mw=50.0)
    assert req.q_limit_mvar == pytest.approx(0.48 * 50.0)
    assert req.clause.startswith("DCC Art. 15(1)(a)") and req.source == "code"
    req = requirement_from(profile, p_ref_mw=50.0, pf=0.95)
    assert req.q_limit_mvar == pytest.approx(50.0 * math.tan(math.acos(0.95)))
    assert req.source == "assumed" and "0.95" in req.clause
    with pytest.raises(ContractError, match="pf"):
        requirement_from(profile, p_ref_mw=50.0, pf=1.2)


def test_an_hour_inside_the_band_needs_nothing():
    camp = build_campus(campus_spec())
    res = reactive_need(camp, rows(), ReactiveRequirement(q_limit_mvar=30.0, clause="c", source="code"))
    assert res.converged and res.compliant_without
    assert res.q_inverters_mvar == 0.0 and res.q_comp_mvar == 0.0


def test_an_hour_just_outside_the_band_is_corrected():
    camp = lowpf_campus()
    q0 = reactive_need(camp, rows(), ReactiveRequirement(1e6, "c", "code")).q0_mvar
    res = reactive_need(camp, rows(), ReactiveRequirement(0.8 * q0, "c", "code"))
    assert not res.compliant_without
    assert res.q_final_mvar == pytest.approx(0.8 * q0, abs=0.01)


def test_inverters_with_headroom_cover_the_need_and_the_pcc_lands_on_the_band_edge():
    camp = lowpf_campus()
    req = ReactiveRequirement(q_limit_mvar=15.0, clause="c", source="code")
    res = reactive_need(camp, rows(), req)
    assert res.q0_mvar > 15.0 and not res.compliant_without
    assert res.q_comp_mvar == 0.0 and res.q_inverters_mvar > 0.0
    assert res.q_final_mvar == pytest.approx(15.0, abs=0.01)
    assert oracle_q(res) == pytest.approx(res.q_final_mvar, abs=1e-6)
    # each inverter stays inside its circle
    for uid, q in res.unit_q.items():
        u = camp.units.loc[uid]
        p = HOUR[uid]
        assert abs(q) <= math.sqrt(u["s_mva"] ** 2 - p ** 2) + 1e-9


def test_when_the_inverters_run_out_the_rest_is_capacitive_compensation():
    camp = lowpf_campus(pf=0.7)                                  # ~46 Mvar of load Q
    req = ReactiveRequirement(q_limit_mvar=2.0, clause="c", source="code")
    res = reactive_need(camp, rows(), req)
    # GEN1 is at 0 MW in this hour: not running, so it offers no Q
    head = sum(math.sqrt(camp.units.at[u, "s_mva"] ** 2 - HOUR[u] ** 2) for u in ("BESS1", "PV1"))
    assert res.q_inverters_mvar == pytest.approx(head)          # every inverter at its limit
    assert res.q_comp_mvar > 0.0                                 # capacitive
    assert res.q_final_mvar == pytest.approx(2.0, abs=0.01)
    assert oracle_q(res, pf=0.7) == pytest.approx(res.q_final_mvar, abs=1e-6)


def test_an_off_unit_offers_no_reactive_power():
    camp = lowpf_campus(pf=0.8)
    req = ReactiveRequirement(q_limit_mvar=5.0, clause="c", source="code")
    res = reactive_need(camp, rows(status={"PV1": 0}, hour=dict(HOUR, PV1=0.0)), req)
    assert "PV1" not in res.unit_q or res.unit_q["PV1"] == 0.0


def test_a_campus_pushing_reactive_power_out_needs_inductive_compensation():
    spec = campus_spec()
    spec["campus"]["cables"]["CB1"]["c_nf_per_km"]["value"] = 4000.0      # ~10 Mvar of cable charging
    spec["campus"]["cables"]["CB1"]["length_km"]["value"] = 20.0
    for uid in ("BESS1", "PV1", "GEN1"):
        spec["campus"]["units"][uid]["s_mva"]["value"] = spec["campus"]["units"][uid]["p_mw"]["value"]
    camp = build_campus(spec)
    hour = dict(HOUR, DC_LOAD=-2.0, BESS1=20.0, PV1=15.0, GEN1=10.0)   # all at full P: no headroom
    req = ReactiveRequirement(q_limit_mvar=1.0, clause="c", source="code")
    res = reactive_need(camp, rows(hour=hour), req)
    assert res.q0_mvar < -1.0
    assert res.q_comp_mvar < 0.0                                 # inductive
    assert res.q_final_mvar == pytest.approx(-1.0, abs=0.01)


def test_the_compensation_sits_on_the_low_voltage_side_of_the_pcc_transformers():
    assert compensation_bus(build_campus(campus_spec())) == "MV1"


def test_compensation_is_sized_to_the_worst_hour_in_each_direction_with_margin():
    from gridspine.static.campus_reactive import ReactiveResult
    r = lambda comp, inv: ReactiveResult(True, 0.0, 0.0, inv, comp, {}, False)
    results = {(2030, 1): r(4.0, 2.0), (2030, 5): r(-3.0, 1.0), (2040, 2): r(6.5, 0.0)}
    s = size_compensation(results, SizingCriteria(margin=0.2)).set_index("direction")
    assert s.at["capacitive", "required_mvar"] == pytest.approx(6.5)
    assert s.at["capacitive", "recommended_mvar"] == pytest.approx(math.ceil(6.5 * 1.2))
    assert (s.at["capacitive", "worst_period"], s.at["capacitive", "worst_hour"]) == (2040, 2)
    assert s.at["inductive", "required_mvar"] == pytest.approx(3.0)
    assert s.at["inductive", "recommended_mvar"] == pytest.approx(math.ceil(3.0 * 1.2))


def test_an_idle_genset_or_a_dark_pv_offers_nothing_but_an_idle_battery_does():
    camp = lowpf_campus(pf=0.8)
    req = ReactiveRequirement(q_limit_mvar=5.0, clause="c", source="code")
    hour = dict(HOUR, PV1=0.0, GEN1=0.0, BESS1=0.0)
    res = reactive_need(camp, rows(hour=hour), req)
    assert set(res.unit_q) == {"BESS1"}
    assert abs(res.q_inverters_mvar) <= 22.0 + 1e-9               # the battery's S alone


# --------------------------------------------------------------------------
# installed compensation is dispatched after the inverters (C8)
# --------------------------------------------------------------------------

DARK = {"BESS1": 0, "PV1": 0, "GEN1": 0}          # every inverter off: no headroom at all
DARK_HOUR = dict(HOUR, BESS1=0.0, PV1=0.0, GEN1=0.0)


def comp_campus(*entries, pf=0.85):
    spec = campus_spec()
    spec["campus"]["units"]["DC_LOAD"]["pf"]["value"] = pf
    spec["campus"]["compensation"] = list(entries)
    return build_campus(spec)


def dark_net(pf=0.85):
    """The hand-built campus at the dark hour: the load alone, every unit off."""
    net = direct_net()
    li = net.load.index[net.load["name"] == "DC_LOAD"][0]
    net.load.at[li, "p_mw"] = 45.0
    net.load.at[li, "q_mvar"] = 45.0 * math.tan(math.acos(pf))
    net.sgen["in_service"] = False
    return net


def test_a_capacitor_bank_switches_the_fewest_steps_that_bring_the_pcc_into_the_band():
    lim, n_steps, per_step = 15.0, 6, 4.0
    camp = comp_campus({"name": "CAP1", "bus": "MV1", "kind": "capacitor_bank",
                        "q_mvar": {"value": n_steps * per_step, "source": "assumed"}, "steps": n_steps})
    res = reactive_need(camp, rows(hour=DARK_HOUR, status=DARK), ReactiveRequirement(lim, "c", "code"),
                        residual=False)
    # brute force over the step counts on the hand-built network
    ref = dark_net()
    sh = pp.create_shunt(ref, ref.bus.index[ref.bus["name"] == "MV1"][0], q_mvar=-per_step, step=0, max_step=n_steps)
    q_at = {}
    for k in range(n_steps + 1):
        ref.shunt.at[sh, "step"] = k
        pp.runpp(ref)
        q_at[k] = float(ref.res_ext_grid["q_mvar"].iloc[0])
    fewest = min(k for k, q in q_at.items() if abs(q) <= lim)
    assert 1 < fewest < n_steps                                  # the case is not trivial
    assert res.q0_mvar == pytest.approx(q_at[0], abs=1e-6) and not res.compliant_without
    assert res.dispatch["CAP1"]["steps"] == fewest
    assert res.q_final_mvar == pytest.approx(q_at[fewest], abs=1e-6)
    assert res.q_comp_mvar == 0.0 and res.q_inverters_mvar == 0.0
    assert res.dispatch["CAP1"]["q_mvar"] > 0                       # capacitive, reported + like q_comp
    assert res.setpoints == {"sgen_q": {}, "shunt_step": {"CAP1": fewest}}


def test_a_bank_too_small_is_switched_fully_and_the_rest_stays_a_gap():
    lim = 5.0
    camp = comp_campus({"name": "CAP1", "bus": "MV1", "kind": "capacitor_bank",
                        "q_mvar": {"value": 6.0, "source": "assumed"}, "steps": 2})
    hour, req = rows(hour=DARK_HOUR, status=DARK), ReactiveRequirement(lim, "c", "code")
    real = reactive_need(camp, hour, req, residual=False)
    ref = dark_net()
    pp.create_shunt(ref, ref.bus.index[ref.bus["name"] == "MV1"][0], q_mvar=-3.0, step=2, max_step=2)
    pp.runpp(ref)
    assert real.dispatch["CAP1"]["steps"] == 2
    assert real.q_final_mvar == pytest.approx(float(ref.res_ext_grid["q_mvar"].iloc[0]), abs=1e-6)
    assert real.q_final_mvar > lim + 1.0 and real.q_comp_mvar == 0.0
    # with the residual on (part one's sizing), the rest is reported as a gap and the PCC lands on the edge
    need = reactive_need(camp, hour, req)
    assert need.dispatch["CAP1"]["steps"] == 2 and need.q_comp_mvar > 0.0
    assert need.q_final_mvar == pytest.approx(lim, abs=0.01)


def _q_on_edge(net, bus, lim):
    """The Q an sgen at ``bus`` must inject to put the PCC on ``+lim``,
    by bisection on the hand-built network."""
    g = pp.create_sgen(net, net.bus.index[net.bus["name"] == bus][0], p_mw=0.0, q_mvar=0.0)
    lo, hi = 0.0, 60.0
    for _ in range(60):
        mid = (lo + hi) / 2
        net.sgen.at[g, "q_mvar"] = mid
        pp.runpp(net)
        lo, hi = (mid, hi) if float(net.res_ext_grid["q_mvar"].iloc[0]) > lim else (lo, mid)
    return (lo + hi) / 2


def test_a_statcom_supplies_the_gap_continuously():
    lim = 12.0
    camp = comp_campus({"name": "ST1", "bus": "MV1", "kind": "statcom", "q_mvar": {"value": 30.0, "source": "assumed"}})
    res = reactive_need(camp, rows(hour=DARK_HOUR, status=DARK), ReactiveRequirement(lim, "c", "code"), residual=False)
    gap = _q_on_edge(dark_net(), "MV1", lim)
    assert res.dispatch["ST1"]["q_mvar"] == pytest.approx(gap, abs=0.02)
    assert res.q_final_mvar == pytest.approx(lim, abs=0.01)
    assert res.setpoints["sgen_q"]["ST1"] == pytest.approx(gap, abs=0.02)


def test_the_inverters_go_first_and_the_statcom_covers_the_rest():
    camp = comp_campus({"name": "ST1", "bus": "MV1", "kind": "statcom", "q_mvar": {"value": 40.0, "source": "assumed"}},
                       pf=0.7)
    res = reactive_need(camp, rows(), ReactiveRequirement(2.0, "c", "code"), residual=False)
    head = sum(math.sqrt(camp.units.at[u, "s_mva"] ** 2 - HOUR[u] ** 2) for u in ("BESS1", "PV1"))
    assert res.q_inverters_mvar == pytest.approx(head)                # every inverter at its limit first
    assert res.dispatch["ST1"]["q_mvar"] > 0.0
    assert res.q_final_mvar == pytest.approx(2.0, abs=0.01)
    # a gap the inverters can cover leaves the STATCOM idle
    res = reactive_need(camp, rows(), ReactiveRequirement(30.0, "c", "code"), residual=False)
    assert res.dispatch["ST1"]["q_mvar"] == 0.0


def test_a_shunt_reactor_absorbs_when_the_campus_pushes_reactive_power_out():
    spec = campus_spec()
    spec["campus"]["cables"]["CB1"]["c_nf_per_km"]["value"] = 4000.0
    spec["campus"]["cables"]["CB1"]["length_km"]["value"] = 20.0
    for uid in ("BESS1", "PV1", "GEN1"):
        spec["campus"]["units"][uid]["s_mva"]["value"] = spec["campus"]["units"][uid]["p_mw"]["value"]
    spec["campus"]["compensation"] = [
        {"name": "CAP1", "bus": "MV1", "kind": "capacitor_bank", "q_mvar": {"value": 10.0, "source": "assumed"}, "steps": 2},
        {"name": "SR1", "bus": "MV1", "kind": "shunt_reactor", "q_mvar": {"value": 8.0, "source": "assumed"}}]
    camp = build_campus(spec)
    hour = dict(HOUR, DC_LOAD=-2.0, BESS1=20.0, PV1=15.0, GEN1=10.0)
    res = reactive_need(camp, rows(hour=hour), ReactiveRequirement(1.0, "c", "code"), residual=False)
    assert res.q0_mvar < -1.0
    assert res.dispatch["SR1"]["steps"] == 1 and res.dispatch["SR1"]["q_mvar"] < 0     # inductive
    assert res.dispatch["CAP1"]["steps"] == 0                                         # the wrong direction stays off
    assert abs(res.q_final_mvar) <= 1.0 + 0.01


def test_without_compensation_nothing_is_dispatched_and_the_setpoints_are_the_inverters():
    camp = lowpf_campus()
    res = reactive_need(camp, rows(), ReactiveRequirement(15.0, "c", "code"))
    assert res.dispatch == {} and res.q_installed_mvar == 0.0
    assert res.setpoints == {"sgen_q": res.unit_q, "shunt_step": {}}


@pytest.mark.parametrize("q_reactor, steps", [(15.0, 0), (10.0, 1)])
def test_a_step_that_overshoots_is_kept_only_if_it_leaves_the_pcc_closer_to_the_band(q_reactor, steps):
    """At this hour the PCC exports 8.4 Mvar against a 1 Mvar band. A 15 Mvar
    reactor would leave it 7.4 Mvar outside on the other side, no closer
    (the fewest steps win the tie); a 10 Mvar one leaves it 1.9 Mvar outside."""
    spec = campus_spec()
    spec["campus"]["cables"]["CB1"]["c_nf_per_km"]["value"] = 4000.0
    spec["campus"]["cables"]["CB1"]["length_km"]["value"] = 20.0
    for uid in ("BESS1", "PV1", "GEN1"):
        spec["campus"]["units"][uid]["s_mva"]["value"] = spec["campus"]["units"][uid]["p_mw"]["value"]
    spec["campus"]["compensation"] = [
        {"name": "SR1", "bus": "MV1", "kind": "shunt_reactor", "q_mvar": {"value": q_reactor, "source": "assumed"}}]
    hour = dict(HOUR, DC_LOAD=-2.0, BESS1=20.0, PV1=15.0, GEN1=10.0)
    res = reactive_need(build_campus(spec), rows(hour=hour), ReactiveRequirement(1.0, "c", "code"), residual=False)
    assert res.dispatch["SR1"]["steps"] == steps
    assert abs(res.q_final_mvar) > 1.0 + 0.01                 # neither lands in the band
