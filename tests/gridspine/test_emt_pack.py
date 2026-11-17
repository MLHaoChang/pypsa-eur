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
    # min case: |Z| = 121/20 = 6.05 ohm at X/R 2: R = 6.05/sqrt(5) = 2.705642, X = 2 R = 5.411285
    assert s["min"]["r_ohm"] == pytest.approx(2.705642253, abs=1e-6)
    assert s["min"]["x_ohm"] == pytest.approx(5.411284506, abs=1e-6)
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
    assert by_label(p)["max"]["note"] == C_NOTE
    assert "IEC 60909 c not applied" in C_NOTE and "10 % higher" in C_NOTE
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


def test_a_case_without_zero_sequence_is_to_be_supplied_not_borrowed(tmp_path):
    p = pack(tmp_path, spec(x0x_min=None, r0x0_min=None))
    s = by_label(p)
    assert s["min"]["x0_ohm"] is None and s["min"]["z0_over_z1"] is None
    gaps = p.to_be_supplied
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
    assert list(p.to_be_supplied["param"]) == ["r0x0_min"]


def test_without_a_max_zero_sequence_no_schedule_is_written(tmp_path):
    p = pack(tmp_path, spec(x0x_max=None, r0x0_max=None))
    assert not (p.path / "case_schedule.json").exists()
    assert not (p.path / "run_schedule.py").exists()
    assert "x0x_max, r0x0_max" in set(p.to_be_supplied["param"])
    assert p.manifest["case_ids"] == []
    with pytest.raises(EmtPackError, match="max case has no zero sequence"):
        battery_from_campus(build_campus(spec(x0x_max=None, r0x0_max=None)))


def test_battery_z0_is_from_max_and_the_differing_case_is_listed(tmp_path):
    p = pack(tmp_path)
    b = cases_from_json((p.path / "battery.json").read_text())
    assert {(round(c.grid.z0_over_z1, 9), round(c.grid.x0_over_r0, 9)) for c in b} == {
        (3.0, 10.0)
    }
    camp = build_campus(spec())
    on_min = [c.case_id for c in battery_from_campus(camp) if c.grid.sk_mva == 2000.0]
    assert on_min and p.manifest["z0_differs_from_battery"] == {"min": on_min}
    assert any("assumed: from max" in a for a in p.manifest["assumptions"])


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
    assert m["channels"] == list(CHANNELS)
    assert m["contract_version"] == emt_pack.CONTRACT_VERSION
    assert m["files"] and math.isfinite(m["vn_kv"])


# --- review and mutant follow-ups --------------------------------------------


def test_normal_case_without_schema_zero_sequence_is_not_a_gap(tmp_path):
    p = pack(tmp_path, spec(sk_normal_mva=2500.0, rx_normal=0.11))
    assert p.to_be_supplied.empty


def test_x0x_without_r0x0_lists_only_the_missing_one(tmp_path):
    p = pack(tmp_path, spec(r0x0_min=None))
    assert list(p.to_be_supplied["param"]) == ["r0x0_min"]
    assert by_label(p)["min"]["x0_ohm"] is None


def test_a_source_without_zero_sequence_is_not_listed_as_differing(tmp_path):
    p = pack(tmp_path, spec(x0x_min=None, r0x0_min=None))
    assert p.manifest["z0_differs_from_battery"] == {}


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
    assert s["skmin_xrmin"]["source"] == "campus PCC corners; Z0 assumed: from max"
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
        ("v_poc_pu",),
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
        "z0_differs_from_battery",
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
    assert len(m["assumptions"]) == 2
    th = rows(p.path, "thevenin_set.json")
    assert set(th) == {"note", "stage_note", "pcc_bus", "vn_kv", "sources", "stages"}
    assert (th["note"], th["pcc_bus"], th["vn_kv"]) == (C_NOTE, "PCC", 110.0)
    assert th["stage_note"] == emt_pack.STAGE_NOTE
