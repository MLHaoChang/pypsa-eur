# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The sub-synchronous impedance scan (grid-compliance GC-08, TRGC 20)."""

import math

import numpy as np
import pandapower as pp
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.static import harmonic_scan as hs
from gridspine.static import subsync_scan as ss

# 110 kV, S_sc 1210 MVA with R/X 0 and c = 1 (normal case): X_grid = 110^2 / 1210 = 10 ohm.
# The line adds 0.5 + j40 ohm, so the loop reactance at f0 is 50 ohm.
X_LOOP = 50.0
CASE = {"normal": (1210.0, 0.0)}


def _radial(f_hz=50):
    net = pp.create_empty_network(sn_mva=100, f_hz=f_hz)
    b0, b1, b2 = (pp.create_bus(net, vn_kv=110.0) for _ in range(3))
    pp.create_ext_grid(
        net, b0, s_sc_max_mva=1210.0, s_sc_min_mva=1210.0, rx_max=0.0, rx_min=0.0
    )
    pp.create_line_from_parameters(
        net,
        b0,
        b1,
        length_km=1.0,
        r_ohm_per_km=0.5,
        x_ohm_per_km=40.0,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
    )
    return net, b1, b2


def _series(kinds):
    return kinds[kinds["kind"] == ss.SERIES]


@pytest.mark.parametrize(
    ("k", "f_er"), [(0.3, 27.386127875), (0.5, 35.355339059), (0.7, 41.833001327)]
)
@pytest.mark.parametrize("df", [0.1, 1.0])
def test_series_capacitor_resonance_at_f0_sqrt_k(k, f_er, df):
    net, b1, b2 = _radial()
    study = ss.subsync_scan(net, b2, CASE, series_caps=[(b1, b2, k * X_LOOP)], df=df)
    res = _series(study.resonance)
    assert len(res) == 1
    assert abs(res["f_er_hz"].iloc[0] - f_er) <= df
    assert res["f_comp_hz"].iloc[0] == pytest.approx(50.0 - res["f_er_hz"].iloc[0])
    assert res["r_ohm"].iloc[0] == pytest.approx(0.5, abs=1e-9)
    assert res["verdict"].iloc[0] == "check"
    assert ss.NO_MODES in res["note"].iloc[0]


def test_scan_range_and_constant_r():
    net, b1, b2 = _radial()
    study = ss.subsync_scan(net, b2, CASE, series_caps=[(b1, b2, 25.0)], df=1.0)
    assert study.scan["f_hz"].iloc[0] == 5.0
    assert study.scan["f_hz"].iloc[-1] == 45.0
    assert len(study.scan) == 41
    # no skin term below f0: R stays the line's 0.5 ohm; X = 50 h - 25 / h
    assert np.allclose(study.scan["r_ohm"], 0.5)
    h = study.scan["f_hz"].to_numpy() / 50.0
    assert np.allclose(study.scan["x_ohm"], X_LOOP * h - 25.0 / h)
    assert set(study.scan["label"]) == {"screening; binding answer is the EMT study"}


def test_no_series_capacitor_not_evaluated_never_clear():
    net, _, b2 = _radial()
    pp.create_line_from_parameters(
        net,
        1,
        b2,
        length_km=1.0,
        r_ohm_per_km=0.1,
        x_ohm_per_km=1.0,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
    )
    study = ss.subsync_scan(net, b2, CASE, torsional_modes={"G": [20.0]})
    res = _series(study.resonance)
    assert res["f_er_hz"].isna().all()
    assert list(res["verdict"]) == ["not evaluated"]
    assert ss.NO_SERIES_CAP in res["note"].iloc[0]
    assert "clear" not in set(study.resonance["verdict"])


def test_ssci_row_per_case_not_evaluated():
    net, b1, b2 = _radial()
    study = ss.subsync_scan(
        net,
        b2,
        {"max": (1210.0, 0.0), "min": (800.0, 0.1)},
        series_caps=[(b1, b2, 25.0)],
    )
    ssci = study.resonance[study.resonance["kind"] == ss.SSCI]
    assert list(ssci["case"]) == ["max", "min"]
    assert set(ssci["verdict"]) == {"not evaluated"}
    assert set(ssci["note"]) == {
        "not evaluated: converter damping not modelled; binding answer is the EMT study"
    }


