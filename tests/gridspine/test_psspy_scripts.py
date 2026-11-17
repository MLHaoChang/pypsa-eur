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
    f0_hz=50.0,
    plant_mbase_mva=80.0,
)
CID = "flat_skmin_xrmin"


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
        (lambda t: t + "\nopen(os.path.join(HERE, 'pack.raw'), 'w')\n", "call to open"),
        (lambda t: t + "\nopen(os.path.join(HERE, 'x.csv'), 'w')\n", "'x.csv' is not in"),
        (lambda t: t.replace("), 'w')", "), 'a')"), "call to open"),
        (lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv'), 'r')\n", "call to open"),
        (lambda t: t + f"\nopen('{CID}.csv', 'w')\n", "call to open"),
        (lambda t: t + f"\nopen(os.path.join(OUT, '{CID}.csv'), 'w')\n", "call to open"),
        (lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv'), 'w', 1)\n", "call to open"),
        (lambda t: t + "\nHERE = '..'\n", "HERE must be bound once"),
        (lambda t: t.replace("HERE = os.path.dirname(", "HERE = (", 1), "HERE must be bound"),
    ],
)
def test_checker_refuses_a_tampered_script(pack, mutate, reason):
    cases, texts = pack
    cid = CID
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


def test_refuses_non_positive_f0(tmp_path):
    _refuse(tmp_path, "f0_hz=0.0 must be", f0_hz=0.0)


def test_refuses_i_poc_without_plant_mbase(tmp_path):
    _refuse(tmp_path, "plant_mbase_mva=None must be", plant_mbase_mva=None)


def test_csv_header_is_contract_names_in_case_order(pack):
    cases, texts = pack
    for cid, text in texts.items():
        line = next(ln for ln in text.splitlines() if ln.startswith("HEADER = "))
        assert ast.literal_eval(line.split("=", 1)[1]) == ["t_s", *cases[cid].channels]
        assert f"open(os.path.join(HERE, '{cid}.csv'), 'w')" in text
        assert not {"'v_poc_pu'", "'f_hz'"} & set(
            ln.rsplit(",", 1)[-1].strip(" )]") for ln in text.splitlines()
            if "_channel(" in ln
        )


def test_convert_gives_contract_units_on_hand_values(pack):
    _, texts = pack
    tree = ast.parse(texts["phase_jump_skmin_xrmin_up"])
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_convert")
    ns = {}
    exec(compile(ast.Module([fn], []), "convert", "exec"), ns)  # pure: no psspy
    rec = {
        "t_s": [0.0, 1.0],
        "raw_v_poc_pu": [0.5, 0.0],
        "raw_theta_poc_deg": [30.0, 10.0],
        "raw_theta_src": [10.0, 10.0],
        "raw_f_hz": [0.01, 0.0],
        "raw_p_poc_mw": [30.0, 3.0],
        "raw_q_poc_mvar": [-40.0, 4.0],
        "raw_id_pu": [0.6, 0.0],
        "raw_iq_pu": [0.8, 1.0],
    }
    out = ns["_convert"](rec, 50.0, 80.0)
    assert out["f_hz"] == [pytest.approx(50.5), 50.0]  # 50 * (1 + 0.01)
    assert out["i_poc_pu"][0] == pytest.approx(50.0 / (0.5 * 80.0))  # |S| / (V S_rated)
    assert math.isnan(out["i_poc_pu"][1])  # V = 0: undefined, not inf
    assert out["theta_poc_deg"] == [20.0, 0.0]
    assert out["q_poc_mvar"] == [-40.0, 4.0] and out["p_poc_mw"] == [30.0, 3.0]
    assert out["iq_pu"] == [0.8, 1.0] and out["t_s"] == [0.0, 1.0]
