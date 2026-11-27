"""
The PSCAD / EMT pack (GC-06 part 1, 2, 4): Thevenin set, case schedule,
automation stub and manifest, from the campus PCC data and the test battery.
"""

import ast
import hashlib
import json
import math
import re

import pytest

from gridspine.handoff import emt_pack
from gridspine.handoff.emt_pack import (
    C_NOTE,
    SCHEDULE_COLUMNS,
    EmtPackError,
    battery_from_campus,
    study_sources,
    write_emt_pack,
)
from gridspine.handoff.test_battery import (
    CHANNEL_SEP,
    CHANNELS,
    PARAM_NAMES,
    Case,
    Thevenin,
    build_battery,
    cases_from_json,
    fault_impedance_ohm,
    thevenin_impedances_ohm,
)
from gridspine.ingest.campus import build_campus
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_campus import campus_spec, t


def spec(**pcc):
    """
    The base campus (PCC 110 kV, Sk 3000/2000 MVA, R/X 0.1/0.12) with
    zero sequence on both study cases unless overridden.
    """
    s = campus_spec()
    p = s["campus"]["pcc"]
    p.update(x0x_max=t(3.0), r0x0_max=t(0.1), x0x_min=t(2.5), r0x0_min=t(0.2))
    for k, v in pcc.items():
        if v is None:
            p.pop(k)
        else:
            p[k] = t(v)
    return s


def pack(tmp_path, s=None, **kw):
    return write_emt_pack(tmp_path / "emt", build_campus(s or spec()), **kw)


def rows(path, name):
    return json.loads((path / name).read_text())


def grid_gaps(p):
    """The to-be-supplied rows of the grid source (not the workbook's)."""
    g = p.to_be_supplied
    return g[g["sheet"].isin(["thevenin", "case_schedule"])]


def by_label(p):
    return {r["label"]: r for r in rows(p.path, "thevenin_set.json")["sources"]}


# --- Thevenin set ---------------------------------------------------------


def test_max_case_r_and_x_match_a_hand_calculation(tmp_path):
    # |Z| = 110^2 / 3000 = 4.03333 ohm; X/R = 1/0.1 = 10
    # R = 4.03333 / sqrt(101) = 0.401331667; X = 10 R = 4.013316667
    r = by_label(pack(tmp_path))["max"]
    assert r["x_over_r"] == pytest.approx(10.0, abs=1e-9)
    assert r["r_ohm"] == pytest.approx(0.401331667, abs=1e-6)
    assert r["x_ohm"] == pytest.approx(4.013316667, abs=1e-6)
    # zero sequence: X0 = 3.0 X1 = 12.03995; R0 = 0.1 X0 = 1.203995
    assert r["x0_ohm"] == pytest.approx(12.039950002, abs=1e-6)
    assert r["r0_ohm"] == pytest.approx(1.203995000, abs=1e-6)


def test_x_over_r_is_one_over_rx_paired_with_its_own_case(tmp_path):
    # rx_max pairs with sk_max, rx_min with sk_min; X/R = 1/rx
    p = pack(tmp_path, spec(rx_max=0.25, rx_min=0.5))
    s = by_label(p)
    assert (s["max"]["sk_mva"], s["max"]["x_over_r"]) == (3000.0, pytest.approx(4.0))
    assert (s["min"]["sk_mva"], s["min"]["x_over_r"]) == (2000.0, pytest.approx(2.0))
    # min case, c_max 1.1: |Z| = 1.1 * 121/20 = 6.655 ohm at X/R 2: R = 6.655/sqrt(5) = 2.976206,
    # X = 2 R = 5.952413
    assert s["min"]["c"] == 1.1
    assert s["min"]["r_ohm"] == pytest.approx(2.976206478, abs=1e-6)
    assert s["min"]["x_ohm"] == pytest.approx(5.952412956, abs=1e-6)
    # battery corners span the smaller and larger X/R
    assert s["skmin_xrmin"]["x_over_r"] == pytest.approx(2.0)
    assert s["skmax_xrmax"]["x_over_r"] == pytest.approx(4.0)


def test_sk_min_above_sk_max_is_refused_by_name():
    pcc = {
        "sk_min_mva": (3001.0, "measured"),
        "sk_max_mva": (3000.0, "measured"),
        "rx_max": (0.1, "assumed"),
        "rx_min": (0.1, "assumed"),
    }
    with pytest.raises(EmtPackError, match=r"sk_min_mva=3001.0 > sk_max_mva=3000.0"):
        study_sources(pcc)
    pcc["sk_min_mva"] = (3000.0, "measured")  # equal is allowed
    assert len(study_sources(pcc)[0]) == 2