def test_two_series_capacitors_on_parallel_paths_give_two_crossings():
    """Two series-LC paths in parallel: X has a zero for each path's resonance, with a pole between."""
    net = pp.create_empty_network(sn_mva=100, f_hz=50)
    g, a, b, poc = (pp.create_bus(net, vn_kv=110.0) for _ in range(4))
    pp.create_ext_grid(
        net, g, s_sc_max_mva=1e7, s_sc_min_mva=1e7, rx_max=0.0, rx_min=0.0
    )
    for bus, x in ((a, 40.0), (b, 60.0)):
        pp.create_line_from_parameters(
            net,
            g,
            bus,
            length_km=1.0,
            r_ohm_per_km=0.2,
            x_ohm_per_km=x,
            c_nf_per_km=0.0,
            max_i_ka=1.0,
        )
    study = ss.subsync_scan(
        net, poc, CASE, series_caps=[(a, poc, 0.2 * 40.0), (b, poc, 0.6 * 60.0)]
    )
    f = sorted(_series(study.resonance)["f_er_hz"])
    assert len(f) == 2
    assert 5.0 < f[0] < f[1] < 45.0


def test_torsional_margin_uses_complement():
    """
    f_er = 50 sqrt(0.5) = 35.355 Hz, complement 14.645 Hz. Modes 14.0 (G1) and 35.0 (G2): the
    complement is 0.645 Hz from G1's mode (clear at 0.5 Hz); f_er itself would be 0.355 Hz from G2's.
    """
    net, b1, b2 = _radial()
    study = ss.subsync_scan(
        net,
        b2,
        CASE,
        series_caps=[(b1, b2, 25.0)],
        torsional_modes={"G1": [14.0], "G2": [35.0]},
        clearance_hz=0.5,
    )
    row = _series(study.resonance).iloc[0]
    assert row["machine"] == "G1"
    assert row["mode_hz"] == 14.0
    assert row["margin_hz"] == pytest.approx(
        50.0 - 50.0 * math.sqrt(0.5) - 14.0, abs=0.01
    )
    assert row["verdict"] == "clear"
    assert not bool(row["assumed"])
    assert ss.NO_MODES not in row["note"]
    tight = ss.subsync_scan(
        net,
        b2,
        CASE,
        series_caps=[(b1, b2, 25.0)],
        torsional_modes={"G1": [14.0], "G2": [35.0]},
        clearance_hz=0.7,
    )
    assert _series(tight.resonance)["verdict"].iloc[0] == "flag"


def test_judge_states_and_edge():
    assert ss.judge(None, 1.0) == "check"
    assert ss.judge(1.0, 1.0) == "clear"
    assert ss.judge(0.999999, 1.0) == "flag"
    assert ss.judge(2.0, 1.0) == "clear"


def test_no_clearance_takes_default_assumed():
    net, b1, b2 = _radial()
    study = ss.subsync_scan(
        net, b2, CASE, series_caps=[(b1, b2, 25.0)], torsional_modes={"G": [14.0]}
    )
    row = _series(study.resonance).iloc[0]
    assert row["clearance_hz"] == 1.0
    assert bool(row["assumed"])
    assert "the grid code sets no torsional clearance; demo default" in row["note"]
    assert row["verdict"] == "flag"  # 0.645 Hz < 1.0 Hz
    marked = ss.subsync_scan(
        net,
        b2,
        CASE,
        series_caps=[(b1, b2, 25.0)],
        clearance_hz=0.5,
        clearance_assumed=True,
    )
    assert _series(marked.resonance)["assumed"].all()


