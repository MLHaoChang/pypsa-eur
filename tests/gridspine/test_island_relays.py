"""Relay sensitivity in the island, I5 (plan 2026-10-07-campus-island-operation).

A relay protects one branch and must see a fault at its ``fault_bus``. In
the island's minimum case its current is:
* each online genset alone, decayed to the relay's operating time
  (instantaneous: I_b = mu * Ik''; time-delayed: Ik = lambda * IrG);
* plus every counted converter at its minimum current, with no decay
  (current-limited), all in phase.
The verdict is "protection blind in island" when that current is below
s * pickup (s = 1.5 by default, assumed).

Hand oracles: blinding by infeed that bypasses the relay; a genset without
field forcing gives a time-delayed relay IrG / x_d,sat whatever the network;
the verdict on both sides of s; linear scaling with the GFM current limit.
"""
import math

import pytest

from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from gridspine.static import island_sc as ISC
from tests.gridspine.test_campus import t
from tests.gridspine.test_island_schema import genset, gfm_bess

IN_20 = 22.0 / (math.sqrt(3) * 20.0)        # rated current of a 22 MVA converter at 20 kV, kA


def feeder(relays, b_unit="gfm", genset_at_a=False, excitation="none", f2_km=1.0):
    """20 kV island: A --F1-- B --F2-- C. A GFM battery (22 MVA, k_gfm_min 1.0)
    at A, another converter at B; or a genset at A instead of the battery."""
    units = {}
    if genset_at_a:
        g = genset()
        g["excitation"], g["xd_sat"] = excitation, t(2.0)
        units["G"] = {"kind": "genset", "bus": "A", "p_mw": t(10.0), "s_mva": t(12.5), "xd_pp": t(0.15),
                      "rx_sc": t(0.07), "island": g}
    else:
        units["BA"] = {"kind": "bess", "bus": "A", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                       "k_sc": t(1.1), "rx_sc": t(0.1), "island": gfm_bess()}
    if b_unit:
        units["BB"] = {"kind": "bess", "bus": "B", "p_mw": t(20.0), "e_mwh": t(20.0), "s_mva": t(22.0),
                       "k_sc": t(1.1), "rx_sc": t(0.1), "island": gfm_bess()}
    cable = lambda f, to, km=1.0: {"from_bus": f, "to_bus": to, "length_km": t(km), "r_ohm_per_km": t(0.1),  # noqa: E731
                           "x_ohm_per_km": t(0.1), "c_nf_per_km": t(0.0), "max_i_ka": t(1.0)}
    return {"campus": {
        "name": "FEEDER", "f_hz": 50,
        "pcc": {"bus": "A", "vn_kv": 20.0, "vm_pu": t(1.0), "sk_max_mva": t(300.0), "sk_min_mva": t(200.0),
                "rx_max": t(0.1), "rx_min": t(0.1)},
        "buses": {"B": {"vn_kv": 20.0}, "C": {"vn_kv": 20.0}},
        "cables": {"F1": cable("A", "B"), "F2": cable("B", "C", f2_km)},
        "units": units,
        "relays": relays,
    }}


def relay(name, branch, fault_bus, pickup, timing="time_delayed", **extra):
    return {"name": name, "branch": branch, "fault_bus": fault_bus, "i_pickup_ka": t(pickup, "datasheet"),
            "timing": timing, **extra}


def row(df, name):
    return df.set_index("relay").loc[name]


def test_an_upstream_relay_sees_only_the_infeed_that_passes_it():
    camp = build_campus(feeder([relay("R1", "F1", "B", 0.1)]))
    r = row(ISC.relay_check(camp), "R1")
    assert r.relay_ka == pytest.approx(1.0 * IN_20, rel=1e-6)           # only BA's current crosses F1
    assert r.fault_total_ka == pytest.approx(2.0 * IN_20, rel=1e-6)     # BB feeds the fault directly
    assert r.bypass_ka == pytest.approx(1.0 * IN_20, rel=1e-6)


def test_the_verdict_on_both_sides_of_s():
    current = 1.0 * IN_20
    sees = row(ISC.relay_check(build_campus(feeder([relay("R", "F1", "B", current / 1.6)]))), "R")
    blind = row(ISC.relay_check(build_campus(feeder([relay("R", "F1", "B", current / 1.4)]))), "R")
    assert sees.verdict == "sees the fault"
    assert blind.verdict == "protection blind in island"


def test_blinding_by_infeed_is_named_when_the_whole_fault_would_be_seen():
    # 2 * IN would be seen at s = 1.5, the relay's own 1 * IN is not
    r = row(ISC.relay_check(build_campus(feeder([relay("R", "F1", "B", 1.2 * IN_20 / 1.5)]))), "R")
    assert r.verdict == "protection blind in island" and "infeed" in r.detail