def test_rx_zero_is_refused_as_an_infinite_x_over_r(tmp_path):
    with pytest.raises(
        EmtPackError,
        match=r"rx_min=0 means X/R not given / infinite: an EMT source needs a finite X/R",
    ):
        pack(tmp_path, spec(rx_min=0.0))


def test_pack_errors_are_contract_errors_and_typed():
    assert issubclass(EmtPackError, ContractError)
    pcc = {
        "sk_min_mva": (2.0, "a"),
        "sk_max_mva": (1.0, "a"),
        "rx_max": (0.1, "a"),
        "rx_min": (0.1, "a"),
    }
    try:
        study_sources(pcc)
    except ContractError as e:  # the catching site callers use
        assert type(e) is EmtPackError
    else:
        pytest.fail("no refusal")


def test_every_row_states_the_c_factor_and_the_manifest_lists_it(tmp_path):
    p = pack(tmp_path)
    rows = by_label(p)
    assert rows["max"]["note"] == C_NOTE
    assert "c_max on Sk-min, c_min on Sk-max" in C_NOTE
    # GC-14: each row names its c; c_min is 1.0 at the 110 kV PCC; normal is no IEC 60909 case
    assert {k: r["c"] for k, r in rows.items()} == {
        "max": 1.0,
        "min": 1.1,
        **({"normal": 1.0} if "normal" in rows else {}),
        "skmin_xrmin": 1.1,
        "skmin_xrmax": 1.1,
        "skmax_xrmin": 1.0,
        "skmax_xrmax": 1.0,
    }
    csv_lines = (p.path / "thevenin_set.csv").read_text().splitlines()[1:]
    assert csv_lines and all(C_NOTE in ln for ln in csv_lines)
    assert any(C_NOTE in a for a in p.manifest["assumptions"])


def test_study_rows_keep_their_pcc_source_tags(tmp_path):
    s = by_label(pack(tmp_path))
    # sk_*: measured, rx_* and zero sequence: assumed
    assert s["max"]["source"] == "assumed/measured"
    assert s["skmax_xrmax"]["group"] == "battery"


def test_normal_case_appears_when_given(tmp_path):
    s = by_label(pack(tmp_path, spec(sk_normal_mva=2500.0, rx_normal=0.11)))
    assert s["normal"]["sk_mva"] == 2500.0
    assert s["normal"]["x_over_r"] == pytest.approx(1 / 0.11)
    assert s["normal"]["x0_ohm"] is None
    # GC-14: the normal case is no IEC 60909 case: c = 1.0, |Z| = 110^2 / 2500 = 4.84 ohm
    assert s["normal"]["c"] == 1.0
    assert math.hypot(s["normal"]["r_ohm"], s["normal"]["x_ohm"]) == pytest.approx(
        4.84, rel=1e-12
    )


def test_a_case_without_zero_sequence_is_to_be_supplied_not_borrowed(tmp_path):
    p = pack(tmp_path, spec(x0x_min=None, r0x0_min=None))
    s = by_label(p)
    assert s["min"]["x0_ohm"] is None and s["min"]["z0_over_z1"] is None
    gaps = grid_gaps(p)
    assert set(gaps.loc[gaps["sheet"] == "thevenin", "param"]) == {
        "x0x_min",
        "r0x0_min",
    }
    assert gaps["why"].str.contains("zero sequence of the min case not held").all()
    assert (
        p.path / "case_schedule.json"
    ).exists()  # the max case still builds the battery


def test_r0x0_zero_is_a_gap_not_an_infinite_ratio(tmp_path):
    p = pack(tmp_path, spec(r0x0_min=0.0))
    assert by_label(p)["min"]["x0_over_r0"] is None
    assert list(grid_gaps(p)["param"]) == ["r0x0_min"]


def test_without_a_max_zero_sequence_no_schedule_is_written(tmp_path):
    p = pack(tmp_path, spec(x0x_max=None, r0x0_max=None))
    assert not (p.path / "case_schedule.json").exists()
    assert not (p.path / "run_schedule.py").exists()
    assert "x0x_max, r0x0_max" in set(grid_gaps(p)["param"])
    assert p.manifest["case_ids"] == []
    with pytest.raises(EmtPackError, match="max case has no zero sequence"):
        battery_from_campus(build_campus(spec(x0x_max=None, r0x0_max=None)))


def test_battery_z0_follows_the_corner_sk_and_nothing_is_assumed(tmp_path):
    # hand ratios from spec(): max X0/X1 = 3, R0/X0 = 0.1 at R/X 0.1; min 2.5, 0.2 at 0.12
    p = pack(tmp_path)
    b = cases_from_json((p.path / "battery.json").read_text())
    want = {
        "skmax": (3.0 * math.sqrt(1.01) / math.sqrt(1.01), 10.0),
        "skmin": (2.5 * math.sqrt(1.04) / math.sqrt(1.0144), 5.0),
    }
    for c in b:
        z0z1, x0r0 = want[c.grid.label.split("_")[0]]
        assert c.grid.z0_over_z1 == pytest.approx(z0z1, rel=1e-12), c.case_id
        assert c.grid.x0_over_r0 == pytest.approx(x0r0, rel=1e-12), c.case_id
    assert p.manifest["z0_assumed"] == {}
    assert not any("assumed: from max" in a for a in p.manifest["assumptions"])


