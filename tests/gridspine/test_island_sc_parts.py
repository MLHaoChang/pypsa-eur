"""The parts of the island short circuit, I5 (plan 2026-10-07-campus-island-operation).

* ``current_source_fault``: a bolted fault fed only by current sources
  through a passive network. Every injected ampere ends in the fault (KCL,
  no shunts), so Ik'' = Σ I_i referred to the fault bus voltage; in a radial
  network each branch carries the sources beyond it. Both by hand.
* ``mu``: the IEC 60909-0 decay of a synchronous machine's current to the
  symmetrical breaking current I_b = μ·Ik''; μ = 1 when Ik''/IrG ≤ 2.
* ``lam``: the steady-state current a time-delayed relay sees,
  Ik = λ·IrG. Without field forcing (constant no-load excitation) λ is
  1/x_d,sat; with PMG field forcing it is the sustained current the
  datasheet states.
"""
import math

import pandapower as pp
import pytest

from gridspine.static import island_sc as ISC


def radial():
    """A 20 kV feeder A - B - C, with a 20/0.4 kV transformer from C to LV."""
    net = pp.create_empty_network(sn_mva=1.0)
    a, b, c = (pp.create_bus(net, 20.0, name=n) for n in "ABC")
    lv = pp.create_bus(net, 0.4, name="LV")
    for f, t, name in ((a, b, "AB"), (b, c, "BC")):
        pp.create_line_from_parameters(net, f, t, 1.0, 0.1, 0.1, 0.0, 1.0, name=name)
    pp.create_transformer_from_parameters(net, c, lv, sn_mva=1.0, vn_hv_kv=20.0, vn_lv_kv=0.4,
                                          vk_percent=6.0, vkr_percent=1.0, pfe_kw=0.0, i0_percent=0.0, name="T")
    return net, {n: i for n, i in zip("ABC", (a, b, c))} | {"LV": lv}


def test_all_injected_current_ends_in_the_fault_referred_to_its_voltage():
    net, bus = radial()
    # 0.1 kA at A and 0.2 kA at B (20 kV), 1.0 kA at LV (0.4 kV = 0.02 kA at 20 kV)
    res = ISC.current_source_fault(net, bus["C"], {bus["A"]: 0.1, bus["B"]: 0.2, bus["LV"]: 1.0})
    assert res.ik_ka == pytest.approx(0.1 + 0.2 + 1.0 * 0.4 / 20.0, rel=1e-9)


def test_each_radial_branch_carries_the_sources_beyond_it():
    net, bus = radial()
    res = ISC.current_source_fault(net, bus["C"], {bus["A"]: 0.1, bus["B"]: 0.2, bus["LV"]: 1.0})
    br = res.branch.set_index("branch")
    assert br.at["AB", "i_ka"] == pytest.approx(0.1, rel=1e-6)
    assert br.at["BC", "i_ka"] == pytest.approx(0.3, rel=1e-6)
    assert br.at["T", "i_ka"] == pytest.approx(1.0 * 0.4 / 20.0, rel=1e-6)     # on its HV side


def test_a_fault_at_the_source_bus_takes_only_what_is_injected_there_and_beyond():
    net, bus = radial()
    res = ISC.current_source_fault(net, bus["A"], {bus["A"]: 0.1, bus["B"]: 0.2})
    assert res.ik_ka == pytest.approx(0.3)
    assert res.branch.set_index("branch").at["BC", "i_ka"] == pytest.approx(0.0, abs=1e-9)


def test_parallel_paths_split_by_impedance():
    net, bus = radial()
    pp.create_line_from_parameters(net, bus["A"], bus["B"], 3.0, 0.1, 0.1, 0.0, 1.0, name="AB2")
    res = ISC.current_source_fault(net, bus["B"], {bus["A"]: 0.4})
    br = res.branch.set_index("branch")
    # 1 km and 3 km of the same cable: the shorter carries 3/4
    assert br.at["AB", "i_ka"] == pytest.approx(0.3, rel=1e-6)
    assert br.at["AB2", "i_ka"] == pytest.approx(0.1, rel=1e-6)


