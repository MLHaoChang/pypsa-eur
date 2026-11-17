"""GC-07 part 5: pack read-back (``readback/pack.py``, ``drivers/readback.py``)."""

import csv
import hashlib
import json

import numpy as np
import pytest

from gridspine.drivers.readback import (
    RATED_MW_SOURCE,
    judge_pack_results,
    run_ratings,
)
from gridspine.handoff.test_battery import (
    CHANNELS,
    CONTRACT_VERSION,
    Case,
    Thevenin,
    battery_json,
)
from gridspine.readback.pack import (
    KNOWN_CONTRACT_VERSIONS,
    KNOWN_PACKS,
    NO_CONNECTED,
    ONE_PHASE,
    Ratings,
    judge_pack,
    status_counts,
)
from gridspine.schema.contracts import ContractError

CITE = {"clause": "synthetic test clause", "source": "assumed"}
PROFILE = {
    "lvrt_curve": {
        **CITE,
        "points": [[0.0, 0.2], [0.15, 0.2], [0.15, 0.5], [0.6, 0.85], [0.6, 0.9]],
    },
    "reactive_current_injection": {
        **CITE,
        "k_gain": 2.0,
        "deadband_pu": 0.1,
        "target_pct": 90.0,
        "rise_time_s": 0.03,
    },
    "active_power_recovery": {**CITE, "recovery_pct": 90.0, "time_s": 0.25},
    "setpoint_step_response": {
        **CITE,
        "rise_level_pct": 90.0,
        "rise_time_s": 0.06,
        "settling_time_s": 0.2,
        "settling_band_pct": 2.0,
        "overshoot_max_pct": 10.0,
    },
    "frequency_droop": {
        **CITE,
        "droop_pct": 4.0,
        "deadband_hz": 0.2,
        "nominal_hz": 50.0,
        "tolerance_pct": 0.5,
    },
}
RATINGS = Ratings(100.0, "test plant", 50.0, "test plant")
T = np.round(np.arange(0.0, 1.5 + 1e-9, 1e-3), 10)
GRID = Thevenin("skmin_xrmin", 100.0, 3.0, 1.0, 3.0)
FAULT = (T >= 0.2) & (T < 0.35)


def _case(case_id, kind, params, trgc="07"):
    return Case(case_id, kind, trgc, GRID, 0.2, 1.5, tuple(CHANNELS), params)


def _rise(y0, y1):
    """y0 before 0.2 s, then a ramp to y1 that reaches 90 % of the change 50 ms in."""
    return y0 + (y1 - y0) * np.clip((T - 0.2) * 18.0, 0.0, 1.0)


def _ride_through(connected=True):
    ch = {
        "v_poc_pu": ("pu", np.where(FAULT, 0.3, 1.0)),
        "iq_pu": ("pu", np.where(FAULT, 1.2, 0.0)),
        "p_poc_mw": (
            "MW",
            np.where(
                T < 0.2,
                100.0,
                np.where(FAULT, 0.0, 100.0 * np.clip((T - 0.35) / 0.2, 0, 1)),
            ),
        ),
    }
    if connected:
        ch["connected"] = ("status", np.ones_like(T))
    return ch


def _csv(path, channels, times=T):
    names = list(channels)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time [s]"] + [f"{n} [{channels[n][0]}]" for n in names])
        for k, t in enumerate(times):
            w.writerow(
                [repr(float(t))] + [repr(float(channels[n][1][k])) for n in names]
            )


def _pscad(results, case_id, channels, numbered=False):
    names = list(channels)
    (results / f"{case_id}.inf").write_text(
        "".join(
            f'PGB({i}) Output Desc="{n}" Group="Main" Max=2.0 Min=-2.0 Units="{channels[n][0]}"\n'
            for i, n in enumerate(names, start=1)
        )
    )
    data = np.column_stack([T] + [channels[n][1] for n in names])
    out = results / (f"{case_id}_01.out" if numbered else f"{case_id}.out")
    np.savetxt(out, data)