def test_stage_reference_is_labelled_and_is_the_grid_alone_with_nothing_installed(
    tmp_path,
):
    p = pack(tmp_path, stages={"none": set(), "all": {"BESS1", "PV1", "GEN1"}})
    st = {r["stage"]: r for r in rows(p.path, "thevenin_set.json")["stages"]}
    assert st["none"]["sk_max_mva"] == pytest.approx(3000.0, rel=1e-9)
    assert st["none"]["sk_min_mva"] == pytest.approx(2000.0, rel=1e-9)
    assert st["all"]["sk_max_mva"] > st["none"]["sk_max_mva"]
    assert all(
        r["note"].startswith("reference, not the EMT source") for r in st.values()
    )
    assert (p.path / "thevenin_stages.csv").read_text().count("\n") == 3


# --- case schedule ----------------------------------------------------------


def test_every_battery_case_appears_once_with_contract_channels(tmp_path):
    camp = build_campus(spec())
    battery = battery_from_campus(camp)
    p = write_emt_pack(tmp_path, camp, battery=battery)
    sched = rows(p.path, "case_schedule.json")
    assert [r["case_id"] for r in sched] == [c.case_id for c in battery]
    contract = list(CHANNELS)
    for r, c in zip(sched, battery):
        names = r["channels"].split(CHANNEL_SEP)
        assert names == list(c.channels)
        assert names == [n for n in contract if n in names]  # contract order
        assert r["output_name"] == c.case_id
    csv = (p.path / "case_schedule.csv").read_text().splitlines()
    assert [ln.split(",")[0] for ln in csv[1:]] == [c.case_id for c in battery]


def test_schedule_fault_impedance_is_the_battery_contract(tmp_path):
    camp = build_campus(spec())
    battery = battery_from_campus(camp)
    sched = {
        r["case_id"]: r
        for r in rows(
            write_emt_pack(tmp_path, camp, battery=battery).path, "case_schedule.json"
        )
    }
    faults = [c for c in battery if c.kind in ("fault_3ph", "fault_1ph")]
    assert faults
    for c in battery:
        r = sched[c.case_id]
        if c in faults:
            zf = fault_impedance_ohm(c, 110.0)
            assert (r["fault_r_ohm"], r["fault_x_ohm"]) == (zf.real, zf.imag)
        else:
            assert r["fault_r_ohm"] is None and r["fault_x_ohm"] is None
        assert (r["t_event_s"], r["t_end_s"]) == (c.t_event_s, c.t_end_s)
        assert {k: r[k] for k in c.params} == dict(c.params)


def test_schedule_round_trips_to_the_same_cases(tmp_path):
    camp = build_campus(spec())
    battery = build_battery(
        2000.0,
        3000.0,
        8.0,
        10.0,
        z0_over_z1=3.0,
        x0_over_r0=10.0,
        overrides={"flat_skmin_xrmin": {"t_end_s": 5.0}},
        contract_version="2",
    )
    p = write_emt_pack(tmp_path, camp, battery=battery)
    back = cases_from_json((p.path / "battery.json").read_text())
    assert back == battery
    assert [r["case_id"] for r in rows(p.path, "case_schedule.json")] == [
        c.case_id for c in back
    ]
    assert p.manifest["case_ids"] == [c.case_id for c in battery]


# --- automation stub ------------------------------------------------------

_FILEISH = re.compile(r"^[\w./-]*\w\.[A-Za-z]{2,5}$")


def _stub(tmp_path):
    p = pack(tmp_path)
    return p, (p.path / "run_schedule.py").read_text()


def _code_strings(tree):
    """String constants outside docstrings."""
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docs
    ]


def test_stub_compiles_and_states_it_is_never_run(tmp_path):
    _, text = _stub(tmp_path)
    compile(text, "run_schedule.py", "exec")
    head = ast.get_docstring(ast.parse(text))
    assert (
        "Generated, never executed in CI; written for the mhi.pscad API version 2"
        in head
    )
    assert "untested against a PSCAD install" in head


def test_stub_references_only_pack_files(tmp_path):
    p, text = _stub(tmp_path)
    files = set(p.manifest["files"])
    refs = [s for s in _code_strings(ast.parse(text)) if _FILEISH.match(s)]
    assert refs == ["case_schedule.json"]
    assert set(refs) <= files
    assert (
        "results" in _code_strings(ast.parse(text))
        and "results/" in p.manifest["outputs"]
    )
    for s in _code_strings(ast.parse(text)):
        assert not s.startswith("/") and not re.match(r"^[A-Za-z]:[\\/]", s), s


