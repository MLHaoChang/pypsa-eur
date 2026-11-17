"""
GC-04 item 6: psspy run scripts per battery case, checked with ``ast`` only
(no psspy in CI; GK condition S1). Each refusal test asserts its own reason.
"""

import ast
import dataclasses
import math
import os

import pytest

from gridspine.handoff.psspy_scripts import (
    CONVERSIONS,
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
        (
            lambda t: t.replace("'pack.dyr'", "'other.dyr'"),
            "'other.dyr' is not in the pack",
        ),
        (lambda t: t.replace("'pack.raw'", "'/tmp/pack.raw'"), "absolute path literal"),
        (
            lambda t: t.replace("import os\n", "import socket\nimport os\n"),
            "import of 'socket'",
        ),
        (
            lambda t: (
                t.replace("from os import path", "") + "\nfrom os import system\n"
            ),
            "from os",
        ),
        (lambda t: t + "\nopen(OUT)\n", "call to open"),
        (lambda t: t + "\neval('1')\n", "call to eval"),
        (lambda t: t + "\nexec('1')\n", "call to exec"),
        (lambda t: t + "\n__import__('x')\n", "call to __import__"),
        (lambda t: t + "\nos.system('x')\n", "os call outside os.path"),
        (lambda t: t + "\nimport subprocess\n", "import of 'subprocess'"),
        (lambda t: t.replace("'f_hz'", "'f'"), "channel 'f_hz' has no ident literal"),
        (lambda t: t + "\nopen(os.path.join(HERE, 'pack.raw'), 'w')\n", "call to open"),
        (
            lambda t: t + "\nopen(os.path.join(HERE, 'x.csv'), 'w')\n",
            "'x.csv' is not in",
        ),
        (lambda t: t.replace("), 'w')", "), 'a')"), "call to open"),
        (
            lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv'), 'r')\n",
            "call to open",
        ),
        (lambda t: t + f"\nopen('{CID}.csv', 'w')\n", "call to open"),
        (
            lambda t: t + f"\nopen(os.path.join(OUT, '{CID}.csv'), 'w')\n",
            "call to open",
        ),
        (
            lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv'), 'w', 1)\n",
            "call to open",
        ),
        (lambda t: t + "\nHERE = '..'\n", "HERE must be bound once"),
        (
            lambda t: t.replace("HERE = os.path.dirname(", "HERE = (", 1),
            "HERE must be bound",
        ),
        (
            lambda t: t.replace("import os\n", "import sys\nimport os\n"),
            "import of 'sys'",
        ),
        (lambda t: t + "\nfrom sys import path\n", "import of 'sys'"),
        (
            lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv'), 'w', newline='')\n",
            "call to open",
        ),
        (
            lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv'), MODE)\n",
            "call to open",
        ),
        (
            lambda t: t + f"\nopen(os.path.relpath(HERE, '{CID}.csv'), 'w')\n",
            "call to open",
        ),
        (
            lambda t: t + f"\nopen(os.path.join(HERE, '{CID}.csv', x=1), 'w')\n",
            "call to open",
        ),
        (
            lambda t: t + f"\nopen(os.path.join(HERE, 'd', '{CID}.csv'), 'w')\n",
            "call to open",
        ),
        (
            lambda t: t + f"\nopen(os.path.join(F.HERE, '{CID}.csv'), 'w')\n",
            "call to open",
        ),
        (lambda t: t + "\nopen(os.path.join(HERE, NAME), 'w')\n", "call to open"),
        (lambda t: t + "\nopen(os.path.join(HERE, '.csv'), 'w')\n", "call to open"),
        (
            lambda t: t + "\nclass C:\n    '/etc/passwd'\n",
            "absolute path literal '/etc/passwd'",
        ),
        (
            lambda t: t + "\ndef f():\n    x = '/etc/passwd'\n",
            "absolute path literal '/etc/passwd'",
        ),
        (lambda t: t + "\ndel HERE\n", "HERE must be bound once"),
        (lambda t: t + "\nx = 'a/b'\n", "file 'a/b' is not in the pack"),
        (lambda t: t + "\nx = 'a\\\\b'\n", r"file 'a\\\\b' is not in the pack"),
        (lambda t: t + "\nfrom . import x\n", "import of ''"),
        (
            lambda t: t + f"\neval(os.path.join(HERE, '{CID}.csv'), 'w')\n",
            "call to eval",
        ),
        (lambda t: t + "\nsubprocess.run(1)\n", "call to subprocess.run"),
        (
            lambda t: t.replace(
                "HERE = os.path.dirname(os.path.abspath(__file__))",
                "for HERE in (os.path.dirname(os.path.abspath(__file__)),): pass",
            ),
            "HERE must be bound once",
        ),
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
        write_scripts(
            _battery() if cases is None else cases, str(tmp_path), **{**KW, **kw}
        )
    assert not os.listdir(tmp_path)


def test_refuses_absolute_file_name(tmp_path):
    _refuse(tmp_path, "raw '/abs/pack.raw': absolute path", raw="/abs/pack.raw")


def test_refuses_non_pack_local_file_name(tmp_path):
    _refuse(tmp_path, "dyr 'sub/pack.dyr': not pack-local", dyr="sub/pack.dyr")


def test_refuses_parent_dir_file_name(tmp_path):
    _refuse(tmp_path, r"seq '\.\.': not pack-local", seq="..")


def test_refuses_empty_case_list(tmp_path):
    _refuse(tmp_path, "no battery cases", cases=[])


@pytest.mark.parametrize("kv", [0.0, -132.0, math.nan, True])
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
            ln.rsplit(",", 1)[-1].strip(" )]")
            for ln in text.splitlines()
            if "_channel(" in ln
        )