def _pack(tmp_path, cases, manifest=None):
    pack = tmp_path / "pack"
    (pack / "results").mkdir(parents=True)
    text = battery_json(cases)
    (pack / "battery.json").write_text(text, newline="\n")
    m = {
        "pack": "pscad_emt",
        "pack_version": "1",
        "contract_version": CONTRACT_VERSION,
        "files": {"battery.json": hashlib.sha256(text.encode()).hexdigest()},
        "data_hash": "d" * 64,
        **(manifest or {}),
    }
    (pack / "manifest.json").write_text(json.dumps(m))
    return pack


def _by_id(judgment):
    return {c["case_id"]: c for c in judgment["cases"]}


def _rows(case):
    return {r["check"]: r for r in case["rows"]}


# --- end to end -----------------------------------------------------------------------------------------------


def _battery():
    return [
        _case(
            "fault_3ph_skmin_xrmin_v030",
            "fault_3ph",
            {"v_retained_pu": 0.3, "duration_s": 0.15},
        ),
        _case(
            "fault_1ph_skmin_xrmin_v030",
            "fault_1ph",
            {"v_retained_pu": 0.3, "duration_s": 0.15},
        ),
        _case("p_step_skmin_xrmin_up", "p_step", {"step_pu": 0.1}, "06"),
        _case("p_step_skmin_xrmin_down", "p_step", {"step_pu": -0.1}, "06"),
        _case("q_step_skmin_xrmin_up", "q_step", {"step_pu": 0.1}, "06"),
        _case("f_step_skmin_xrmin_up", "f_step", {"step_hz": 0.5}, "06"),
        _case("v_step_skmin_xrmin_up", "v_step", {"step_pu": 0.05}, "06"),
        _case("flat_skmin_xrmin", "flat", {}, "06"),
    ]


def _full_pack(tmp_path):
    pack = _pack(tmp_path, _battery())
    res = pack / "results"
    _csv(res / "fault_3ph_skmin_xrmin_v030.csv", _ride_through())
    _pscad(res, "fault_1ph_skmin_xrmin_v030", _ride_through(connected=False))
    _pscad(
        res,
        "p_step_skmin_xrmin_up",
        {"p_poc_mw": ("MW", _rise(50.0, 60.0))},
        numbered=True,
    )
    slow = 50.0 - 10.0 * np.clip((T - 0.2) * 5.0, 0.0, 1.0)  # 90 % at 180 ms: too slow
    _pscad(res, "p_step_skmin_xrmin_down", {"p_poc_mw": ("MW", slow)})
    _csv(res / "q_step_skmin_xrmin_up.csv", {"q_poc_mvar": ("Mvar", _rise(5.0, 10.0))})
    f = np.where(T < 0.2, 50.0, 50.5)
    p = np.where(T < 0.2, 50.0, 35.0)  # (0.5 - 0.2) / 50 / 0.15 = 4 %
    _csv(res / "f_step_skmin_xrmin_up.csv", {"f_hz": ("Hz", f), "p_poc_mw": ("MW", p)})
    (res / "notes.txt").write_text("not a result")
    (res / "flat_skmin_xrmin.csv").write_text("time [s]\n0.0\n")
    return pack


def test_end_to_end_status_counts(tmp_path):
    j = judge_pack(_full_pack(tmp_path), PROFILE, RATINGS)
    cases = _by_id(j)
    assert {k: c["status"] for k, c in cases.items()} == {
        "fault_3ph_skmin_xrmin_v030": "pass",
        "fault_1ph_skmin_xrmin_v030": "unknown",
        "p_step_skmin_xrmin_up": "pass",
        "p_step_skmin_xrmin_down": "fail",
        "q_step_skmin_xrmin_up": "pass",
        "f_step_skmin_xrmin_up": "pass",
        "v_step_skmin_xrmin_up": "unknown",
        "flat_skmin_xrmin": "unknown",
    }
    assert status_counts(j) == {"pass": 4, "fail": 1, "unknown": 3}
    assert cases["v_step_skmin_xrmin_up"]["detail"] == "no result file"
    assert "not judged by GC-07" in cases["flat_skmin_xrmin"]["detail"]
    assert cases["flat_skmin_xrmin"]["rows"] == []
    assert j["header"]["extra_files"] == ["notes.txt"]
    assert j["header"]["connected"] == NO_CONNECTED