def test_a_missing_reference_is_caught(tmp_path):
    p, text = _stub(tmp_path)
    bad = text.replace('"case_schedule.json"', '"cases.json"')
    refs = {s for s in _code_strings(ast.parse(bad)) if _FILEISH.match(s)}
    assert not refs <= set(p.manifest["files"])


def test_stub_imports_and_calls_are_restricted(tmp_path):
    _, text = _stub(tmp_path)
    tree = ast.parse(text)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module)
    assert mods == {"argparse", "json", "pathlib", "mhi.pscad"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not ({"eval", "exec", "compile", "__import__"} & names)
    assert not ({"system", "popen", "Popen", "run_command"} & attrs)
    assert "subprocess" not in text and "os." not in text


def _stub_read_keys(tree):
    """Every schedule column the stub reads: the SOURCE_KEYS/EVENT_KEYS keys and case[...]."""
    keys = set()
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ("SOURCE_KEYS", "EVENT_KEYS")
        ):
            keys |= {k.value for k in n.value.keys}
        elif (
            isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name)
            and n.value.id == "case"
            and isinstance(n.slice, ast.Constant)
        ):
            keys.add(n.slice.value)
    return keys


def test_every_column_the_stub_reads_is_a_schedule_column(tmp_path):
    _, text = _stub(tmp_path)
    keys = _stub_read_keys(ast.parse(text))
    assert {"r_ohm", "t_end_s", "output_name", "kind"} <= keys
    assert keys <= set(emt_pack.SCHEDULE_COLUMNS), keys - set(emt_pack.SCHEDULE_COLUMNS)


def test_schedule_json_keys_and_csv_header_are_the_schedule_columns(tmp_path):
    p, _ = _stub(tmp_path)
    cases = rows(p.path, "case_schedule.json")
    assert cases
    for c in cases:
        assert set(c) == set(emt_pack.SCHEDULE_COLUMNS)  # JSON keys are sorted
    header = (p.path / "case_schedule.csv").read_text().splitlines()[0]
    assert header.split(",") == list(emt_pack.SCHEDULE_COLUMNS)


def test_stub_runs_once_per_case_with_that_cases_values(tmp_path, monkeypatch):
    import runpy
    import sys
    import types

    p, _ = _stub(tmp_path)
    cases = rows(p.path, "case_schedule.json")
    calls = []

    class Comp:
        def __init__(self, name):
            self.name = name

        def parameters(self, **kw):
            calls.append((self.name, kw))

    class Project:
        def find(self, name):
            return Comp(name)

        def parameters(self, **kw):
            calls.append(("project", kw))

        def run(self):
            calls.append(("run", {}))

    class Pscad:
        def load(self, ws):
            calls.append(("load", ws))

        def project(self, name):
            return Project()

        def quit(self):
            calls.append(("quit", {}))

    mhi = types.ModuleType("mhi")
    mod = types.ModuleType("mhi.pscad")
    mod.launch = Pscad
    mhi.pscad = mod
    monkeypatch.setitem(sys.modules, "mhi", mhi)
    monkeypatch.setitem(sys.modules, "mhi.pscad", mod)
    ns = runpy.run_path(str(p.path / "run_schedule.py"), run_name="stub")
    ns["main"](["ws.pswx", "proj"])

    runs = [i for i, c in enumerate(calls) if c[0] == "run"]
    assert len(runs) == len(cases)
    assert calls[0] == ("load", "ws.pswx") and calls[-1][0] == "quit"
    assert (p.path / "results").is_dir()
    for case, end in zip(cases, runs):
        block = calls[end - 3 : end]  # source, event, project parameters
        assert block[2][1] == {
            "time_duration": case["t_end_s"],
            "output_filename": case["output_name"] + ".out",
        }
        assert block[0][0] == "GRID_SOURCE" and block[1][0] == "EVENT"
        # Expected parameter names are written by hand, not read from the stub;
        # a blank (None) column must not be passed at all.
        source = {
            "R": case["r_ohm"],
            "X": case["x_ohm"],
            "R0": case["r0_ohm"],
            "X0": case["x0_ohm"],
        }
        event = {
            "KIND": case["kind"],
            "T_EVENT": case["t_event_s"],
            "T_DUR": case["duration_s"],
            "RF": case["fault_r_ohm"],
            "XF": case["fault_x_ohm"],
            "VRET": case["v_retained_pu"],
            "STEP_PU": case["step_pu"],
            "STEP_HZ": case["step_hz"],
            "RAMP_HZ_S": case["ramp_hz_per_s"],
            "ANGLE_DEG": case["angle_deg"],
        }
        assert block[0][1] == {k: v for k, v in source.items() if v is not None}
        assert block[1][1] == {k: v for k, v in event.items() if v is not None}
    # The schedule has blank event columns and distinct values, so a dropped
    # None filter or swapped names (R0/X0, T_EVENT/T_DUR) cannot pass.
    assert any(c["duration_s"] is None for c in cases)
    assert any(c["t_event_s"] != c["duration_s"] and c["duration_s"] for c in cases)
    assert all(c["r0_ohm"] != c["x0_ohm"] for c in cases)