def test_a_time_delayed_relay_sees_irg_over_xd_sat_from_a_genset_without_field_forcing():
    camp = build_campus(feeder([relay("R", "F2", "C", 0.01)], b_unit=None, genset_at_a=True))
    r = row(ISC.relay_check(camp), "R")
    irg = 12.5 / (math.sqrt(3) * 20.0)
    assert r.relay_ka == pytest.approx(irg / 2.0, rel=1e-6)             # lambda = 1 / x_d,sat = 0.5


def test_with_pmg_field_forcing_the_relay_sees_the_sustained_current_capped_by_the_initial():
    camp = build_campus(feeder([relay("R", "F2", "C", 0.01)], b_unit=None, genset_at_a=True, excitation="avr_pmg"))
    r = row(ISC.relay_check(camp), "R")
    irg = 12.5 / (math.sqrt(3) * 20.0)
    initial = r.initial_ka
    assert r.relay_ka == pytest.approx(min(ISC.PMG_SUSTAINED_PU * irg, initial), rel=1e-6)


def test_an_instantaneous_relay_sees_the_breaking_current_mu_times_ik():
    camp = build_campus(feeder([relay("R", "F2", "C", 0.01, timing="instantaneous", t_min_s=t(0.02))],
                               b_unit=None, genset_at_a=True))
    r = row(ISC.relay_check(camp), "R")
    irg = 12.5 / (math.sqrt(3) * 20.0)
    assert r.relay_ka == pytest.approx(ISC.mu(r.initial_ka / irg, 0.02) * r.initial_ka, rel=1e-6)


def test_the_relay_current_scales_with_the_gfm_current_limit():
    camp = build_campus(feeder([relay("R1", "F1", "B", 0.1)]))
    res = ISC.relay_check(camp, sensitivity=[1.2, 1.5, 2.0])
    r = row(res, "R1")
    for v in (1.2, 1.5, 2.0):
        assert r[f"relay_ka_at_{v:g}"] == pytest.approx(v * IN_20, rel=1e-6)


def test_the_grid_to_island_ratio_is_reported():
    camp = build_campus(feeder([relay("R1", "F1", "B", 0.1)]))
    r = row(ISC.relay_check(camp), "R1")
    assert r.grid_ikss_max_ka > r.relay_ka
    assert r.grid_to_island == pytest.approx(r.grid_ikss_max_ka / r.relay_ka)


def test_with_the_largest_source_out_the_verdict_is_judged_again():
    camp = build_campus(feeder([relay("R1", "F1", "B", 0.1)]))
    r = row(ISC.relay_check(camp), "R1")
    assert r.largest_out in ("BA", "BB")
    # without BA nothing crosses F1; without BB the relay current is unchanged
    assert r.relay_n1_ka == pytest.approx(0.0 if r.largest_out == "BA" else IN_20, abs=1e-9)


@pytest.mark.parametrize("mutate,match", [
    (lambda r: r.update(branch="NOPE"), "NOPE"),
    (lambda r: r.update(fault_bus="ZZ"), "ZZ"),
    (lambda r: r.update(timing="soon"), "timing"),
    (lambda r: r.update(timing="instantaneous", t_min_s=t(0.03)), "t_min_s"),
    (lambda r: r.update(i_pickup_ka=0.1), "i_pickup_ka"),
])
def test_a_malformed_relay_is_refused(mutate, match):
    r = relay("R", "F1", "B", 0.1)
    mutate(r)
    with pytest.raises(ContractError, match=match):
        build_campus(feeder([r]))


def test_relays_are_kept_on_the_campus():
    camp = build_campus(feeder([relay("R1", "F1", "B", 0.1), relay("R2", "F2", "C", 0.05, "instantaneous")]))
    assert list(camp.relays["relay"]) == ["R1", "R2"]
    assert camp.relays.set_index("relay").at["R2", "t_min_s"] == 0.02     # the default minimum time


def test_a_far_fault_that_never_exceeds_the_sustained_current_is_not_raised_by_it():
    """60 km out, the genset's initial current is below 3 x IrG: PMG forcing
    cannot make the steady current larger than the initial one."""
    camp = build_campus(feeder([relay("R", "F2", "C", 0.01)], b_unit=None, genset_at_a=True,
                               excitation="avr_pmg", f2_km=60.0))
    r = row(ISC.relay_check(camp), "R")
    irg = 12.5 / (math.sqrt(3) * 20.0)
    assert r.initial_ka < ISC.PMG_SUSTAINED_PU * irg
    assert r.relay_ka == pytest.approx(r.initial_ka, rel=1e-9)
