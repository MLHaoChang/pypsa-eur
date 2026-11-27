"""GC-07 part 5: pack read-back (``readback/pack.py``, ``drivers/readback.py``)."""

import csv
import dataclasses
import hashlib
import json

import numpy as np
import pytest

from gridspine.drivers.readback import (
    RATED_MW_SOURCE,
    judge_pack_results,
    run_data_hash,
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
    read_manifest,
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


V1_CHANNELS = tuple(c for c in CHANNELS if c != "connected")  # contract v1 (GC-12)


def _case(case_id, kind, params, trgc="07"):
    return Case(case_id, kind, trgc, GRID, 0.2, 1.5, V1_CHANNELS, params)


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
        "fault_3ph_skmin_xrmin_v030": "unknown",
        "fault_1ph_skmin_xrmin_v030": "unknown",
        # contract v1 has no 'connected' channel, so no case passes (GK A2 D-2);
        # the v2 twin of this pack is in test_pack_contract2.py
        "p_step_skmin_xrmin_up": "unknown",
        "p_step_skmin_xrmin_down": "fail",
        "q_step_skmin_xrmin_up": "unknown",
        "f_step_skmin_xrmin_up": "unknown",
        "v_step_skmin_xrmin_up": "unknown",
        "flat_skmin_xrmin": "unknown",
    }
    assert status_counts(j) == {"pass": 0, "fail": 1, "unknown": 7}
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
    # a 'connected' column outside the contract is not read (K3, GC-12)
    assert "connected" in _ride_through()
    assert three["stays_connected"]["status"] == "unknown"
    assert NO_CONNECTED in three["stays_connected"]["detail"]


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
    assert _rows(c)["rise_time"]["status"] == "pass"
    assert "target 0.2 pu = pre-step mean 0.1 pu" in _rows(c)["rise_time"]["detail"]


def test_p_based_rows_name_the_rated_p_base(tmp_path):
    j = judge_pack(_full_pack(tmp_path), PROFILE, RATINGS)
    assert j["header"]["rated_p_base"] == "rated P = test plant"
    assert j["header"]["rated_mva_base"] == "rated MVA = test plant"
    cases = _by_id(j)
    for cid in ("p_step_skmin_xrmin_up", "f_step_skmin_xrmin_up"):
        assert all(
            "rated P = test plant" in r["detail"]
            for r in cases[cid]["rows"]
            if r["check"] != "stays_connected"
        )
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
    assert _rows(c)["rise_time"]["status"] == "pass"


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
            {"contract_version": "3"},
            "contract_version '3' is not one this reader knows",
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


@pytest.mark.parametrize(
    "case, channels",
    [
        (
            _case("v_step_skmin_xrmin_up", "v_step", {"step_pu": 0.05}, "06"),
            {"v_poc_pu": ("kV", np.full_like(T, 110.0))},
        ),
        (
            _case(
                "fault_3ph_skmin_xrmin_v030",
                "fault_3ph",
                {"v_retained_pu": 0.3, "duration_s": 0.15},
            ),
            {"v_poc_pu": ("kV", np.where(FAULT, 33.0, 110.0))},
        ),
    ],
)
def test_a_voltage_not_in_pu_is_unknown_naming_it(tmp_path, case, channels):
    c = _one(tmp_path, case, case.case_id, channels, RATINGS)
    assert c["status"] == "unknown" and c["rows"] == []
    assert "'v_poc_pu' is in 'kV', expected 'pu'" in c["detail"]


def test_an_unreadable_result_is_unknown_for_that_case_only(tmp_path):
    pack = _full_pack(tmp_path)
    (pack / "results" / "p_step_skmin_xrmin_down.out").unlink()
    j = judge_pack(pack, PROFILE, RATINGS)
    cases = _by_id(j)
    bad = cases["p_step_skmin_xrmin_down"]
    assert bad["status"] == "unknown" and bad["rows"] == []
    assert bad["detail"].startswith("p_step_skmin_xrmin_down.inf unreadable: ")
    assert _rows(cases["p_step_skmin_xrmin_up"])["settling_time"]["status"] == "pass"