# --- manifest ---------------------------------------------------------------


def test_manifest_and_files_are_byte_deterministic(tmp_path):
    a, b = pack(tmp_path / "a"), pack(tmp_path / "b")
    for name in a.files:
        assert (a.path / name).read_bytes() == (b.path / name).read_bytes(), name


def test_data_hash_covers_every_listed_file_and_moves(tmp_path):
    p = pack(tmp_path)
    files = p.manifest["files"]
    on_disk = {f.name for f in p.path.iterdir()} - {"manifest.json"}
    assert set(files) == on_disk
    for name, h in files.items():
        assert hashlib.sha256((p.path / name).read_bytes()).hexdigest() == h
    joined = "".join(f"{n}:{files[n]}\n" for n in sorted(files))
    assert p.manifest["data_hash"] == hashlib.sha256(joined.encode()).hexdigest()
    # one file changes (a single case's end time) -> the hash moves
    camp = build_campus(spec())
    edited = battery_from_campus(camp, overrides={"flat_skmin_xrmin": {"t_end_s": 5.0}})
    q = write_emt_pack(tmp_path / "q", camp, battery=edited)
    assert q.manifest["files"]["thevenin_stages.csv"] == files["thevenin_stages.csv"]
    assert q.manifest["files"]["case_schedule.json"] != files["case_schedule.json"]
    assert q.manifest["data_hash"] != p.manifest["data_hash"]


def test_model_set_id_only_when_passed(tmp_path):
    without = pack(tmp_path / "a")
    assert "model_set_id" not in without.manifest
    assert "model_set_id" not in json.loads(
        (without.path / "manifest.json").read_text()
    )
    with_ = pack(tmp_path / "b", model_set_id="ms-0007")
    assert (
        json.loads((with_.path / "manifest.json").read_text())["model_set_id"]
        == "ms-0007"
    )


def test_manifest_cites_the_contract(tmp_path):
    m = json.loads((pack(tmp_path).path / "manifest.json").read_text())
    # contract version 2: the schedule records `connected` (GC-12, T1)
    assert m["channels"] == [
        "v_poc_pu",
        "theta_poc_deg",
        "f_hz",
        "p_poc_mw",
        "q_poc_mvar",
        "id_pu",
        "iq_pu",
        "i_poc_pu",
        "connected",
    ]
    assert m["contract_version"] == emt_pack.EMT_CONTRACT_VERSION
    assert m["files"] and math.isfinite(m["vn_kv"])


# --- review and mutant follow-ups --------------------------------------------


def test_normal_case_without_schema_zero_sequence_is_not_a_gap(tmp_path):
    p = pack(tmp_path, spec(sk_normal_mva=2500.0, rx_normal=0.11))
    assert grid_gaps(p).empty


def test_x0x_without_r0x0_lists_only_the_missing_one(tmp_path):
    p = pack(tmp_path, spec(r0x0_min=None))
    assert list(grid_gaps(p)["param"]) == ["r0x0_min"]
    assert by_label(p)["min"]["x0_ohm"] is None


def test_without_a_min_zero_sequence_the_skmin_cases_are_assumed_from_max(tmp_path):
    p = pack(tmp_path, spec(x0x_min=None, r0x0_min=None))
    b = cases_from_json((p.path / "battery.json").read_text())
    skmin = [c.case_id for c in b if c.grid.label.startswith("skmin_")]
    assert len(skmin) == 28
    assert p.manifest["z0_assumed"] == {cid: "assumed: from max" for cid in skmin}
    assert {(c.grid.z0_over_z1, c.grid.x0_over_r0) for c in b} == {(3.0, 10.0)}
    assert any(
        a.startswith("battery zero sequence at the Sk-min corners: assumed: from max")
        for a in p.manifest["assumptions"]
    )


def test_missing_pcc_field_is_refused_by_name():
    with pytest.raises(EmtPackError, match=r"PCC data lacks \['rx_min'\]"):
        study_sources(
            {"sk_min_mva": (1.0, "a"), "sk_max_mva": (2.0, "a"), "rx_max": (0.1, "a")}
        )