def test_convert_gives_contract_units_on_hand_values(pack):
    _, texts = pack
    tree = ast.parse(texts["phase_jump_skmin_xrmin_up"])
    fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_convert"
    )
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


def _check(text):
    return check_script(text, _allowed(CID))


def test_checker_skips_docstrings_only(pack):
    _, texts = pack
    text = texts[CID] + '\ndef f():\n    "see /etc/passwd and other.raw"\n'
    assert _check(text) == _allowed(CID)
    mod = (
        '"""Module notes: /etc/passwd, other.raw."""\n' + texts[CID].split('"""', 2)[2]
    )
    assert _check(mod) == _allowed(CID)


def test_checker_allows_from_psspy_imports(pack):
    _, texts = pack
    assert _check(texts[CID] + "\nfrom psspy import psseinit\n") == _allowed(CID)


def test_docstring_lists_every_case_parameter(pack):
    cases, texts = pack
    for cid, text in texts.items():
        doc = ast.get_docstring(ast.parse(text))
        for k, v in cases[cid].params.items():
            assert f"{k}={v!r}" in doc


def test_bolted_three_phase_fault_uses_the_mva_default(pack):
    _, texts = pack
    bolted = "psspy.dist_bus_fault(POC_BUS, 1, POC_KV, [0.0, -2.0e11])"
    assert bolted in texts["fault_3ph_skmin_xrmin_v000"].splitlines()
    v020 = texts["fault_3ph_skmin_xrmin_v020"]
    assert bolted not in v020 and "psspy.dist_bus_fault(POC_BUS, 2, POC_KV, [" in v020


def test_checker_refuses_an_empty_script():
    with pytest.raises(ContractError, match="HERE must be bound once"):
        check_script("", _allowed(CID))


def test_f_step_advances_the_angle_by_two_pi_cycles(pack):
    _, texts = pack
    text = next(t for cid, t in texts.items() if cid.startswith("f_step_"))
    assert f"psspy.change_state(K, ANGLE0 + {2.0 * math.pi!r} * cycles)" in text


def test_flat_case_docstring_says_parameters_none(pack):
    _, texts = pack
    assert "parameters: none;" in ast.get_docstring(ast.parse(texts[CID]))


def test_plant_branch_and_machine_are_written(pack):
    _, texts = pack
    lines = texts[CID].splitlines()
    assert "BR_FROM, BR_TO, BR_CKT = 101, 900, '1'" in lines
    assert "PLANT_BUS, PLANT_ID = 201, '1'" in lines
    assert "POC_BUS, POC_KV = 101, 132.0" in lines
    assert "GRID_BUS, GRID_ID, GRID_MBASE = 900, '1', 100.0" in lines