def _bad_records():
    fault = "fault_3ph_skmin_xrmin_v030"
    early = T < 0.25  # ends inside the fault, before clearance
    iq_pct = dict(_ride_through(connected=False))
    iq_pct["iq_pu"] = ("%", iq_pct["iq_pu"][1] * 100.0)
    f = np.where(T < 0.2, 50.0, 50.5)
    p = np.where(T < 0.2, 50.0, 35.0)
    before = T < 0.1  # ends before t_event
    return [
        (
            fault,
            T[early],
            {k: (u, v[early]) for k, (u, v) in _ride_through(False).items()},
        ),
        (fault, T, iq_pct),
        (
            "f_step_skmin_xrmin_up",
            T,
            {"f_hz": ("pu", f / 50.0), "p_poc_mw": ("MW", p)},
        ),
        (
            "p_step_skmin_xrmin_up",
            T[before],
            {"p_poc_mw": ("MW", _rise(50.0, 60.0)[before])},
        ),
    ]


@pytest.mark.parametrize(
    "case_id, times, channels",
    _bad_records(),
    ids=["truncated_fault", "iq_in_percent", "f_hz_in_pu", "ends_before_t_event"],
)
def test_a_case_the_judge_cannot_take_is_unknown_and_the_rest_are_judged(
    tmp_path, case_id, times, channels
):
    good = {
        k: c["status"]
        for k, c in _by_id(
            judge_pack(_full_pack(tmp_path / "a"), PROFILE, RATINGS)
        ).items()
    }
    pack = _full_pack(tmp_path / "b")
    for f in (pack / "results").glob(f"{case_id}*"):
        f.unlink()
    _csv(pack / "results" / f"{case_id}.csv", channels, times)
    cases = _by_id(judge_pack(pack, PROFILE, RATINGS))
    bad = cases[case_id]
    assert bad["status"] == "unknown"
    assert {k: c["status"] for k, c in cases.items() if k != case_id} == {
        k: v for k, v in good.items() if k != case_id
    }
    assert bad["rows"] == [] and bad["detail"]
    if case_id != "p_step_skmin_xrmin_up":
        assert bad["detail"].startswith("not judged: ")


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
    assert rows["stays_connected"]["status"] == "unknown"
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
    assert status_counts(j) == {"pass": 0, "fail": 1, "unknown": 7}
    assert read_manifest(str(tmp_path / "pack"))["data_hash"] == "d" * 64


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
    assert j["header"]["extra_files"] == []
    assert _rows(j["cases"][0])["settling_time"]["status"] == "pass"


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


# --- the GC-06 pack itself (keeps the synthetic manifest in step with emt_pack) -------------------------------


def _emt_pack(tmp_path):
    from gridspine.handoff.emt_pack import battery_from_campus
    from gridspine.ingest.campus import build_campus
    from tests.gridspine.test_emt_pack import pack, spec

    return pack(tmp_path, battery=battery_from_campus(build_campus(spec())))


def test_the_reader_matches_the_emt_pack_writer(tmp_path):
    from gridspine.handoff import emt_pack
    from gridspine.readback import pack as reader

    assert (reader.MANIFEST, reader.BATTERY_JSON, reader.RESULTS_DIR) == (
        emt_pack.MANIFEST,
        emt_pack.BATTERY_JSON,
        emt_pack.RESULTS_DIR,
    )
    assert emt_pack.PACK_VERSION in KNOWN_PACKS["pscad_emt"]
    written = json.loads((_emt_pack(tmp_path).path / "manifest.json").read_text())
    synthetic = json.loads(
        (_pack(tmp_path / "s", [P_UP]) / "manifest.json").read_text()
    )
    assert set(synthetic) <= set(written)
    assert written["pack"] == "pscad_emt"
    assert written["contract_version"] in KNOWN_CONTRACT_VERSIONS