def test_battery_rows_say_their_zero_sequence_is_borrowed(tmp_path):
    s = by_label(pack(tmp_path))
    corners = [r for r in s.values() if r["group"] == "battery"]
    assert sorted(r["label"] for r in corners) == [
        "skmax_xrmax",
        "skmax_xrmin",
        "skmin_xrmax",
        "skmin_xrmin",
    ]
    assert s["skmin_xrmin"]["source"] == (
        "campus PCC corners; Z0 per corner: Sk-max corners from the max case, "
        "Sk-min corners from the min case"
    )
    s = by_label(pack(tmp_path / "nomin", spec(x0x_min=None, r0x0_min=None)))
    assert s["skmin_xrmin"]["source"] == (
        "campus PCC corners; Z0 Sk-max corners from the max case; Sk-min corners "
        "assumed: from max; this corner's Z0 assumed: from max"
    )
    assert s["skmax_xrmin"]["source"] == (
        "campus PCC corners; Z0 Sk-max corners from the max case; Sk-min corners "
        "assumed: from max"
    )
    camp = build_campus(spec())
    own = write_emt_pack(tmp_path / "own", camp, battery=battery_from_campus(camp))
    assert by_label(own)["skmin_xrmin"]["source"] == "caller's battery"


def test_a_battery_holding_a_non_case_is_refused(tmp_path):
    with pytest.raises(ContractError, match="not a Case"):
        write_emt_pack(tmp_path, build_campus(spec()), battery=("flat",))


def test_schedule_zero_sequence_present_and_absent(tmp_path):
    camp = build_campus(spec())
    battery = battery_from_campus(camp)
    sched = {
        r["case_id"]: r
        for r in rows(
            write_emt_pack(tmp_path / "a", camp, battery=battery).path,
            "case_schedule.json",
        )
    }
    c = next(c for c in battery if c.kind == "fault_1ph")
    _, z0 = thevenin_impedances_ohm(c.grid, 110.0)
    assert (sched[c.case_id]["r0_ohm"], sched[c.case_id]["x0_ohm"]) == (
        z0.real,
        z0.imag,
    )
    flat = Case(
        "flat_only",
        "flat",
        "04/05",
        Thevenin("g", 2000.0, 8.0),
        1.0,
        10.0,
        ("v_poc_pu", "connected"),
    )
    r = rows(
        write_emt_pack(tmp_path / "b", camp, battery=(flat,)).path, "case_schedule.json"
    )[0]
    assert r["r0_ohm"] is None and r["x0_ohm"] is None


def _literal(text, name):
    for n in ast.parse(text).body:
        if isinstance(n, ast.Assign) and n.targets[0].id == name:
            return ast.literal_eval(n.value)
    raise AssertionError(name)


def test_stub_maps_every_column_that_tells_cases_apart(tmp_path):
    _, text = _stub(tmp_path)
    events = _literal(text, "EVENT_KEYS")
    assert {"kind", "t_event_s", "fault_r_ohm", "fault_x_ohm", *PARAM_NAMES} <= set(
        events
    )
    assert set(_literal(text, "SOURCE_KEYS")) == {"r_ohm", "x_ohm", "r0_ohm", "x0_ohm"}


def test_manifest_and_thevenin_keys_are_pinned(tmp_path):
    p = pack(tmp_path)
    m = json.loads((p.path / "manifest.json").read_text())
    assert set(m) == {
        "pack",
        "pack_version",
        "contract_version",
        "f_hz",
        "pcc_bus",
        "vn_kv",
        "files",
        "data_hash",
        "case_ids",
        "channels",
        "outputs",
        "assumptions",
        "z0_assumed",
        "to_be_supplied",
        "trgc",
    }
    assert (m["pack"], m["pack_version"], m["f_hz"], m["pcc_bus"], m["vn_kv"]) == (
        "pscad_emt",
        "1",
        50.0,
        "PCC",
        110.0,
    )
    assert m["trgc"] == ["02", "03", "05", "07", "17", "19", "20", "21", "22", "23"]
    assert m["to_be_supplied"] == "to_be_supplied.csv"
    assert len(m["assumptions"]) == 1  # c note; no Z0 assumed when both cases hold it
    th = rows(p.path, "thevenin_set.json")
    assert set(th) == {"note", "stage_note", "pcc_bus", "vn_kv", "sources", "stages"}
    assert (th["note"], th["pcc_bus"], th["vn_kv"]) == (C_NOTE, "PCC", 110.0)
    assert th["stage_note"] == emt_pack.STAGE_NOTE


def test_each_battery_corner_is_one_row(tmp_path):
    srcs = rows(pack(tmp_path).path, "thevenin_set.json")["sources"]
    labels = [r["label"] for r in srcs if r["group"] == "battery"]
    assert sorted(labels) == [
        "skmax_xrmax",
        "skmax_xrmin",
        "skmin_xrmax",
        "skmin_xrmin",
    ]


