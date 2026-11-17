"""
GC-04 item 6: psspy run scripts per battery case, checked with ``ast`` only
(no psspy in CI; GK condition S1). Each refusal test asserts its own reason.
"""

import ast
import math
import os

import pytest

from gridspine.handoff.psspy_scripts import (
    EVENT_CALLS,
    PENDING,
    check_script,
    write_scripts,
)
from gridspine.handoff.test_battery import KIND_PARAMS, build_battery
from gridspine.schema.contracts import ContractError

FILES = {"raw": "pack.raw", "seq": "pack.seq", "dyr": "pack.dyr"}
KW = dict(
    **FILES,
    poc_bus=101,
    poc_kv=132.0,
    grid_machine=(900, "1"),
    grid_mbase_mva=100.0,
    plant_branch=(101, 900, "1"),
    plant_machine=(201, "1"),
)


def _battery(overrides=None):
    return build_battery(
        300.0, 1000.0, 3.0, 10.0, z0_over_z1=1.5, x0_over_r0=4.0, overrides=overrides
    )


def _allowed(cid):
    return set(FILES.values()) | {f"{cid}.out", f"{cid}.csv"}


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    cases = _battery(
        {"fault_3ph_skmin_xrmin_v020": {"duration_s": 0.25, "t_event_s": 0.5}}
    )
    d = tmp_path_factory.mktemp("pack")
    out = write_scripts(cases, str(d), **KW)
    texts = {cid: (d / name).read_text(encoding="utf-8") for cid, name in out.items()}
    return {c.case_id: c for c in cases}, texts


def test_one_script_per_case_and_every_kind(pack):
    cases, texts = pack
    assert set(texts) == set(cases)
    assert {c.kind for c in cases.values()} == set(KIND_PARAMS) == set(EVENT_CALLS)


def test_every_script_compiles_and_passes_the_checker(pack):
    cases, texts = pack
    for cid, text in texts.items():
        compile(text, f"run_{cid}.py", "exec")
        files = check_script(text, _allowed(cid), cases[cid].channels)
        assert files == _allowed(cid)


def test_docstring_names_case_kind_trgc_corner_and_pending(pack):
    cases, texts = pack
    for cid, text in texts.items():
        doc = ast.get_docstring(ast.parse(text))
        c = cases[cid]
        for part in (cid, c.kind, f"TRGC {c.trgc}", c.grid.label, PENDING):
            assert part in doc


def _literals(text):
    return {
        n.value
        for n in ast.walk(ast.parse(text))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_every_channel_is_an_ident_literal(pack):
    cases, texts = pack
    for cid, text in texts.items():
        assert set(cases[cid].channels) <= _literals(text)


def test_override_reaches_the_script(pack):
    _, texts = pack
    text = texts["fault_3ph_skmin_xrmin_v020"]
    assert "T_EVENT, T_END = 0.5, 10.0" in text
    assert "T_EVENT + 0.25" in text


def test_files_resolve_against_the_script_dir(pack):
    _, texts = pack
    text = texts["flat_skmin_xrmin"]
    assert "os.path.dirname(os.path.abspath(__file__))" in text
    assert "os.path.join(HERE, 'pack.raw')" in text


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda t: t.replace("'pack.dyr'", "'other.dyr'"), "'other.dyr' is not in the pack"),
        (lambda t: t.replace("'pack.raw'", "'/tmp/pack.raw'"), "absolute path literal"),
        (lambda t: t.replace("import os\n", "import socket\nimport os\n"), "import of 'socket'"),
        (lambda t: t.replace("from os import path", "") + "\nfrom os import system\n", "from os"),
        (lambda t: t + "\nopen(OUT)\n", "call to open"),
        (lambda t: t + "\neval('1')\n", "call to eval"),
        (lambda t: t + "\nexec('1')\n", "call to exec"),
        (lambda t: t + "\n__import__('x')\n", "call to __import__"),
        (lambda t: t + "\nos.system('x')\n", "os call outside os.path"),
        (lambda t: t + "\nimport subprocess\n", "import of 'subprocess'"),
        (lambda t: t.replace("'f_hz'", "'f'"), "channel 'f_hz' has no ident literal"),
    ],
)
def test_checker_refuses_a_tampered_script(pack, mutate, reason):
    cases, texts = pack
    cid = "flat_skmin_xrmin"
    with pytest.raises(ContractError, match=reason.replace("(", r"\(")):
        check_script(mutate(texts[cid]), _allowed(cid), cases[cid].channels)


def test_thevenin_pu_matches_hand_calculation(pack):
    _, texts = pack
    # Sk 1000 MVA, X/R 10, MBASE 100 MVA: |Z| = 100/1000 = 0.1 pu,
    # R = 0.1/sqrt(101), X = 10 R.
    text = texts["flat_skmax_xrmax"]
    line = next(ln for ln in text.splitlines() if ln.startswith("Z1_PU = "))
    r, x = ast.literal_eval(line.split("=", 1)[1].split("#")[0].strip())
    assert r == pytest.approx(0.1 / math.sqrt(101.0), rel=1e-12)
    assert x == pytest.approx(1.0 / math.sqrt(101.0), rel=1e-12)
    # Z0 = 1.5 |Z1| at X0/R0 = 4: on the 1-ph fault cases only matters, but written always
    line0 = next(ln for ln in text.splitlines() if ln.startswith("Z0_PU = "))
    r0, x0 = ast.literal_eval(line0.split("=", 1)[1].split("#")[0].strip())
    assert r0 == pytest.approx(0.15 / math.sqrt(17.0), rel=1e-12)
    assert x0 == pytest.approx(0.6 / math.sqrt(17.0), rel=1e-12)


def _refuse(tmp_path, reason, cases=None, **kw):
    with pytest.raises(ContractError, match=reason):
        write_scripts(_battery() if cases is None else cases, str(tmp_path), **{**KW, **kw})
    assert not os.listdir(tmp_path)


def test_refuses_absolute_file_name(tmp_path):
    _refuse(tmp_path, "raw '/abs/pack.raw': absolute path", raw="/abs/pack.raw")


def test_refuses_non_pack_local_file_name(tmp_path):
    _refuse(tmp_path, "dyr 'sub/pack.dyr': not pack-local", dyr="sub/pack.dyr")


def test_refuses_parent_dir_file_name(tmp_path):
    _refuse(tmp_path, r"seq '\.\.': not pack-local", seq="..")


def test_refuses_empty_case_list(tmp_path):
    _refuse(tmp_path, "no battery cases", cases=[])


@pytest.mark.parametrize("kv", [0.0, -132.0, math.nan])
def test_refuses_non_positive_kv(tmp_path, kv):
    _refuse(tmp_path, "poc_kv=.* must be a finite number > 0", poc_kv=kv)


def test_refuses_non_positive_mbase(tmp_path):
    _refuse(tmp_path, "grid_mbase_mva=0.0 must be", grid_mbase_mva=0.0)


def test_refuses_pq_channels_without_plant_branch(tmp_path):
    _refuse(tmp_path, "need plant_branch", plant_branch=None)


def test_refuses_setpoint_steps_without_plant_machine(tmp_path):
    _refuse(tmp_path, "need plant_machine", plant_machine=None)
