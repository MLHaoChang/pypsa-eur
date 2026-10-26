"""The harmonic impedance scan at the PoC (datacenter grid readiness issue 12, part 3)."""
import math

import numpy as np
import pandapower as pp
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import harmonic_scan as hs


def _net(f_hz=50.0, s_sc=1000.0, rx=0.1, caps=(), line_km=None):
    """A grid at 33 kV with capacitor banks ``(q_mvar, steps)`` at the PoC; with ``line_km``, the PoC is
    a cable's far end."""
    net = pp.create_empty_network(sn_mva=100.0, f_hz=f_hz)
    grid = pp.create_bus(net, vn_kv=33.0, name="grid")
    pp.create_ext_grid(net, grid, s_sc_max_mva=s_sc, s_sc_min_mva=s_sc / 2, rx_max=rx, rx_min=rx)
    poc = grid
    if line_km is not None:
        poc = pp.create_bus(net, vn_kv=33.0, name="poc")
        pp.create_line_from_parameters(net, grid, poc, length_km=line_km, r_ohm_per_km=0.1, x_ohm_per_km=0.3,
                                       c_nf_per_km=0.0, max_i_ka=1.0, name="cable")
    for k, (q, steps) in enumerate(caps):
        pp.create_shunt(net, poc, q_mvar=-q / steps, p_mw=0.0, step=0, max_step=steps, name=f"C{k + 1}")
    return net, poc


def _cases(net):
    eg = net.ext_grid.iloc[0]
    return {"max": (float(eg["s_sc_max_mva"]), float(eg["rx_max"])),
            "min": (float(eg["s_sc_min_mva"]), float(eg["rx_min"]))}


@pytest.mark.parametrize("f_hz", [50.0, 60.0])
def test_the_peak_lands_on_the_parallel_resonance(f_hz):
    net, poc = _net(f_hz=f_hz, s_sc=1000.0, rx=0.05, caps=[(10.0, 1)])
    study = hs.impedance_scan(net, poc, {"max": (1000.0, 0.05)})
    h_r = math.sqrt(1000.0 / 10.0)                                  # 10
    res = study.resonance[study.resonance["cap_state"] == "C1=1/1"]
    assert len(res) == 1
    assert abs(res.iloc[0]["h_r"] - h_r) <= hs.DH + 1e-9
    assert res.iloc[0]["f_hz"] == pytest.approx(res.iloc[0]["h_r"] * f_hz)
    scan = study.scan
    assert np.allclose(scan["f_hz"], scan["h"] * f_hz)
    assert set(scan["label"]) == {hs.LABEL} and set(study.resonance["label"]) == {hs.LABEL}


def test_r_grows_with_root_h_and_x_with_h():
    net, poc = _net(s_sc=1e9, rx=0.0, line_km=5.0)                  # a stiff grid: Z_poc is the cable
    study = hs.impedance_scan(net, poc, {"max": (1e9, 0.0)})
    s = study.scan.set_index("h")
    r1, x1 = s.at[1.0, "r_ohm"], s.at[1.0, "x_ohm"]
    assert r1 == pytest.approx(0.5, rel=1e-6) and x1 == pytest.approx(1.5, rel=1e-4)
    for h in (4.0, 9.0, 25.0):
        assert s.at[h, "r_ohm"] / r1 == pytest.approx(math.sqrt(h), rel=1e-6)
        assert s.at[h, "x_ohm"] / x1 == pytest.approx(h, rel=1e-4)


def test_a_flat_response_has_no_resonance_row():
    net, poc = _net(line_km=5.0)
    study = hs.impedance_scan(net, poc, _cases(net))
    assert study.resonance.empty
    assert all(g["z_ohm"].is_monotonic_increasing for _, g in study.scan.groupby("case"))


def test_the_peak_needs_its_prominence():
    z = np.array([1.0, 1.2, 1.4, 1.3, 1.2, 1.3, 3.0, 1.0, 0.9])
    assert [i for i, _ in hs.peaks(z)] == [6]                       # the 1.4 ripple stands 1.17x, not 1.5x


def test_the_weaker_grid_resonates_lower():
    net, poc = _net(s_sc=1000.0, caps=[(10.0, 1)])
    res = hs.impedance_scan(net, poc, _cases(net)).resonance.set_index("case")
    assert res.at["min", "h_r"] < res.at["max", "h_r"]
    assert res.at["min", "h_r"] == pytest.approx(math.sqrt(500.0 / 10.0), abs=hs.DH)