def test_r0x0_without_x0x_lists_only_the_missing_one(tmp_path):
    p = pack(tmp_path, spec(x0x_min=None))
    assert list(grid_gaps(p)["param"]) == ["x0x_min"]


def test_json_files_have_sorted_keys(tmp_path):
    p = pack(tmp_path)
    for name in ("manifest.json", "thevenin_set.json", "case_schedule.json"):
        text = (p.path / name).read_text()
        assert text == json.dumps(json.loads(text), sort_keys=True, indent=1) + "\n", (
            name
        )


# --- the generated mhi.pscad stub, run against a fake module (GC-06d) -----------


class _FakeComponent:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def parameters(self, **kw):
        self.log.append(("component", self.name, kw))


class _FakeProject(_FakeComponent):
    def find(self, name):
        return _FakeComponent(name, self.log)

    def run(self):
        self.log.append(("run", self.name, {}))


class _FakePscad:
    def __init__(self, log):
        self.log = log

    def load(self, workspace):
        self.log.append(("load", workspace, {}))

    def project(self, name):
        return _FakeProject(name, self.log)

    def quit(self):
        self.log.append(("quit", "", {}))


def _run_stub(tmp_path, monkeypatch, argv=("ws.pswx", "plant")):
    """Run the pack's stub in-process with ``mhi.pscad`` replaced by a fake."""
    import importlib.util
    import sys
    import types

    p = pack(tmp_path)
    log = []
    fake = types.ModuleType("mhi.pscad")
    fake.launch = lambda: _FakePscad(log)
    pkg = types.ModuleType("mhi")
    pkg.pscad = fake
    monkeypatch.setitem(sys.modules, "mhi", pkg)
    monkeypatch.setitem(sys.modules, "mhi.pscad", fake)
    spec_ = importlib.util.spec_from_file_location(
        "run_schedule_under_test", p.path / "run_schedule.py"
    )
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    mod.main(list(argv))
    return p, mod, log


def test_stub_runs_one_project_run_per_case_with_the_case_id_as_output(
    tmp_path, monkeypatch
):
    p, mod, log = _run_stub(tmp_path, monkeypatch)
    cases = json.loads((p.path / "case_schedule.json").read_text())
    assert len(cases) > 1
    runs = [e for e in log if e[0] == "run"]
    assert len(runs) == len(cases)
    outputs = [
        e[2]["output_filename"]
        for e in log
        if e[0] == "component" and e[1] == "plant" and "output_filename" in e[2]
    ]
    assert all(c["output_name"] == c["case_id"] for c in cases)
    assert outputs == [c["case_id"] + ".out" for c in cases]
    assert log[0][:2] == ("load", "ws.pswx")
    assert log[-1][0] == "quit"
    assert (p.path / "results").is_dir()


def test_stub_sets_each_case_s_source_values_on_the_named_component(
    tmp_path, monkeypatch
):
    p, mod, log = _run_stub(tmp_path, monkeypatch, ("ws.pswx", "plant"))
    cases = json.loads((p.path / "case_schedule.json").read_text())
    src = [e for e in log if e[0] == "component" and e[1] == "GRID_SOURCE"]
    with_r = [c for c in cases if c.get("r_ohm") is not None]
    assert with_r and len(src) == len(with_r)
    for e, c in zip(src, with_r):
        assert e[2]["R"] == c["r_ohm"] and e[2]["X"] == c["x_ohm"]
        if c.get("r0_ohm") is None:
            assert "R0" not in e[2]  # a case without zero sequence sets none


def test_stub_only_reads_schedule_columns_the_pack_writes(tmp_path, monkeypatch):
    p, mod, log = _run_stub(tmp_path, monkeypatch)
    stub_columns = (
        set(mod.SOURCE_KEYS) | set(mod.EVENT_KEYS) | {"t_end_s", "output_name"}
    )
    assert stub_columns <= set(SCHEDULE_COLUMNS), stub_columns - set(SCHEDULE_COLUMNS)
    cases = json.loads((p.path / "case_schedule.json").read_text())
    assert all(set(c) <= set(SCHEDULE_COLUMNS) for c in cases)
    # the CSV the pack writes beside the JSON carries the same columns
    header = (p.path / "case_schedule.csv").read_text().splitlines()[0]
    assert header.split(",") == SCHEDULE_COLUMNS