@pytest.mark.parametrize("x, t, expected", [
    (1.5, 0.02, 1.0),                                   # Ik''/IrG <= 2: no decay counted
    (1.5, 0.25, 1.0),                                   # (the formula alone would give 1.09)
    (5.0, 0.02, 0.84 + 0.26 * math.exp(-0.26 * 5.0)),
    (5.0, 0.05, 0.71 + 0.51 * math.exp(-0.30 * 5.0)),
    (5.0, 0.10, 0.62 + 0.72 * math.exp(-0.32 * 5.0)),
    (5.0, 0.25, 0.56 + 0.94 * math.exp(-0.38 * 5.0)),
    (0.5 / 0.09, 0.02, 0.84 + 0.26 * math.exp(-0.26 * 0.5 / 0.09)),
])
def test_mu_follows_the_iec_formula(x, t, expected):
    assert ISC.mu(x, t) == pytest.approx(expected, rel=1e-12)


def test_mu_refuses_a_minimum_time_it_has_no_formula_for():
    with pytest.raises(ValueError, match="t_min"):
        ISC.mu(5.0, 0.03)


def test_lambda_without_field_forcing_is_one_over_xd_sat():
    assert ISC.lam("none", xd_sat=2.0) == pytest.approx(0.5)
    assert ISC.lam("avr_shunt", xd_sat=1.8) == pytest.approx(1.0 / 1.8)


def test_lambda_with_pmg_field_forcing_is_the_sustained_current():
    assert ISC.lam("avr_pmg", xd_sat=2.0, ik_sustained_pu=2.5) == pytest.approx(2.5)
    assert ISC.lam("avr_pmg", xd_sat=2.0) == pytest.approx(ISC.PMG_SUSTAINED_PU)


def test_a_source_in_a_part_cut_off_from_the_fault_contributes_nothing():
    net, bus = radial()
    net.line.loc[net.line["name"] == "AB", "in_service"] = False
    res = ISC.current_source_fault(net, bus["C"], {bus["A"]: 0.1, bus["B"]: 0.2})
    assert res.ik_ka == pytest.approx(0.2)


def test_mu_is_continuous_at_the_threshold():
    for t_min in ISC.MU_COEFFICIENTS:
        assert ISC.mu(2.0 + 1e-9, t_min) == pytest.approx(1.0, abs=0.011)
        assert ISC.mu(2.0 + 1e-9, t_min) <= 1.0


def test_parallel_runs_of_a_line_divide_its_impedance():
    net, bus = radial()
    net.line.loc[net.line["name"] == "AB", "parallel"] = 2
    pp.create_line_from_parameters(net, bus["A"], bus["B"], 3.0, 0.1, 0.1, 0.0, 1.0, name="AB2")
    res = ISC.current_source_fault(net, bus["B"], {bus["A"]: 0.7})
    br = res.branch.set_index("branch")
    # two 1 km runs against one 3 km run: admittances 2 and 1/3, so 6/7 and 1/7
    assert br.at["AB", "i_ka"] == pytest.approx(0.6, rel=1e-6)
    assert br.at["AB2", "i_ka"] == pytest.approx(0.1, rel=1e-6)


def test_parallel_transformers_split_by_their_complex_impedance():
    net, bus = radial()
    pp.create_transformer_from_parameters(net, bus["C"], bus["LV"], sn_mva=1.0, vn_hv_kv=20.0, vn_lv_kv=0.4,
                                          vk_percent=6.0, vkr_percent=4.0, pfe_kw=0.0, i0_percent=0.0, name="T2")
    res = ISC.current_source_fault(net, bus["C"], {bus["LV"]: 1.0})
    br = res.branch.set_index("branch")
    z1 = complex(0.01, math.sqrt(0.06 ** 2 - 0.01 ** 2))
    z2 = complex(0.04, math.sqrt(0.06 ** 2 - 0.04 ** 2))
    share1 = abs((1 / z1) / (1 / z1 + 1 / z2))
    assert br.at["T", "i_ka"] == pytest.approx(share1 * 1.0 * 0.4 / 20.0, rel=1e-6)