def test_one_phase_fault_says_positive_sequence_and_why_connection_is_unknown(tmp_path):
    j = judge_pack(_full_pack(tmp_path), PROFILE, RATINGS)
    rows = _rows(_by_id(j)["fault_1ph_skmin_xrmin_v030"])
    assert all(ONE_PHASE in r["detail"] for r in rows.values())
    sc = rows["stays_connected"]
    assert sc["status"] == "unknown" and NO_CONNECTED in sc["detail"]
    others = [r for k, r in rows.items() if k != "stays_connected"]
    assert others and not any(NO_CONNECTED in r["detail"] for r in others)
    three = _rows(_by_id(j)["fault_3ph_skmin_xrmin_v030"])
    assert not any(ONE_PHASE in r["detail"] for r in three.values())
    assert three["stays_connected"]["status"] == "pass"
    assert not any(NO_CONNECTED in r["detail"] for r in three.values())


def test_every_row_and_case_carries_the_pack_binding(tmp_path):
    j = judge_pack(_full_pack(tmp_path), PROFILE, RATINGS)
    assert j["header"]["data_hash"] == "d" * 64
    assert j["header"]["contract_version"] == CONTRACT_VERSION
    assert j["header"]["ratings"]["mw_source"] == "test plant"
    for c in j["cases"]:
        assert c["data_hash"] == "d" * 64 and c["contract_version"] == CONTRACT_VERSION
        for r in c["rows"]:
            assert (
                r["data_hash"] == "d" * 64 and r["contract_version"] == CONTRACT_VERSION
            )


# --- ratings --------------------------------------------------------------------------------------------------


def _one(tmp_path, case, name, channels, ratings):
    pack = _pack(tmp_path, [case])
    _csv(pack / "results" / f"{name}.csv", channels)
    return _by_id(judge_pack(pack, PROFILE, ratings))[name]


def test_mw_to_pu_by_hand(tmp_path):
    # 50 MW on 200 MW rated is 0.25 pu; +0.1 pu commands 0.35 pu (70 MW); 60 MW falls short
    c = _one(
        tmp_path,
        _case("p_step_skmin_xrmin_up", "p_step", {"step_pu": 0.1}, "06"),
        "p_step_skmin_xrmin_up",
        {"p_poc_mw": ("MW", _rise(50.0, 60.0))},
        Ratings(200.0, "hand"),
    )
    r = _rows(c)["settling_time"]
    assert r["status"] == "fail"
    assert "target 0.35 pu = pre-step mean 0.25 pu" in r["detail"]
    assert "commanded step_pu 0.1" in r["detail"]


def test_mvar_to_pu_by_hand(tmp_path):
    # 4 Mvar on 40 MVA rated is 0.1 pu; +0.1 pu commands 0.2 pu (8 Mvar), reached
    c = _one(
        tmp_path,
        _case("q_step_skmin_xrmin_up", "q_step", {"step_pu": 0.1}, "06"),
        "q_step_skmin_xrmin_up",
        {"q_poc_mvar": ("Mvar", _rise(4.0, 8.0))},
        Ratings(1.0, "unused", 40.0, "hand"),
    )
    assert c["status"] == "pass"
    assert "target 0.2 pu = pre-step mean 0.1 pu" in _rows(c)["rise_time"]["detail"]