#: recorded channel -> the psspy line that records it
CHANNEL_LINE = {
    "raw_theta_poc_deg": "psspy.voltage_and_angle_channel([-1, -1, -1, POC_BUS], "
    "['raw_v_poc_pu', 'raw_theta_poc_deg'])",
    "raw_theta_src": "psspy.machine_array_channel([-1, 1, GRID_BUS], GRID_ID, 'raw_theta_src')",
    "raw_f_hz": "psspy.bus_frequency_channel([-1, POC_BUS], 'raw_f_hz')",
    "raw_p_poc_mw": "psspy.branch_p_and_q_channel([-1, -1, -1, BR_FROM, BR_TO], BR_CKT, "
    "['raw_p_poc_mw', 'raw_q_poc_mvar'])",
    "raw_id_pu": "psspy.machine_array_channel([-1, 22, PLANT_BUS], PLANT_ID, 'raw_id_pu')",
    "raw_iq_pu": "psspy.machine_array_channel([-1, 23, PLANT_BUS], PLANT_ID, 'raw_iq_pu')",
}
V_ONLY = "psspy.voltage_channel([-1, -1, -1, POC_BUS], 'raw_v_poc_pu')"


def _flat(channels):
    flat = next(c for c in _battery() if c.case_id == CID)
    return dataclasses.replace(flat, channels=channels)


@pytest.mark.parametrize(
    "channels",
    [
        ("v_poc_pu",),
        ("theta_poc_deg",),
        ("f_hz",),
        ("p_poc_mw",),
        ("q_poc_mvar",),
        ("id_pu",),
        ("iq_pu",),
        ("i_poc_pu",),
        ("v_poc_pu", "theta_poc_deg", "f_hz", "p_poc_mw", "id_pu", "iq_pu"),
    ],
)
def test_each_channel_line_iff_requested(tmp_path, channels):
    out = write_scripts([_flat(channels)], str(tmp_path), **KW)
    lines = (tmp_path / out[CID]).read_text(encoding="utf-8").splitlines()
    raw = {r for ch in channels for r in CONVERSIONS[ch][0]}
    for rec, line in CHANNEL_LINE.items():
        want = rec in raw or (rec == "raw_p_poc_mw" and "raw_q_poc_mvar" in raw)
        assert (line in lines) == want, rec
    v_only = "raw_v_poc_pu" in raw and "raw_theta_poc_deg" not in raw
    assert (V_ONLY in lines) == v_only


def test_no_plant_lines_when_no_case_needs_them(tmp_path):
    kw = {**KW, "plant_branch": None, "plant_machine": None, "plant_mbase_mva": None}
    out = write_scripts([_flat(("v_poc_pu", "f_hz"))], str(tmp_path), **kw)
    text = (tmp_path / out[CID]).read_text(encoding="utf-8")
    assert "BR_FROM" not in text and "PLANT_BUS" not in text
    assert "F0_HZ, PLANT_MBASE = 50.0, None" in text


@pytest.mark.parametrize("ch", ["p_poc_mw", "q_poc_mvar", "i_poc_pu"])
def test_each_pq_current_channel_needs_plant_branch(tmp_path, ch):
    _refuse(
        tmp_path,
        f"case {CID}: P/Q/current channels need plant_branch",
        cases=[_flat((ch,))],
        plant_branch=None,
    )


@pytest.mark.parametrize("ch", ["id_pu", "iq_pu"])
def test_id_iq_channels_need_plant_machine(tmp_path, ch):
    _refuse(
        tmp_path,
        f"case {CID}: setpoint steps and id/iq channels need plant_machine",
        cases=[_flat((ch,))],
        plant_machine=None,
    )


def test_setpoint_step_without_current_channels_needs_plant_machine(tmp_path):
    step = next(c for c in _battery() if c.kind == "p_step")
    step = dataclasses.replace(step, channels=("v_poc_pu",))
    _refuse(
        tmp_path,
        f"case {step.case_id}: setpoint steps .* need plant_machine",
        cases=[step],
        plant_machine=None,
    )