def test_clearance_from_profile():
    assert ss.clearance_from_profile(None) == (None, [])
    v, cited = ss.clearance_from_profile(
        {
            "subsync_torsional_clearance_hz": {
                "value": 2.0,
                "clause": "TRGC 20",
                "source": "code",
            }
        }
    )
    assert (v, cited) == (
        2.0,
        [
            {
                "key": "subsync_torsional_clearance_hz",
                "clause": "TRGC 20",
                "source": "code",
            }
        ],
    )


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"series_caps": [(1, 2, 0.0)]}, "needs Xc > 0"),
        ({"series_caps": [(1, 2, math.inf)]}, "Xc must be finite"),
        ({"series_caps": [(1, 99, 10.0)]}, "not between in-service buses"),
        ({"torsional_modes": {"G": [50.0]}}, r"must lie in \(0, 50.0\)"),
        ({"torsional_modes": {"G": [math.inf]}}, "torsional mode of G must be finite"),
        ({"clearance_hz": 0.0}, "clearance must be > 0"),
        ({"clearance_hz": math.inf}, "clearance must be finite"),
        ({"df": 0.0}, "need df > 0"),
    ],
)
def test_refuses(kw, reason):
    net, _, b2 = _radial()
    with pytest.raises(ContractError, match=reason):
        ss.subsync_scan(net, b2, CASE, **kw)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ((0.0, 0.1), "needs S_sc > 0"),
        ((1000.0, -0.1), "R/X >= 0"),
        ((math.inf, 0.1), "S_sc of case x must be finite"),
        ((1000.0, None), "R/X of case x must be a number"),
    ],
)
def test_refuses_bad_case(case, reason):
    net, _, b2 = _radial()
    with pytest.raises(ContractError, match=reason):
        ss.subsync_scan(net, b2, {"x": case})


def test_refuses_low_f0():
    with pytest.raises(ContractError, match="f0 >= 10"):
        ss.frequencies(9.0)


def test_harmonic_scan_branches_promoted_without_change():
    assert hs._branches is hs.branches


# --- the network elements, each against an independent hand formula --------------------------

FS = np.array([10.0, 20.0, 30.0, 40.0])
H = FS / 50.0


def test_grid_c_and_rx_by_case():
    """Max case: X_grid = c Un^2 / S_sc / sqrt(1 + rx^2) = 1.1 * 10 / sqrt(1.01) ohm, R_grid = rx X_grid."""
    net, b1, b2 = _radial()
    xg = 1.1 * 10.0 / math.sqrt(1.01)
    z = ss.z_poc(net, b2, 1210.0, 0.1, FS, [(b1, b2, 25.0)], "max")
    assert np.allclose(z, (0.1 * xg + 0.5) + 1j * ((xg + 40.0) * H - 25.0 / H))


def test_z_poc_builds_its_own_branches():
    net, b1, b2 = _radial()
    study = ss.subsync_scan(net, b2, CASE, series_caps=[(b1, b2, 25.0)], df=10.0)
    z = ss.z_poc(net, b2, 1210.0, 0.0, study.scan["f_hz"].to_numpy(), [(b1, b2, 25.0)])
    assert np.allclose(z, study.scan["r_ohm"] + 1j * study.scan["x_ohm"])


def test_cable_charging_pi():
    """One pi line from the grid bus: B/2 at each end, B = 2 pi f0 C l (here 1e-3 S at f0)."""
    net = pp.create_empty_network(sn_mva=100, f_hz=50)
    g, poc = pp.create_bus(net, vn_kv=110.0), pp.create_bus(net, vn_kv=110.0)
    pp.create_ext_grid(
        net, g, s_sc_max_mva=1210.0, s_sc_min_mva=1210.0, rx_max=0.0, rx_min=0.0
    )
    c_nf = 1e-3 / (2 * math.pi * 50) * 1e9
    pp.create_line_from_parameters(
        net,
        g,
        poc,
        length_km=1.0,
        r_ohm_per_km=0.5,
        x_ohm_per_km=40.0,
        c_nf_per_km=c_nf,
        max_i_ka=1.0,
    )
    yc = 0.5j * H * 1e-3
    z0 = 1.0 / (1.0 / (1j * 10.0 * H) + yc)
    expect = 1.0 / (yc + 1.0 / (0.5 + 1j * 40.0 * H + z0))
    assert np.allclose(ss.z_poc(net, poc, 1210.0, 0.0, FS), expect)


@pytest.mark.parametrize("q_mvar", [-5.0, 5.0])
def test_shunt_bank_all_steps_in(q_mvar):
    """A bank of 2 steps of q (load convention) and 0.1 MW at the PoC: Y = (P - j Q h^+-1) / Un^2."""
    net, b1, b2 = _radial()
    pp.create_shunt(net, b2, q_mvar=q_mvar, p_mw=0.1, step=1, max_step=2)
    q = 2 * q_mvar
    yb = (0.2 - 1j * q * (H if q < 0 else 1.0 / H)) / 110.0**2
    zs = 0.5 + 1j * (X_LOOP * H - 25.0 / H)
    assert np.allclose(
        ss.z_poc(net, b2, 1210.0, 0.0, FS, [(b1, b2, 25.0)]), 1.0 / (1.0 / zs + yb)
    )