def test_p_based_rows_name_the_rated_p_base(tmp_path):
    j = judge_pack(_full_pack(tmp_path), PROFILE, RATINGS)
    assert j["header"]["rated_p_base"] == "rated P = test plant"
    assert j["header"]["rated_mva_base"] == "rated MVA = test plant"
    cases = _by_id(j)
    for cid in ("p_step_skmin_xrmin_up", "f_step_skmin_xrmin_up"):
        assert all("rated P = test plant" in r["detail"] for r in cases[cid]["rows"])
    fault = _rows(cases["fault_3ph_skmin_xrmin_v030"])
    assert "rated P = test plant" in fault["active_power_recovery"]["detail"]
    assert "rated P" not in fault["reactive_current_rise"]["detail"]
    assert not any(
        "rated P" in r["detail"] for r in cases["q_step_skmin_xrmin_up"]["rows"]
    )
    j = judge_pack(
        _full_pack(tmp_path / "b"), PROFILE, Ratings(rated_mva=50.0, mva_source="m")
    )
    assert j["header"]["rated_p_base"] == "no rated P"
    assert j["header"]["rated_mva_base"] == "rated MVA = m"
    assert _by_id(j)["p_step_skmin_xrmin_up"]["detail"] == (
        "no rated P given: 'p_poc_mw' cannot be put in pu"
    )
    j = judge_pack(_full_pack(tmp_path / "c"), PROFILE, Ratings(100.0, "x"))
    assert j["header"]["rated_mva_base"] == "no rated MVA"


def test_mvar_is_never_put_in_pu_of_the_mw_rating(tmp_path):
    # 4 Mvar is 0.1 pu of 40 MVA but 0.04 pu of 100 MW; only the first may appear
    c = _one(
        tmp_path,
        _case("q_step_skmin_xrmin_up", "q_step", {"step_pu": 0.1}, "06"),
        "q_step_skmin_xrmin_up",
        {"q_poc_mvar": ("Mvar", _rise(4.0, 8.0))},
        Ratings(100.0, "mw", 40.0, "mva"),
    )
    assert "pre-step mean 0.1 pu" in _rows(c)["rise_time"]["detail"]
    assert c["status"] == "pass"


def test_a_missing_rating_is_unknown_never_mw_as_pu(tmp_path):
    c = _one(
        tmp_path,
        _case("q_step_skmin_xrmin_up", "q_step", {"step_pu": 0.1}, "06"),
        "q_step_skmin_xrmin_up",
        {"q_poc_mvar": ("Mvar", _rise(4.0, 8.0))},
        Ratings(100.0, "hand"),
    )
    assert c["status"] == "unknown" and "no rated MVA given" in c["detail"]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"rated_mw": 0.0, "mw_source": "x"}, "rated_mw must be a finite number > 0"),
        ({"rated_mw": -1.0, "mw_source": "x"}, "rated_mw must be a finite number > 0"),
        (
            {"rated_mw": float("nan"), "mw_source": "x"},
            "rated_mw must be a finite number > 0",
        ),
        (
            {"rated_mw": float("inf"), "mw_source": "x"},
            "rated_mw must be a finite number > 0",
        ),
        ({"rated_mw": True, "mw_source": "x"}, "rated_mw must be a finite number > 0"),
        ({"rated_mw": "100", "mw_source": "x"}, "rated_mw must be a finite number > 0"),
        ({"rated_mw": 100.0}, "rated_mw needs a stated source"),
        ({"rated_mw": 100.0, "mw_source": "  "}, "rated_mw needs a stated source"),
        ({"rated_mw": 100.0, "mw_source": 5}, "rated_mw needs a stated source"),
        (
            {"rated_mva": 0.0, "mva_source": "x"},
            "rated_mva must be a finite number > 0",
        ),
        ({"rated_mva": 50.0}, "rated_mva needs a stated source"),
        (
            {"rated_mva": -5.0, "mva_source": "x"},
            "rated_mva must be a finite number > 0",
        ),
        (
            {"rated_mva": float("nan"), "mva_source": "x"},
            "rated_mva must be a finite number > 0",
        ),
        (
            {"rated_mva": float("inf"), "mva_source": "x"},
            "rated_mva must be a finite number > 0",
        ),
        ({"rated_mva": 50.0, "mva_source": ""}, "rated_mva needs a stated source"),
    ],
)
def test_rating_refusals(kwargs, match):
    with pytest.raises(ContractError, match=match):
        Ratings(**kwargs)