def test_a_written_emt_pack_is_read_and_judged(tmp_path):
    p = _emt_pack(tmp_path).path
    manifest = read_manifest(p)
    assert manifest["files"]["battery.json"]
    ids = manifest["case_ids"]
    fault = next(i for i in ids if i.startswith("fault_3ph_"))
    (p / "results").mkdir(exist_ok=True)
    _csv(p / "results" / f"{fault}.csv", _ride_through())
    j = judge_pack(p, PROFILE, RATINGS)
    cases = _by_id(j)
    assert set(cases) == set(ids)
    assert j["header"]["data_hash"] == manifest["data_hash"]
    assert all(
        c["detail"] == "no result file"
        for k, c in cases.items()
        if k != fault
        and c["rows"] == []
        and not k.startswith(("flat", "f_ramp", "phase_jump"))
    )
    # the EMT pack writes contract v2 (GC-12 PR-C), so 'connected' = 1 is read
    assert manifest["contract_version"] == "2"
    assert _rows(cases[fault])["stays_connected"]["status"] == "pass"


# --- GC-13: the pack's own ratings, bound to the campus run ---------------------------------------

SOURCE = "plant nameplate (test)"
RATED = {
    "rated_mw": 100.0,
    "rated_mva": 50.0,
    "rating_source": SOURCE,
    "campus_data_hash": "a" * 64,
}
PACK_RATINGS = Ratings(100.0, SOURCE, 50.0, SOURCE)
P_BASE = f"rated P 100 MW = {SOURCE}; pack ratings.json"
Q_BASE = f"rated MVA 50 MVA = {SOURCE}; pack ratings.json"


def _files_hash(files):
    text = "".join(f"{n}:{h}\n" for n, h in sorted(files.items()))
    return hashlib.sha256(text.encode()).hexdigest()


def _rated(tmp_path, doc=None, manifest_doc=None, **over):
    """``_full_pack`` written with ratings: ``ratings.json`` hashed, its keys in the manifest."""
    pack = _full_pack(tmp_path)
    doc = {**RATED, **over} if doc is None else doc
    text = json.dumps(doc)
    (pack / "ratings.json").write_text(text)
    m = json.loads((pack / "manifest.json").read_text())
    m["files"]["ratings.json"] = hashlib.sha256(text.encode()).hexdigest()
    m["data_hash"] = _files_hash(m["files"])
    m.update(doc if manifest_doc is None else manifest_doc)
    (pack / "manifest.json").write_text(json.dumps(m))
    return pack


def _edit_manifest(pack, **changes):
    m = json.loads((pack / "manifest.json").read_text())
    m.update(changes)
    (pack / "manifest.json").write_text(json.dumps(m))


def test_a_rated_pack_is_judged_on_its_ratings_without_caller_input(tmp_path):
    pack = _rated(tmp_path)
    j = judge_pack(pack, PROFILE)
    assert j == judge_pack(pack, PROFILE, PACK_RATINGS)
    h = j["header"]
    assert h["ratings"] == {
        "rated_mw": 100.0,
        "mw_source": SOURCE,
        "rated_mva": 50.0,
        "mva_source": SOURCE,
    }
    assert h["rating_source"] == SOURCE and h["campus_data_hash"] == "a" * 64
    assert h["rated_p_base"] == P_BASE and h["rated_mva_base"] == Q_BASE
    by = _by_id(j)
    for cid, base in (
        ("p_step_skmin_xrmin_up", P_BASE),
        ("q_step_skmin_xrmin_up", Q_BASE),
        ("f_step_skmin_xrmin_up", P_BASE),
        ("fault_3ph_skmin_xrmin_v030", P_BASE),
    ):
        rated = [r for r in by[cid]["rows"] if base in r["detail"]]
        assert rated, cid
    # the q_step's three checks read Q in pu of the rated MVA and say so
    q = _rows(by["q_step_skmin_xrmin_up"])
    assert all(
        Q_BASE in q[c]["detail"] for c in ("rise_time", "settling_time", "overshoot")
    )
    assert Q_BASE not in q["stays_connected"]["detail"]