def test_every_step_count_is_scanned_and_a_part_switched_bank_flags():
    # 3 steps of 10 MVAr on 1000 MVA: h_r = sqrt(1000 / 10 s) = 10, 7.07, 5.77. One step sits 3 orders
    # from 7 and 13; two steps 0.07 from 7 (flag); all three 0.77 from 5 (clear)
    net, poc = _net(s_sc=1000.0, rx=0.02, caps=[(30.0, 3)])
    study = hs.impedance_scan(net, poc, {"max": (1000.0, 0.02)}, limit_h=0.5)
    assert sorted(study.scan["cap_state"].unique()) == ["C1=0/3", "C1=1/3", "C1=2/3", "C1=3/3"]
    res = study.resonance.set_index("cap_state")
    assert res.at["C1=2/3", "verdict"] == "flag" and res.at["C1=2/3", "nearest_order"] == 7
    assert res.at["C1=3/3", "verdict"] == "clear"
    assert "C1=0/3" not in res.index
    assert not res["assumed"].any()


def test_too_many_combinations_fall_back_with_a_note():
    net, poc = _net(caps=[(10.0, 4), (10.0, 4), (10.0, 4)])         # 125 > MAX_CAP_STATES
    states, notes = hs.cap_states(hs.switched_shunts(net))
    assert len(states) == 2 + 3 * 4
    assert "MAX_CAP_STATES" in notes[0]
    assert {i: 4 for i in net.shunt.index} in states and {i: 0 for i in net.shunt.index} in states


def test_the_nearest_order_counts_the_unit_spectra():
    assert hs.nearest_order(8.9) == (7, "6k±1")
    assert hs.nearest_order(9.1, spectrum_orders=[9]) == (9, "unit spectrum")
    assert hs.nearest_order(7.2, spectrum_orders=[7]) == (7, "6k±1 and unit spectrum")


@pytest.mark.parametrize("h_r, verdict", [(7.5, "clear"), (7.49, "flag"), (6.5, "clear"), (6.51, "flag")])
def test_the_clearance_boundary(h_r, verdict):
    order, _ = hs.nearest_order(h_r)
    assert hs.judge(abs(h_r - order), 0.5) == verdict


def test_without_a_profile_key_the_limit_is_the_demo_default_and_assumed():
    net, poc = _net(caps=[(10.0, 1)])
    res = hs.impedance_scan(net, poc, {"max": (1000.0, 0.1)}).resonance
    assert set(res["limit_h"]) == {hs.DEFAULT_CLEARANCE_H}
    assert res["assumed"].all() and res["note"].str.contains("demo default").all()
    assert hs.clearance_from_profile({}) == (None, [])
    lim = {"value": 0.3, "clause": "x", "source": "code"}
    assert hs.clearance_from_profile({"harmonic_resonance_clearance_h": lim})[0] == 0.3


def test_the_envelope_holds_every_scan_point():
    net, poc = _net(caps=[(10.0, 2)], line_km=3.0)
    study = hs.impedance_scan(net, poc, _cases(net))
    env = study.envelope.set_index("h")
    assert list(env.index) == list(range(1, hs.H_MAX + 1))
    scan = study.scan
    assert set(scan["cap_state"]) == {"C1=0/2", "C1=1/2", "C1=2/2"} and set(scan["case"]) == {"max", "min"}
    for h in (1, 5, 10, 33, 50):
        near = scan[(scan["h"] >= h - 0.5 - 1e-9) & (scan["h"] <= h + 0.5 + 1e-9)]
        e = env.loc[h]
        assert (near["r_ohm"] >= e["r_min_ohm"]).all() and (near["r_ohm"] <= e["r_max_ohm"]).all()
        assert (near["x_ohm"] >= e["x_min_ohm"]).all() and (near["x_ohm"] <= e["x_max_ohm"]).all()
        assert (near["z_ohm"] <= e["z_max_ohm"]).all()
        assert e["points"] == len(near)


def test_a_long_cable_is_named():
    net, poc = _net(line_km=60.0)
    net.line["c_nf_per_km"] = 250.0
    study = hs.impedance_scan(net, poc, _cases(net))
    assert any("cable cable (60 km)" in n for n in study.notes)
    assert study.envelope["note"].str.contains("lumped pi").all()


def test_a_bad_case_is_refused():
    net, poc = _net()
    with pytest.raises(ContractError, match="S_sc > 0"):
        hs.impedance_scan(net, poc, {"max": (0.0, 0.1)})


def test_cable_capacitance_resonates_with_the_grid():
    # A cable with negligible series impedance: its C (half at each end) acts as one bank of
    # Q_c = U^2 * 2 pi f0 * C * length at the PoC
    net, poc = _net(s_sc=100.0, rx=0.02, line_km=10.0)
    net.line["c_nf_per_km"] = 300.0
    net.line["r_ohm_per_km"], net.line["x_ohm_per_km"] = 1e-5, 1e-4
    q_c = 33.0 ** 2 * 2 * math.pi * 50.0 * 300e-9 * 10.0
    res = hs.impedance_scan(net, poc, {"max": (100.0, 0.02)}).resonance
    assert len(res) == 1
    assert abs(res.iloc[0]["h_r"] - math.sqrt(100.0 / q_c)) <= hs.DH + 1e-9