def test_tiny_positive_rating_is_accepted():
    assert Ratings(float(np.nextafter(0.0, 1.0)), "edge").rated_mw > 0


def test_run_ratings_reads_the_campus_connection(tmp_path):
    (tmp_path / "campus.yaml").write_text("pcc:\n  p_connection_mw: 80.0\n")
    r = run_ratings(tmp_path)
    assert r.rated_mw == 80.0 and r.mw_source == RATED_MW_SOURCE
    assert r.rated_mva is None
    r = run_ratings(
        tmp_path,
        rated_mw=90.0,
        mw_source="nameplate",
        rated_mva=95.0,
        mva_source="nameplate",
    )
    assert (r.rated_mw, r.mw_source, r.rated_mva) == (90.0, "nameplate", 95.0)
    with pytest.raises(ContractError, match="rated_mw needs a stated source"):
        run_ratings(tmp_path, rated_mw=90.0)
    assert run_ratings(tmp_path / "none").rated_mw is None
    for text in ("", "name: x\n", "pcc:\n", "pcc:\n  vn_kv: 33\n"):
        (tmp_path / "campus.yaml").write_text(text)
        assert run_ratings(tmp_path).rated_mw is None


# --- pack binding ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pack", "version"), [(p, v) for p, vs in KNOWN_PACKS.items() for v in vs]
)
@pytest.mark.parametrize("contract", KNOWN_CONTRACT_VERSIONS)
def test_each_known_version_is_accepted(tmp_path, pack, version, contract):
    p = _pack(
        tmp_path,
        [_case("flat_skmin_xrmin", "flat", {}, "06")],
        {"pack": pack, "pack_version": version, "contract_version": contract},
    )
    assert judge_pack(p, PROFILE, RATINGS)["header"]["pack_version"] == version


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"pack": "psse"}, "is not one this reader knows"),
        ({"pack_version": "2"}, "is not one this reader knows"),
        (
            {"contract_version": "2"},
            "contract_version '2' is not one this reader knows",
        ),
        ({"files": {}}, "lists no battery.json"),
        ({"files": {"battery.json": "0" * 64}}, "SHA-256 is not the manifest's entry"),
        ({"data_hash": ""}, "needs files"),
        ({"files": None}, "needs files"),
        ({"data_hash": 123}, "needs files"),
    ],
)
def test_manifest_refusals(tmp_path, change, match):
    p = _pack(tmp_path, [_case("flat_skmin_xrmin", "flat", {}, "06")], change)
    with pytest.raises(ContractError, match=match):
        judge_pack(p, PROFILE, RATINGS)


def test_no_manifest_or_battery_is_refused(tmp_path):
    p = _pack(tmp_path, [_case("flat_skmin_xrmin", "flat", {}, "06")])
    (p / "battery.json").unlink()
    with pytest.raises(ContractError, match="battery.json is missing"):
        judge_pack(p, PROFILE, RATINGS)
    (p / "manifest.json").unlink()
    with pytest.raises(ContractError, match="no manifest.json"):
        judge_pack(p, PROFILE, RATINGS)
    (p / "manifest.json").write_text("[")
    with pytest.raises(ContractError, match="does not parse"):
        judge_pack(p, PROFILE, RATINGS)
    (p / "manifest.json").write_text("[]")
    with pytest.raises(ContractError, match="is not an object"):
        judge_pack(p, PROFILE, RATINGS)
    with pytest.raises(ContractError, match="ratings must be a Ratings"):
        judge_pack(p, PROFILE, {"rated_mw": 1.0})


# --- result files ---------------------------------------------------------------------------------------------


P_UP = _case("p_step_skmin_xrmin_up", "p_step", {"step_pu": 0.1}, "06")


def test_both_pscad_namings_are_ambiguous(tmp_path):
    pack = _pack(tmp_path, [P_UP])
    ch = {"p_poc_mw": ("MW", _rise(50.0, 60.0))}
    _pscad(pack / "results", P_UP.case_id, ch)
    _pscad(pack / "results", P_UP.case_id, ch, numbered=True)
    with pytest.raises(ContractError, match="ambiguous"):
        judge_pack(pack, PROFILE, RATINGS)


