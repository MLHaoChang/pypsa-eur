# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
GC-04b: a detailed plant's REPCA1s (one per machine, all on the plant's PoC
branch, gains shared by ``dyr_writer.share_repca1``) give the PoC the
aggregated machine's step response.

The response comes from ``repca1_reduced`` (a reduced REPC_A model, tests
only), run on the records the writer actually wrote, parsed back from the
.dyr text. The tolerance is 1e-6 pu on sum(MBASE), over 20 s, on PoC P and Q
(GK A C4). Each wrong scaling (``MUTANTS``) misses it by more than 1e-2 in
its named case (GK A C3).
"""

import functools

import pandapower as pp
import pytest
import yaml

from gridspine.handoff import dyr_writer
from gridspine.handoff.dyr_writer import (
    PocBranch,
    share_repca1,
    unequal_dispatch,
    write_dyr,
)
from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import (
    REPCA1_CONS,
    REPCA1_ICONS,
    load_unit_params,
    load_unit_templates,
    wecc_params,
)
from tests.gridspine import repca1_reduced as rr
from tests.gridspine.test_dyr_wecc import PLANT, POC, _net, _records, _write
from tests.gridspine.test_dyr_writer import _IBR, TOY_UNITS, _p, _toy_templates

TOL = 1e-6  # pu on sum(MBASE), the GC-04b "matches the aggregate" tolerance
MISS = 1e-2  # a wrong scaling misses the aggregate by more than this
MBASE = {"W_BUS_02": 50.0, "W2": 100.0, "W3": 150.0}
P0 = 0.5  # every machine's initial pu P

#: Aggregate controller of each case (pu on sum(MBASE)): flags and gains
#: chosen so the case's path is live and the limits it names bind.
_BASE = {
    "repc_vcflag": 1,
    "repc_refflag": 1,
    "repc_fflag": 1,
    "repc_tfltr": 0.02,
    "repc_kp": 1.0,
    "repc_ki": 10.0,
    "repc_tft": 0.02,
    "repc_tfv": 0.05,
    "repc_vfrz": 0.0,
    "repc_rc": 0.05,
    "repc_xc": 0.05,
    "repc_kc": 0.05,
    "repc_emax": 0.1,
    "repc_emin": -0.1,
    "repc_dbd1": 0.0,
    "repc_dbd2": 0.0,
    "repc_qmax": 0.44,
    "repc_qmin": -0.44,
    "repc_kpg": 0.25,
    "repc_kig": 1.0,
    "repc_tp": 0.05,
    "repc_fdbd1": -0.0006,
    "repc_fdbd2": 0.0006,
    "repc_femax": 0.1,
    "repc_femin": -0.1,
    "repc_pmax": 1.0,
    "repc_pmin": 0.0,
    "repc_tg": 0.05,
    "repc_ddn": 20.0,
    "repc_dup": 20.0,
}
_Q_MODE = {
    "repc_refflag": 0,
    "repc_kp": 0.5,
    "repc_ki": 2.0,
    "repc_dbd1": -0.05,
    "repc_dbd2": 0.05,
}
#: case -> (aggregate controller, step); femax/femin bind in p_up/p_down,
#: emax/emin and the deadband in q_up/q_down (GK A C2).
CASES = {
    "p_up": (_BASE, {"dpref": 0.2}),
    "p_down": (_BASE, {"dpref": -0.2}),
    "over_frequency": (_BASE, {"df": 0.003}),
    "under_frequency": (_BASE, {"df": -0.003}),
    "v_ldc": (_BASE, {"dvref": 0.02}),
    "v_droop": (_BASE | {"repc_vcflag": 0}, {"dvref": 0.02}),
    "q_up": (_BASE | _Q_MODE, {"dqref": 0.3}),
    "q_down": (_BASE | _Q_MODE, {"dqref": -0.3}),
    "p_limit": (_BASE, {"dpref": 0.6}),
    "q_limit": (_BASE | _Q_MODE, {"dqref": 0.6}),
}
#: case -> the limits that must bind in it, observed in the run (GK A C2)
BINDS = {
    "p_up": {"repc_femax"},
    "p_down": {"repc_femin"},
    "q_up": {"repc_emax"},
    "q_down": {"repc_emin"},
    "p_limit": {"repc_pmax"},
    "q_limit": {"repc_qmax"},
}


def _times(*names, k):
    return lambda v, w: v | {n: v[n] * k(w) for n in names}


#: wrong scaling -> (applied on top of share_repca1, its killing case)
MUTANTS = {
    "kpg_kig_unscaled": (_times("repc_kpg", "repc_kig", k=lambda w: 1 / w), "p_up"),
    "ddn_times_w": (_times("repc_ddn", k=lambda w: w * w), "over_frequency"),
    "dup_times_w": (_times("repc_dup", k=lambda w: w * w), "under_frequency"),
    "v_mode_kp_ki_times_w": (_times("repc_kp", "repc_ki", k=lambda w: w), "v_ldc"),
    "kc_unscaled": (_times("repc_kc", k=lambda w: 1 / w), "v_droop"),
    "rc_unscaled": (_times("repc_rc", k=lambda w: 1 / w), "v_ldc"),
    "xc_unscaled": (_times("repc_xc", k=lambda w: 1 / w), "v_ldc"),
    "q_mode_kp_ki_unscaled": (_times("repc_kp", "repc_ki", k=lambda w: 1 / w), "q_up"),
    "femax_unscaled": (_times("repc_femax", k=lambda w: w), "p_up"),
    "femax_inverted": (_times("repc_femax", k=lambda w: w * w), "p_up"),
    "femin_unscaled": (_times("repc_femin", k=lambda w: w), "p_down"),
    "femin_inverted": (_times("repc_femin", k=lambda w: w * w), "p_down"),
    "emax_unscaled": (_times("repc_emax", k=lambda w: w), "q_up"),
    "emax_inverted": (_times("repc_emax", k=lambda w: w * w), "q_up"),
    "emin_unscaled": (_times("repc_emin", k=lambda w: w), "q_down"),
    "emin_inverted": (_times("repc_emin", k=lambda w: w * w), "q_down"),
    "dbd2_unscaled": (_times("repc_dbd2", k=lambda w: w), "q_up"),
    "dbd2_inverted": (_times("repc_dbd2", k=lambda w: w * w), "q_up"),
    "dbd1_unscaled": (_times("repc_dbd1", k=lambda w: w), "q_down"),
    "dbd1_inverted": (_times("repc_dbd1", k=lambda w: w * w), "q_down"),
}

_SHARES = [m / sum(MBASE.values()) for m in MBASE.values()]


@functools.cache
def _aggregate(case):
    agg, step = CASES[case]
    return rr.simulate([agg], [sum(MBASE.values())], p0=P0, **step)


def _detailed(case, controllers):
    return rr.simulate(controllers, list(MBASE.values()), p0=P0, **CASES[case][1])


# --- the written records ---------------------------------------------------------


def _templates(tmp_path, repca1):
    units = dict(TOY_UNITS)
    for u, m in MBASE.items():
        units[u] = {
            "model": "inverter",
            "mbase_mva": m,
            "include_in_inertia": False,
            "params": {
                "k_sc": _p(1.2, "assumed"),
                "rx_sc": _p(0.1, "assumed"),
                **{k: _p(v, "assumed") for k, v in (_IBR | repca1).items()},
            },
        }
    f = tmp_path / "plant.yaml"
    f.write_text(yaml.safe_dump({"units": units}, sort_keys=False))
    return load_unit_params(f), load_unit_templates(f)


def _plant_net():
    """
    The toy with a three-machine plant (50/100/150 MVA, all at P0 pu) on
    BUS_02 behind the PoC line (line 1, grid end BUS_01).
    """
    net = _net()
    net.sgen.at[0, "p_mw"] = P0 * MBASE["W_BUS_02"]
    for u in ("W2", "W3"):
        pp.create_sgen(
            net, bus=1, p_mw=P0 * MBASE[u], q_mvar=0.0, sn_mva=MBASE[u], name=u
        )
    return net


PLANTS = {"P1": tuple(MBASE)}
PLANT_POC = {u: PocBranch("line", 1, 0) for u in MBASE}


def _written(tmp_path, repca1, net=None, plants=PLANTS, poc=PLANT_POC):
    params, templates = _templates(tmp_path, repca1)
    path = tmp_path / "plant.dyr"
    write_dyr(
        _plant_net() if net is None else net,
        params,
        path,
        ibr=wecc_params(templates),
        poc=poc,
        plants=plants,
    )
    return path.read_text()


def _repca1_fields(text):
    """Every REPCA1 record's fields by name, parsed back from the .dyr."""
    out = []
    for _b, model, _i, toks in _records(text):
        if model != "REPCA1":
            continue
        icons, cons = toks[4:7], toks[7:]
        assert len(cons) == len(REPCA1_CONS)
        out.append(
            {n: int(v) for n, v in zip(REPCA1_ICONS, icons)}
            | {n: float(v) for n, v in zip(REPCA1_CONS, cons)}
        )
    return out


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_written_detailed_plant_matches_the_aggregate_within_1e_6(tmp_path, case):
    written = _repca1_fields(_written(tmp_path, CASES[case][0]))
    assert len(written) == len(MBASE)
    assert rr.deviation(_detailed(case, written), _aggregate(case)) <= 1e-6