def test_plant_mbase_checked_when_given_even_if_unused(tmp_path):
    _refuse(
        tmp_path,
        "plant_mbase_mva=0.0 must be",
        cases=[_flat(("v_poc_pu",))],
        plant_mbase_mva=0.0,
    )


def test_accepts_a_generator_of_cases(tmp_path):
    cases = _battery()
    out = write_scripts((c for c in cases), str(tmp_path), **KW)
    assert set(out) == {c.case_id for c in cases}


def test_refuses_an_empty_generator(tmp_path):
    _refuse(tmp_path, "no battery cases to write scripts for", cases=(c for c in ()))


def test_refuses_a_non_case_in_the_battery(tmp_path):
    _refuse(tmp_path, "test battery holds a str, not a Case", cases=[*_battery(), "x"])


@pytest.mark.parametrize(
    "name, reason",
    [
        (None, "raw None: not a file name"),
        (123, "raw 123: not a file name"),
        ("", "raw '': not a file name"),
        ("C:\\pack.raw", "absolute path; give a pack-local name"),
        ("~/pack.raw", "absolute path; give a pack-local name"),
        (".", r"raw '\.': not pack-local"),
    ],
)
def test_refuses_bad_raw_name(tmp_path, name, reason):
    _refuse(tmp_path, reason, raw=name)


@pytest.mark.parametrize("bus", [0, -1, True, 101.0, "101", None])
def test_refuses_bad_poc_bus(tmp_path, bus):
    _refuse(
        tmp_path,
        f"poc_bus={bus!r} must be a positive int bus number".replace(".", r"\."),
        poc_bus=bus,
    )


@pytest.mark.parametrize("label", ["grid_machine", "plant_machine"])
@pytest.mark.parametrize(
    "machine, reason",
    [
        ([900, "1"], "must be a \\(positive int bus, str id\\) tuple"),
        ((900,), "must be a \\(positive int bus, str id\\) tuple"),
        ((900, "1", "x"), "must be a \\(positive int bus, str id\\) tuple"),
        ((0, "1"), "must be a \\(positive int bus, str id\\) tuple"),
        ((True, "1"), "must be a \\(positive int bus, str id\\) tuple"),
        ((900, 1), "must be a \\(positive int bus, str id\\) tuple"),
        ((900, ""), "id '' must be 1 or 2 characters"),
        ((900, "123"), "id '123' must be 1 or 2 characters"),
    ],
)
def test_refuses_bad_machine(tmp_path, label, machine, reason):
    _refuse(tmp_path, f"{label}.*{reason}", **{label: machine})


def test_refuses_missing_grid_machine(tmp_path):
    _refuse(tmp_path, "grid_machine=None must be a", grid_machine=None)


def test_accepts_two_char_machine_id(tmp_path):
    out = write_scripts(
        [_flat(("v_poc_pu",))], str(tmp_path), **{**KW, "grid_machine": (900, "G1")}
    )
    assert (
        "GRID_BUS, GRID_ID, GRID_MBASE = 900, 'G1', 100.0"
        in (tmp_path / out[CID]).read_text()
    )


@pytest.mark.parametrize(
    "branch",
    [
        [101, 900, "1"],
        (101, 900),
        (101, 900, "1", 0),
        (101.0, 900, "1"),
        (101, "900", "1"),
        (True, 900, "1"),
        (101, 900, 1),
    ],
)
def test_refuses_bad_plant_branch(tmp_path, branch):
    _refuse(
        tmp_path,
        r"plant_branch=.* must be a \(from bus, to bus, ckt\) tuple",
        plant_branch=branch,
    )


def test_refuses_a_conversion_with_more_than_three_inputs(tmp_path, monkeypatch):
    four = ("raw_v_poc_pu", "raw_f_hz", "raw_p_poc_mw", "raw_q_poc_mvar")
    monkeypatch.setitem(CONVERSIONS, "v_poc_pu", (four, "a", "four inputs"))
    with pytest.raises(
        ContractError, match="conversion of 'v_poc_pu': 4 inputs, at most 3"
    ):
        write_scripts([_flat(("v_poc_pu",))], str(tmp_path), **KW)