def test_an_unrated_pack_keeps_its_report(tmp_path):
    # GK A R6: no ratings source text on Q rows and no new header keys for a pack without ratings
    j = judge_pack(_full_pack(tmp_path), PROFILE, RATINGS)
    assert "rating_source" not in j["header"] and "campus_data_hash" not in j["header"]
    assert j["header"]["rated_p_base"] == "rated P = test plant"
    assert j["header"]["rated_mva_base"] == "rated MVA = test plant"
    by = _by_id(j)
    assert not any(
        "rated MVA" in r["detail"] for r in by["q_step_skmin_xrmin_up"]["rows"]
    )
    assert not any("None" in r["detail"] for c in j["cases"] for r in c["rows"])
    p = _rows(by["p_step_skmin_xrmin_up"])
    assert "rated P = test plant" in p["rise_time"]["detail"]
    assert all(
        "pack ratings.json" not in r["detail"] for c in j["cases"] for r in c["rows"]
    )
    # and an unrated pack with a fake data_hash still reads (master's path)
    assert read_manifest(_full_pack(tmp_path / "b"))["data_hash"] == "d" * 64


@pytest.mark.parametrize(
    "field, value",
    [
        ("rated_mw", 90.0),
        ("mw_source", "other"),
        ("rated_mva", 40.0),
        ("mva_source", "other"),
    ],
)
def test_caller_ratings_that_differ_from_the_pack_in_any_field_are_refused(
    tmp_path, field, value
):
    caller = Ratings(**{**dataclasses.asdict(PACK_RATINGS), field: value})
    with pytest.raises(ContractError, match="differ from the pack's"):
        judge_pack(_rated(tmp_path), PROFILE, caller)


@pytest.mark.parametrize(
    "field, value",
    [
        ("rated_mw", 120.0),
        ("rated_mva", 60.0),
        ("rating_source", "other"),
        ("campus_data_hash", "b" * 64),
    ],
)
def test_a_manifest_copy_that_differs_from_ratings_json_in_any_field_is_refused(
    tmp_path, field, value
):
    pack = _rated(tmp_path, manifest_doc={**RATED, field: value})
    with pytest.raises(ContractError, match="ratings differ"):
        read_manifest(pack)


@pytest.mark.parametrize(
    "bad", [0, -1.0, float("nan"), float("inf"), "100", True, None]
)
def test_a_pack_rating_that_is_not_finite_and_positive_is_refused(tmp_path, bad):
    for name in ("rated_mw", "rated_mva"):
        pack = _rated(tmp_path / name, **{name: bad})
        with pytest.raises(ContractError, match=f"{name} must be a finite number > 0"):
            judge_pack(pack, PROFILE)


@pytest.mark.parametrize(
    "over, match",
    [
        ({"rating_source": " "}, "rating_source must be stated"),
        (
            {"campus_data_hash": "A" * 64},
            "64 hex digits",
        ),  # lowercase only, as model_hash gives
        ({"campus_data_hash": "a" * 63}, "64 hex digits"),
    ],
)
def test_a_blank_source_or_a_malformed_hash_is_refused(tmp_path, over, match):
    with pytest.raises(ContractError, match=match):
        judge_pack(_rated(tmp_path, **over), PROFILE)