@pytest.mark.parametrize("case", ["p_up", "p_down", "q_up", "q_down"])
def test_unshared_records_miss_the_aggregate_by_more_than_1e_2(case):
    """The refusal's reason, measured: N controllers with the aggregate's CONs."""
    agg = CASES[case][0]
    assert rr.deviation(_detailed(case, [agg] * len(MBASE)), _aggregate(case)) > 1e-2


@pytest.mark.parametrize("mutant", sorted(MUTANTS))
def test_each_wrong_scaling_misses_the_aggregate_in_its_case(mutant):
    wrong, case = MUTANTS[mutant]
    agg = CASES[case][0]
    controllers = [wrong(share_repca1(agg, w), w) for w in _SHARES]
    assert rr.deviation(_detailed(case, controllers), _aggregate(case)) > 1e-2


@pytest.mark.parametrize("case", sorted(BINDS))
def test_each_case_binds_its_limits_in_the_aggregate_and_the_detailed_plant(case):
    """
    The limits the scaling rescales (femax/femin, Q-mode emax/emin) and the
    output limits (Pmax, Qmax) bind during the run, observed: otherwise a
    mutant scaling that limit the wrong way would survive (GK A C2).
    """
    detailed = _detailed(case, [share_repca1(CASES[case][0], w) for w in _SHARES])
    assert BINDS[case] <= _aggregate(case)[3]
    assert detailed[3] == _aggregate(case)[3]


