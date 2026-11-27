# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""
GC-04b S2: the PSS/E pack for a detailed plant (GK A C5, C7).

The plant is the bundle fixture's PV1 (16.5 MVA) plus PV2 (33 MVA) and PV3
(49.5 MVA) on MV1, behind TR1. Every converter gets a gain-shared REPCA1;
the scripts step every machine's reference by the same reference MW (each
output by the step in its own pu), record each
machine's currents and sum them by MBASE, and poll every machine for
``connected``.
"""

import ast
import copy

import pandas as pd
import pytest

from gridspine.handoff import campus_bundle as CB
from gridspine.handoff import psse_pack as PP
from gridspine.handoff.dyr_writer import PocBranch
from gridspine.handoff.psspy_scripts import write_scripts
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus import t
from tests.gridspine.test_campus_bundle import bundle
from tests.gridspine.test_dyr_wecc import _records
from tests.gridspine.test_island_flow import rows, spec
from tests.gridspine.test_psse_pack import PLANT, _gfl_bess
from tests.gridspine.test_psse_pack import _write as _write_aggregate
from tests.gridspine.test_psspy_connected import (
    _assigned,
    _battery2,
    _helpers,
    _poll_run,
)
from tests.gridspine.test_psspy_scripts import KW

MEMBERS = ("PV1", "PV2", "PV3")
MBASE = {"PV1": 16.5, "PV2": 33.0, "PV3": 49.5}
DETAILED = PP.Plant("PV", PocBranch("trafo", 0, 0), members=MEMBERS)


def _spec(s=None):
    s = copy.deepcopy(s or spec())
    u = s["campus"]["units"]
    for name in MEMBERS[1:]:
        k = MBASE[name] / MBASE["PV1"]
        u[name] = copy.deepcopy(u["PV1"]) | {
            "p_mw": t(15.0 * k),
            "s_mva": t(MBASE[name]),
        }
    return s


def _rows(pv=(10.0, 20.0, 30.0)):
    """The hour: PV1..PV3 at the same pu (10/16.5) unless ``pv`` says otherwise."""
    r = rows(pv=pv[0])
    extra = pd.DataFrame(
        {"unit_id": list(MEMBERS[1:]), "p_mw": list(pv[1:]), "status": [1, 1]}
    )
    return pd.concat([r, extra], ignore_index=True)


def _pack(tmp_path, plant=DETAILED, pv=(10.0, 20.0, 30.0), s=None):
    r = _rows(pv)
    camp, b = bundle(tmp_path / "b", s=_spec(s), r=r)
    hc = CB.dataclasses.replace(camp, net=copy.deepcopy(camp.net))
    CB.apply_hour(hc, r)
    text = (b.path / f"{b.path.name}.dyr").read_text()
    grid = next(x for x in _records(text) if x[1] == "GENCLS")
    out = tmp_path / "pack"
    cases = {c.case_id: c for c in PP._battery_cases(hc.net)[0]}  # the pack's default
    m = PP.write_psse_pack(
        out, hc, b.path.name, text.splitlines(), (grid[0], grid[2]), plant=plant
    )
    return m, out, cases


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    return _pack(tmp_path_factory.mktemp("detailed"))


def _cid(pack, kind):
    return next(c for c in pack[0]["outputs"] if c.startswith(kind))


def _script(pack, kind):
    return (pack[1] / f"run_{_cid(pack, kind)}.py").read_text()


# --- the .dyr --------------------------------------------------------------------------


def test_every_plant_converter_gets_a_repca1_on_the_one_poc_branch(pack):
    m, out, _ = pack
    recs = [
        r for r in _records((out / f"{m['case']}.dyr").read_text()) if r[1] == "REPCA1"
    ]
    assert len(recs) == len(MEMBERS)
    assert len({tuple(r[3][:4]) for r in recs}) == 1  # BUSR BUS1 BUS2 CKT
    # Kpg (CON 16, after the 7 ICONs) follows each machine's MBASE share
    kpg = [float(r[3][7 + 15]) for r in recs]
    assert kpg[1] / kpg[0] == pytest.approx(2.0, rel=1e-6)
    assert kpg[2] / kpg[0] == pytest.approx(3.0, rel=1e-6)


def test_a_plant_of_differing_controllers_is_refused(tmp_path):
    """
    PV1 and a grid-following BESS1 carry different generic REPCA1 sets
    (``repc_bess``): there is no one aggregate controller to share.
    """
    plant = PP.Plant("MIX", PocBranch("trafo", 0, 0), members=("PV1", "BESS1"))
    with pytest.raises(ContractError, match="carry REPCA1 values other than"):
        _pack(tmp_path, plant=plant, s=_gfl_bess())


def test_a_one_member_plant_writes_the_aggregate_pack(tmp_path):
    _, m1, out1 = _write_aggregate(tmp_path / "a", plant=PLANT)
    one = PP.Plant("PV1", PLANT.branch, members=("PV1",))
    _, m2, out2 = _write_aggregate(tmp_path / "b", plant=one)
    assert m1 == m2
    for name in m1["files"]:
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name


# --- steps: every machine, the same reference MW (C7) ------------------------------------------------


class _Calls:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))


def _run_step(text, m_base):
    """
    Run the script's step loop (the ``for`` over PLANT_MACHINES that calls
    increment_*) with the case's MBASEs ``m_base``.
    """
    loops = [
        n
        for n in ast.parse(text).body
        if isinstance(n, ast.For) and "increment_" in ast.unparse(n)
    ]
    assert len(loops) == 1
    fake = _Calls()
    ns = {
        "psspy": fake,
        "PLANT_MACHINES": _assigned(text, "PLANT_MACHINES"),
        "M_BASE": m_base,
        "PLANT_MBASE": sum(m_base),
    }
    exec(compile(ast.Module(loops, []), "step", "exec"), ns)
    return fake.calls


@pytest.mark.parametrize(
    ("kind", "call"), [("p_step", "increment_gref"), ("q_step", "increment_vref")]
)
def test_a_p_or_q_step_moves_every_reference_by_the_same_mw_and_step_pu(
    pack, kind, call
):
    text = _script(pack, kind)
    m_base = [MBASE[u] for u in MEMBERS]
    calls = _run_step(text, m_base)
    machines = _assigned(text, "PLANT_MACHINES")
    assert [c[:3] for c in calls] == [(call, b, i) for b, i in machines]
    step = pack[2][_cid(pack, kind)].params["step_pu"]  # the case's own step
    # each reference moves by the same MW, the aggregate's step on the plant rating ...
    mw = [c[3] * m for c, m in zip(calls, m_base)]
    assert mw == pytest.approx([step * sum(m_base)] * len(MEMBERS), rel=1e-12)
    # ... which is the step / w in each machine's own pu
    assert [c[3] for c in calls] == pytest.approx(
        [step * sum(m_base) / m for m in m_base], rel=1e-12
    )


def test_a_v_step_moves_every_machine_by_the_same_pu_volts(pack):
    text = _script(pack, "v_step")
    calls = _run_step(text, [MBASE[u] for u in MEMBERS])
    step = pack[2][_cid(pack, "v_step")].params["step_pu"]
    assert [c[3] for c in calls] == [step] * len(MEMBERS)  # the case's step, unscaled
    assert {c[0] for c in calls} == {"increment_vref"}


def test_the_scripts_check_the_case_mbase_sum_against_the_plant_rating(pack):
    text = _script(pack, "p_step")
    assert _assigned(text, "(F0_HZ, PLANT_MBASE)")[1] == pytest.approx(
        sum(MBASE.values())
    )
    assert "if abs(sum(M_BASE) - PLANT_MBASE) > 1e-6:" in text


def _run_base_read(text, replies):
    """Run the script's MBASE read and sum check with a fake macdat that
    returns ``replies[k]`` = (ierr, MBASE) for machine k."""
    body = ast.parse(text).body
    keep = [
        n
        for n in body
        if (isinstance(n, ast.Assign) and ast.unparse(n) == "M_BASE = []")
        or (isinstance(n, ast.For) and "macdat" in ast.unparse(n))
        or (isinstance(n, ast.If) and "sum(M_BASE)" in ast.unparse(n))
    ]
    assert len(keep) == 3
    it = iter(replies)

    class _Fake:
        def macdat(self, bus, mid, what):
            assert what == "MBASE"
            return next(it)

    ns = {
        "psspy": _Fake(),
        "PLANT_MACHINES": _assigned(text, "PLANT_MACHINES"),
        "PLANT_MBASE": _assigned(text, "(F0_HZ, PLANT_MBASE)")[1],
    }
    exec(compile(ast.Module(keep, []), "mbase", "exec"), ns)
    return ns["M_BASE"]


def test_the_case_mbase_read_passes_when_the_sum_is_the_rating(pack):
    text = _script(pack, "p_step")
    good = [(0, MBASE[u]) for u in MEMBERS]
    assert _run_base_read(text, good) == [MBASE[u] for u in MEMBERS]


@pytest.mark.parametrize(
    ("replies", "reason"),
    [
        ([(0, 16.5), (0, 33.0), (0, 50.0)], "MBASE sum differs from the plant rating"),
        ([(0, 16.5), (1, 33.0), (0, 49.5)], "plant machine MBASE not read"),
        ([(0, 16.5), (0, 0.0), (0, 49.5)], "plant machine MBASE not read"),
    ],
)
def test_the_run_stops_on_a_wrong_case_mbase(pack, replies, reason):
    with pytest.raises(SystemExit, match=reason):
        _run_base_read(_script(pack, "p_step"), replies)


# --- connected: every machine (C7) ---------------------------------------------------------


def test_the_status_poll_covers_every_plant_machine(pack):
    text = _script(pack, "flat")
    assert _assigned(text, "STATUS_MACHINES") == _assigned(text, "PLANT_MACHINES")
    assert len(_assigned(text, "STATUS_MACHINES")) == len(MEMBERS)


def test_three_machines_one_tripping_reads_0(pack):
    text = _script(pack, "fault_3ph")
    three = [tuple(x) for x in _assigned(text, "STATUS_MACHINES")]
    ok = {"branch": (0, 1)} | {mc: (0, 1) for mc in three}
    trip = ok | {three[1]: (0, 0)}
    polls, fake = _poll_run(text, [ok, trip], three)
    assert polls["v"] == [1, 0] and polls["why"] == []
    assert sum(c[0] == "macint" for c in fake.calls) == 2 * len(three)


# --- currents: MBASE-weighted sums ----------------------------------------------------------


def test_the_currents_are_the_mbase_weighted_sum_on_the_plant_rating(pack):
    text = _script(pack, "v_step")
    assert "raw_iq_pu' + '_%d' % k" in text
    m_base = [MBASE[u] for u in MEMBERS]
    iq = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    rec = {
        "t_s": [0.0, 1.0],
        "raw_v_poc_pu": [1.0, 1.0],
        "raw_f_hz": [0.0, 0.0],
        "raw_p_poc_mw": [0.0, 0.0],
        "raw_q_poc_mvar": [0.0, 0.0],
        **{f"raw_iq_pu_{k}": v for k, v in enumerate(iq)},
        "poll_t": [0.0],
        "poll_v": [1],
    }
    out = _helpers(text)["_convert"](rec, 50.0, sum(m_base), m_base)
    want = [sum(iq[k][j] * m_base[k] for k in range(3)) / sum(m_base) for j in range(2)]
    assert out["iq_pu"] == pytest.approx(want, rel=1e-12)


# --- equal-dispatch assumption (C5) ---------------------------------------------------------


def test_an_equal_pu_dispatch_states_no_plant_assumption(pack):
    m, out, _ = pack
    assert "plant_assumed" not in m
    assert "unequal pu" not in (out / "README.md").read_text()


def test_an_unequal_pu_dispatch_is_listed_with_its_machines(tmp_path):
    m, out, _ = _pack(tmp_path, pv=(10.0, 20.0, 40.0))
    (note,) = m["plant_assumed"]
    assert "plant 'PV'" in note and "unequal pu P" in note
    assert all(u in note for u in MEMBERS)
    assert f"- {note}" in (out / "README.md").read_text()


# --- write_scripts' own checks --------------------------------------------------------------


_FLAT = [next(c for c in _battery2() if c.kind == "flat")]
_TWO = [(201, "1"), (202, "1")]


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"plant_machines": []}, "plant_machines is empty"),
        ({"plant_machines": [(201, "1"), (201, "1")]}, "repeat a machine"),
        (
            {"plant_machines": [(202, "1"), (201, "1")]},
            "is not the first of plant_machines",
        ),
        ({"plant_machines": _TWO, "plant_mbase_mva": None}, "need plant_mbase_mva"),
        ({"plant_machines": [(201, "toolong")]}, "1 or 2 characters"),
    ],
)
def test_bad_plant_machines_are_refused(tmp_path, kw, reason):
    args = KW | {"plant_machine": (201, "1")} | kw
    with pytest.raises(ContractError, match=reason):
        write_scripts(_FLAT, str(tmp_path), **args, contract_version="2")