def test_unbound_or_altered_ratings_are_refused(tmp_path):
    # one byte changed in ratings.json, its files entry left: SHA-256 mismatch
    pack = _rated(tmp_path / "a")
    text = (pack / "ratings.json").read_text()
    (pack / "ratings.json").write_text(text.replace("100.0", "100.1"))
    with pytest.raises(ContractError, match="ratings.json's SHA-256"):
        read_manifest(pack)
    # ... its files entry updated too, but not the data_hash: the hash no longer binds it
    m = json.loads((pack / "manifest.json").read_text())
    m["files"]["ratings.json"] = hashlib.sha256(
        (pack / "ratings.json").read_bytes()
    ).hexdigest()
    m["rated_mw"] = 100.1
    (pack / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(
        ContractError, match="data_hash is not the hash of its file list"
    ):
        read_manifest(pack)
    pack = _pack(tmp_path / "c", _battery(), {"rated_mw": 100.0})
    with pytest.raises(ContractError, match="lists no ratings.json"):
        read_manifest(pack)
    pack = _rated(tmp_path / "d")
    (pack / "ratings.json").unlink()
    with pytest.raises(ContractError, match="ratings.json is missing"):
        judge_pack(pack, PROFILE)
    pack = _rated(tmp_path / "e")
    (pack / "ratings.json").write_text(json.dumps({**RATED, "extra": 1}))
    m = json.loads((pack / "manifest.json").read_text())
    m["files"]["ratings.json"] = hashlib.sha256(
        (pack / "ratings.json").read_bytes()
    ).hexdigest()
    m["data_hash"] = _files_hash(m["files"])
    (pack / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ContractError, match="must hold exactly"):
        judge_pack(pack, PROFILE)


def _run(tmp_path, spec=None):
    import yaml

    from tests.gridspine.test_campus import campus_spec

    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    spec = campus_spec() if spec is None else spec
    (run / "campus.yaml").write_text(yaml.safe_dump(spec))
    return run, spec


def test_run_data_hash_is_the_frozen_model_sets_model_hash(tmp_path):
    # GK A R1: a change to run_data_hash or to the freeze's model hash breaks this
    from gridspine.ingest.aggregate import freeze

    run, spec = _run(tmp_path)
    ms = freeze(spec, {"study": "gc13"}, aggregated=False)
    assert run_data_hash(run) == ms.model_hashes["detailed"]
    assert (
        run_data_hash(run) != ms.data_hash
    )  # the set's input hash also covers its params


def test_driver_judges_a_rated_pack_for_the_run_it_was_built_from(tmp_path):
    run, _ = _run(tmp_path)
    h = run_data_hash(run)
    j = judge_pack_results(run, _rated(tmp_path, campus_data_hash=h), PROFILE)
    assert j["header"]["campus_data_hash"] == h
    assert j["header"]["ratings"]["rated_mw"] == 100.0


def _snapshot(folder):
    return (
        {p.name: p.read_bytes() for p in folder.iterdir()} if folder.exists() else None
    )


def test_driver_refuses_a_pack_built_from_another_run_naming_both_hashes(tmp_path):
    import yaml

    run, spec = _run(tmp_path)
    h = run_data_hash(run)
    judge_pack_results(run, _rated(tmp_path / "ok", campus_data_hash=h), PROFILE)
    before = _snapshot(run / "compliance")
    assert sorted(before) == ["judgment.csv", "judgment.json"]
    # one value changed in the run's campus: the pack is another run's
    spec["campus"]["name"] = f"{spec['campus'].get('name', '')} edited"
    (run / "campus.yaml").write_text(yaml.safe_dump(spec))
    h2 = run_data_hash(run)
    assert h2 != h
    with pytest.raises(ContractError) as exc:
        judge_pack_results(run, _rated(tmp_path / "x", campus_data_hash=h), PROFILE)
    assert h in str(exc.value) and h2 in str(exc.value)
    assert _snapshot(run / "compliance") == before  # nothing written, no partial file
    # a run with no campus description, or one that is not a mapping, cannot be matched
    for i, text in enumerate((None, "", "- a list\n")):
        if text is None:
            (run / "campus.yaml").unlink()
        else:
            (run / "campus.yaml").write_text(text)
        with pytest.raises(ContractError, match="cannot read this run's campus"):
            judge_pack_results(
                run, _rated(tmp_path / f"y{i}", campus_data_hash=h), PROFILE
            )
        assert _snapshot(run / "compliance") == before
    # an unrated pack needs no campus description (master's path)
    judge_pack_results(
        tmp_path / "bare", _full_pack(tmp_path / "plain"), PROFILE, RATINGS
    )