def test_pscad_and_csv_together_are_ambiguous(tmp_path):
    pack = _pack(tmp_path, [P_UP])
    ch = {"p_poc_mw": ("MW", _rise(50.0, 60.0))}
    _pscad(pack / "results", P_UP.case_id, ch)
    _csv(pack / "results" / f"{P_UP.case_id}.csv", ch)
    with pytest.raises(ContractError, match="ambiguous"):
        judge_pack(pack, PROFILE, RATINGS)


def test_no_results_directory_reads_unknown(tmp_path):
    pack = _pack(tmp_path, [P_UP])
    (pack / "results").rmdir()
    j = judge_pack(pack, PROFILE, RATINGS)
    assert (
        j["cases"][0]["status"] == "unknown"
        and j["cases"][0]["detail"] == "no result file"
    )
    assert j["header"]["extra_files"] == []


@pytest.mark.parametrize(
    ("case", "channels", "missing"),
    [
        (
            _case(
                "fault_3ph_skmin_xrmin_v030",
                "fault_3ph",
                {"v_retained_pu": 0.3, "duration_s": 0.15},
            ),
            {"iq_pu": ("pu", np.zeros_like(T))},
            "v_poc_pu",
        ),
        (P_UP, {"v_poc_pu": ("pu", np.ones_like(T))}, "p_poc_mw"),
        (
            _case("q_step_skmin_xrmin_up", "q_step", {"step_pu": 0.1}, "06"),
            {"p_poc_mw": ("MW", np.ones_like(T))},
            "q_poc_mvar",
        ),
        (
            _case("v_step_skmin_xrmin_up", "v_step", {"step_pu": 0.05}, "06"),
            {"p_poc_mw": ("MW", np.ones_like(T))},
            "v_poc_pu",
        ),
        (
            _case("f_step_skmin_xrmin_up", "f_step", {"step_hz": 0.5}, "06"),
            {"p_poc_mw": ("MW", np.ones_like(T))},
            "f_hz",
        ),
        (
            _case("f_step_skmin_xrmin_up", "f_step", {"step_hz": 0.5}, "06"),
            {"f_hz": ("Hz", np.ones_like(T))},
            "p_poc_mw",
        ),
    ],
)
def test_a_missing_contract_channel_is_unknown_naming_it(
    tmp_path, case, channels, missing
):
    c = _one(tmp_path, case, case.case_id, channels, RATINGS)
    assert c["status"] == "unknown" and f"'{missing}'" in c["detail"]


def test_a_wrong_unit_is_unknown_naming_it(tmp_path):
    c = _one(
        tmp_path, P_UP, P_UP.case_id, {"p_poc_mw": ("kW", _rise(5e4, 6e4))}, RATINGS
    )
    assert (
        c["status"] == "unknown"
        and "'p_poc_mw' is in 'kW', expected 'MW'" in c["detail"]
    )


def test_fault_without_p_or_iq_judges_what_it_has(tmp_path):
    case = _case(
        "fault_3ph_skmin_xrmin_v030",
        "fault_3ph",
        {"v_retained_pu": 0.3, "duration_s": 0.15},
    )
    ch = _ride_through()
    del ch["iq_pu"], ch["p_poc_mw"]
    c = _one(tmp_path, case, case.case_id, ch, RATINGS)
    rows = _rows(c)
    assert rows["stays_connected"]["status"] == "pass"
    assert rows["reactive_current_rise"]["status"] == "unknown"
    assert c["status"] == "unknown"


# --- driver ---------------------------------------------------------------------------------------------------