def test_elements_off_the_poc_are_left_out():
    """An island with its own grid and line, and lines to an out-of-service bus, change nothing."""
    net, b1, b2 = _radial()
    base = ss.z_poc(net, b2, 1210.0, 0.0, FS, [(b1, b2, 25.0)])
    i1, i2 = pp.create_bus(net, vn_kv=110.0), pp.create_bus(net, vn_kv=110.0)
    pp.create_ext_grid(
        net, i1, s_sc_max_mva=500.0, s_sc_min_mva=500.0, rx_max=0.1, rx_min=0.1
    )
    pp.create_line_from_parameters(
        net,
        i1,
        i2,
        length_km=1.0,
        r_ohm_per_km=1.0,
        x_ohm_per_km=5.0,
        c_nf_per_km=0.0,
        max_i_ka=1.0,
    )
    off = pp.create_bus(net, vn_kv=110.0, in_service=False)
    for a, b in ((b2, off), (off, b2)):
        pp.create_line_from_parameters(
            net,
            a,
            b,
            length_km=1.0,
            r_ohm_per_km=1.0,
            x_ohm_per_km=5.0,
            c_nf_per_km=0.0,
            max_i_ka=1.0,
        )
    assert np.allclose(ss.z_poc(net, b2, 1210.0, 0.0, FS, [(b1, b2, 25.0)]), base)
    with pytest.raises(ContractError, match="not between in-service buses"):
        ss.z_poc(net, b2, 1210.0, 0.0, FS, [(off, b2, 25.0)])


def test_refuses_poc_out_of_service_and_no_grid():
    net, b1, b2 = _radial()
    net.bus.loc[b2, "in_service"] = False
    with pytest.raises(ContractError, match="PoC bus .* is not an in-service bus"):
        ss.z_poc(net, b2, 1210.0, 0.0, FS)
    net, b1, b2 = _radial()
    net.ext_grid["in_service"] = False
    with pytest.raises(ContractError, match="no path to the grid"):
        ss.z_poc(net, b2, 1210.0, 0.0, FS, [(b1, b2, 25.0)])


def test_crossings_at_a_scan_point_counted_once_and_interpolated():
    fs = np.array([1.0, 2.0, 3.0])
    assert ss.crossings(fs, np.array([1 - 1j, 3 + 0j, 5 + 1j])) == [(2.0, 3.0)]
    assert ss.crossings(fs, np.array([1 - 1j, 3 + 1j, 5 + 3j])) == [(1.5, 2.0)]
    assert ss.crossings(fs, np.array([1 + 1j, 3 - 1j, 5 - 3j])) == []


def test_lowest_f0_scans_one_point_and_mode_at_zero_refused():
    assert list(ss.frequencies(10.0)) == [5.0]
    net, _, b2 = _radial()
    with pytest.raises(ContractError, match="must lie in"):
        ss.subsync_scan(net, b2, CASE, torsional_modes={"G": [0.0]})


def test_long_line_note_judged_at_f0():
    """The lumped-pi note is about the scanned range (up to f0), not the harmonic range."""
    net, b1, b2 = _radial()
    net.line["c_nf_per_km"] = 10.0
    net.line["length_km"] = (
        5.0  # past 0.4 of a quarter wave at the harmonic top, far short at f0
    )
    assert ss.subsync_scan(net, b2, CASE, series_caps=[(b1, b2, 25.0)]).notes == []
    net.line["length_km"] = 2000.0
    study = ss.subsync_scan(net, b2, CASE, series_caps=[(b1, b2, 25.0)])
    assert len(study.notes) == 1 and "quarter wavelength at 50 Hz" in study.notes[0]
    assert all(
        study.notes[0] in n
        for n in study.resonance.loc[study.resonance["kind"] == ss.SERIES, "note"]
    )


def test_a_grid_on_another_island_is_not_the_poc_grid():
    net, b1, b2 = _radial()
    net.ext_grid["in_service"] = False
    i1 = pp.create_bus(net, vn_kv=110.0)
    pp.create_ext_grid(
        net, i1, s_sc_max_mva=500.0, s_sc_min_mva=500.0, rx_max=0.1, rx_min=0.1
    )
    with pytest.raises(ContractError, match="no path to the grid"):
        ss.z_poc(net, b2, 1210.0, 0.0, FS, [(b1, b2, 25.0)])