def test_the_tolerance_boundary_is_the_literal_1e_6():
    assert TOL == 1e-6 and MISS == 1e-2


# --- share_repca1 ------------------------------------------------------------------


def test_share_one_is_the_identity():
    assert share_repca1(_BASE, 1.0) == _BASE
    assert share_repca1(_BASE | _Q_MODE, 1.0) == _BASE | _Q_MODE


@pytest.mark.parametrize("flags", [{}, _Q_MODE])
def test_share_scales_each_field_by_its_rule(flags):
    agg, w = _BASE | flags, 0.25
    got = share_repca1(agg, w)
    q_mode = bool(flags)
    by_w = {"repc_kpg", "repc_kig", "repc_kc", "repc_rc", "repc_xc"}
    by_inv = {"repc_ddn", "repc_dup", "repc_femax", "repc_femin"}
    if q_mode:
        by_w |= {"repc_kp", "repc_ki"}
        by_inv |= {"repc_dbd1", "repc_dbd2", "repc_emax", "repc_emin"}
    for name, v in agg.items():
        expect = v * w if name in by_w else v / w if name in by_inv else v
        assert got[name] == pytest.approx(expect, rel=1e-15), name


@pytest.mark.parametrize("share", [0.0, -0.5, 1.5])
def test_a_share_outside_0_1_is_refused(share):
    with pytest.raises(ContractError, match="share"):
        share_repca1(_BASE, share)


# --- N = 1 and grouping (GK A C6) -------------------------------------------------------


def test_a_one_machine_plant_writes_todays_aggregate_record_byte_for_byte(tmp_path):
    _, today = _write(tmp_path, PLANT, POC)
    params, templates = _toy_templates(tmp_path, ibr=PLANT)
    path = tmp_path / "one.dyr"
    write_dyr(
        _net(),
        params,
        path,
        ibr=wecc_params(templates),
        poc=POC,
        plants={"P1": ("W_BUS_02",)},
    )
    assert path.read_text() == today


def test_two_machines_on_one_branch_without_a_plant_are_still_refused(tmp_path):
    with pytest.raises(ContractError, match="declare them one detailed plant"):
        _written(tmp_path, _BASE, plants=None)


def test_two_machines_on_one_branch_in_different_plants_are_refused(tmp_path):
    with pytest.raises(ContractError, match="share PoC branch"):
        _written(tmp_path, _BASE, plants={"A": ("W_BUS_02", "W2"), "B": ("W3",)})


def test_a_plant_whose_machines_have_different_poc_branches_is_refused(tmp_path):
    poc = dict(PLANT_POC) | {"W3": PocBranch("line", 0, 0)}
    with pytest.raises(ContractError, match="different PoC branches"):
        _written(tmp_path, _BASE, poc=poc)


def test_a_plant_member_without_a_poc_branch_is_refused(tmp_path):
    poc = {u: b for u, b in PLANT_POC.items() if u != "W3"}
    with pytest.raises(ContractError, match="W3 has no PoC branch"):
        _written(tmp_path, _BASE, poc=poc)


def test_a_unit_in_two_plants_is_refused():
    with pytest.raises(ContractError, match="is in plants"):
        dyr_writer._plant_members(PLANT_POC, {"A": ("W2",), "B": ("W2",)})


def test_the_written_shares_follow_the_raw_mbase(tmp_path):
    fields = _repca1_fields(_written(tmp_path, _BASE))
    assert [f["repc_kpg"] for f in fields] == pytest.approx(
        [_BASE["repc_kpg"] * w for w in _SHARES], rel=1e-7
    )


# --- equal-dispatch assumption (GK A C5) ---------------------------------------------


def test_equal_pu_dispatch_states_no_assumption():
    assert unequal_dispatch(_plant_net(), PLANTS) == []


def test_unequal_pu_dispatch_names_the_plant_and_its_machines():
    net = _plant_net()
    net.sgen.loc[net.sgen["name"] == "W3", "p_mw"] = 0.6 * MBASE["W3"]
    (note,) = unequal_dispatch(net, PLANTS)
    assert "plant 'P1'" in note and "unequal pu P" in note
    assert "W_BUS_02 0.5000" in note and "W3 0.6000" in note