def test_stub_says_what_connected_means_only_when_the_contract_has_it(monkeypatch):
    from gridspine.handoff.emt_pack import CONNECTED_NOTE, stub_text

    monkeypatch.setattr(emt_pack, "EMT_CONTRACT_VERSION", "1")
    assert "connected" not in stub_text(3)
    monkeypatch.setattr(emt_pack, "EMT_CONTRACT_VERSION", "2")
    text = stub_text(3)
    assert text.count(CONNECTED_NOTE) == 1
    head = ast.get_docstring(ast.parse(text))
    assert CONNECTED_NOTE in head
    assert "momentary cessation or current blocking is not a trip" in head
    compile(text, "run_schedule.py", "exec")


def test_lv_pcc_takes_c_min_095_on_sk_max_rows(tmp_path):
    # GC-14 F2: a 0.4 kV PCC, Sk 20 / 10 MVA. Sk-max rows: c 0.95, |Z| = 0.95 * 0.16 / 20 =
    # 0.0076 ohm; Sk-min rows: c 1.1, |Z| = 1.1 * 0.16 / 10 = 0.0176 ohm.
    s = spec(sk_max_mva=20.0, sk_min_mva=10.0)
    c = s["campus"]
    c["pcc"]["vn_kv"] = 0.4
    c["buses"] = {b: {"vn_kv": 0.4} for b in c["buses"]}
    for tr in c["transformers"].values():
        tr["vn_hv_kv"], tr["vn_lv_kv"] = t(0.42, "datasheet"), t(0.4, "datasheet")
    rows_ = by_label(pack(tmp_path, s))
    want = {
        "max": (0.95, 0.0076),
        "min": (1.1, 0.0176),
        "skmax_xrmin": (0.95, 0.0076),
        "skmax_xrmax": (0.95, 0.0076),
        "skmin_xrmin": (1.1, 0.0176),
        "skmin_xrmax": (1.1, 0.0176),
    }
    assert set(rows_) == set(want)
    for label, (cv, z) in want.items():
        r = rows_[label]
        assert r["vn_kv"] == 0.4 and r["c"] == cv, label
        assert math.hypot(r["r_ohm"], r["x_ohm"]) == pytest.approx(z, rel=1e-12), label


# --- GC-13: plant ratings bound by the data hash ------------------------------------------------


def test_ratings_are_written_hashed_and_change_the_data_hash(tmp_path):
    from gridspine.handoff.test_battery import RATINGS_JSON, PackRatings
    from gridspine.readback.pack import judge_pack

    r = PackRatings(90.0, 99.0, "plant nameplate (test)", "a" * 64)
    p = pack(tmp_path / "a", ratings=r)
    m = p.manifest
    assert RATINGS_JSON in m["files"]
    assert json.loads((p.path / RATINGS_JSON).read_text()) == r.as_dict()
    assert {k: m[k] for k in r.as_dict()} == r.as_dict()
    assert m["data_hash"] != pack(tmp_path / "none").manifest["data_hash"]
    assert m["data_hash"] == read_pack_hash(m["files"])
    other = PackRatings(91.0, 99.0, "plant nameplate (test)", "a" * 64)
    assert m["data_hash"] != pack(tmp_path / "b", ratings=other).manifest["data_hash"]
    assert m["data_hash"] == pack(tmp_path / "c", ratings=r).manifest["data_hash"]
    # read-back judges the written pack on its own ratings, with no caller input
    header = judge_pack(p.path, {})["header"]
    assert (
        header["ratings"]["rated_mw"] == 90.0 and header["ratings"]["rated_mva"] == 99.0
    )
    assert header["rating_source"] == "plant nameplate (test)"
    assert header["campus_data_hash"] == "a" * 64
    with pytest.raises(ContractError, match="PackRatings"):
        pack(tmp_path / "d", ratings={"rated_mw": 90.0})


#: master's EMT pack (before GC-13) for ``pack(tmp_path)``: its data_hash and manifest SHA-256.
MASTER_DATA_HASH = "1799b3857f44f1c9ff9b9b3704572bfd58a5200b613d5cef6cfa867b6f8809e5"
MASTER_MANIFEST_SHA = "c830bd4549965f2354c2d8fec2442239536883c961f7db3df52dd2f7ba0b7d9f"


def read_pack_hash(files):
    from gridspine.readback.pack import _files_hash

    return _files_hash(files)


def test_a_pack_without_ratings_is_byte_equal_to_master(tmp_path):
    # GK A R2: ratings=None writes what master wrote; no ratings file, no rating keys
    from gridspine.handoff.test_battery import RATING_KEYS, RATINGS_JSON

    p = pack(tmp_path)
    assert p.manifest["data_hash"] == MASTER_DATA_HASH
    assert (
        hashlib.sha256((p.path / "manifest.json").read_bytes()).hexdigest()
        == MASTER_MANIFEST_SHA
    )
    assert (
        RATINGS_JSON not in p.manifest["files"] and not (p.path / RATINGS_JSON).exists()
    )
    assert not set(RATING_KEYS) & set(p.manifest)