def test_driver_writes_json_and_csv_and_a_rerun_replaces_both(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    pack = _full_pack(tmp_path)
    j = judge_pack_results(run, pack, PROFILE, RATINGS)
    out = run / "compliance"
    doc = json.loads((out / "judgment.json").read_text())
    assert doc == json.loads(json.dumps(j))
    rows = list(csv.DictReader((out / "judgment.csv").open()))
    assert {r["case_id"] for r in rows} == {c.case_id for c in _battery()}
    flat = [r for r in rows if r["case_id"] == "flat_skmin_xrmin"]
    assert len(flat) == 1 and flat[0]["status"] == "unknown" and flat[0]["check"] == ""
    assert all(r["data_hash"] == "d" * 64 for r in rows)
    assert sorted(p.name for p in out.iterdir()) == ["judgment.csv", "judgment.json"]
    # a re-run on a changed result replaces both files
    (pack / "results" / "f_step_skmin_xrmin_up.csv").unlink()
    judge_pack_results(run, pack, PROFILE, RATINGS)
    doc = json.loads((out / "judgment.json").read_text())
    assert _by_id(doc)["f_step_skmin_xrmin_up"]["detail"] == "no result file"
    rows = list(csv.DictReader((out / "judgment.csv").open()))
    f = [r for r in rows if r["case_id"] == "f_step_skmin_xrmin_up"]
    assert len(f) == 1 and f[0]["detail"] == "no result file"
    assert sorted(p.name for p in out.iterdir()) == ["judgment.csv", "judgment.json"]


def test_a_string_pack_path_is_accepted(tmp_path):
    j = judge_pack(str(_full_pack(tmp_path)), PROFILE, RATINGS)
    assert status_counts(j) == {"pass": 4, "fail": 1, "unknown": 3}


def test_extra_files_are_exactly_the_ones_no_case_claims(tmp_path):
    pack = _pack(tmp_path, [P_UP])
    res = pack / "results"
    cid = P_UP.case_id
    _csv(res / f"{cid}.csv", {"p_poc_mw": ("MW", _rise(50.0, 60.0))})
    claimed = []  # only the CSV, the one result present, is claimed
    extra = [
        f"{cid}.txt",  # the case's stem, not a result extension
        "other.csv",  # a result extension, not the case's stem
        f"{cid}_01.csv",  # numbered, but not .out
        f"{cid}_ab.out",  # not a two-digit number
        f"{cid}_001.out",  # three digits
        f"{'z' * len(cid)}_01.out",  # another case's numbering
        "notes",
    ]
    for name in claimed + extra:
        (res / name).write_text("x")
    j = judge_pack(pack, PROFILE, RATINGS)
    assert j["header"]["extra_files"] == sorted(extra)


def test_pscad_files_are_claimed(tmp_path):
    pack = _pack(tmp_path, [P_UP])
    _pscad(
        pack / "results",
        P_UP.case_id,
        {"p_poc_mw": ("MW", _rise(50.0, 60.0))},
        numbered=True,
    )
    j = judge_pack(pack, PROFILE, RATINGS)
    assert j["header"]["extra_files"] == [] and j["cases"][0]["status"] == "pass"


def test_the_pre_step_mean_window_includes_its_start_and_excludes_the_step(tmp_path):
    # 0.1 s before the 0.2 s step: samples 0.100 .. 0.199 (100 of them); 150 MW at 0.100
    p = np.where(T >= 0.2, 60.0, 50.0)
    p = np.where(np.isclose(T, 0.1), 150.0, p)
    c = _one(tmp_path, P_UP, P_UP.case_id, {"p_poc_mw": ("MW", p)}, RATINGS)
    # (99 x 50 + 150) / 100 = 51 MW = 0.51 pu
    assert "pre-step mean 0.51 pu" in _rows(c)["rise_time"]["detail"]


def test_no_sample_before_the_step_is_unknown(tmp_path):
    times = np.array([0.0, 0.05, 0.25, 0.5, 1.0, 1.5])
    pack = _pack(tmp_path, [P_UP])
    _csv(
        pack / "results" / f"{P_UP.case_id}.csv",
        {"p_poc_mw": ("MW", np.full(times.shape, 50.0))},
        times,
    )
    c = judge_pack(pack, PROFILE, RATINGS)["cases"][0]
    assert (
        c["status"] == "unknown"
        and "no sample in the 0.1 s before the step" in c["detail"]
    )
