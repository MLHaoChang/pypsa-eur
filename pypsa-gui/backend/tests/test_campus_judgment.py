"""The campus run stores and serves the pack read-back judgment (grid-compliance GC-22).

End to end: a real campus run writes PSS/E bundles, each with its EMT pack; the
upload (the trigger) adds results to one pack and judges it; the stored judgment
is served exactly as ``compliance/judgment.json`` holds it.
"""

import csv
import json

import numpy as np
import pytest
import yaml
from fastapi import HTTPException

from gridspine.drivers.readback import COMPLIANCE_DIR, JUDGMENT_JSON
from gridspine.handoff.test_battery import cases_from_json
from gridspine.readback.pack import status_counts
from services import campus_electrical_service as ce
from services import campus_judgment as cj
from services.grid_compliance import sources
from tests import test_campus_electrical_service as _ces
from tests.test_campus_island_hours_run import _island_campus, _run
from tests.test_campus_island_tables import _sidecar

hub, user_and_db = _ces.hub, _ces.user_and_db

T = np.round(np.arange(0.0, 1.5 + 1e-9, 1e-3), 10)
FAULT = (T >= 0.2) & (T < 0.35)


def _with_zero_sequence(project):
    """The PCC's max-case zero sequence, so the bundles' EMT packs carry a test battery."""
    path = ce.campus_dir(project) / ce.CAMPUS_FILE
    spec = yaml.safe_load(path.read_text())
    spec["campus"]["pcc"].update(x0x_max={"value": 3.0, "source": "assumed"},
                                 r0x0_max={"value": 0.1, "source": "assumed"})
    ce.save_campus(project, yaml.safe_dump(spec, sort_keys=False))


@pytest.fixture
def island_run(hub):
    _sidecar(hub)
    _island_campus(hub)
    _with_zero_sequence(hub)
    run = _run(hub)
    bundle = ce.psse_bundles(hub)["bundles"][0]["name"]
    return hub, run, bundle


def _pack(run, bundle):
    return run / ce.cs.PSSE_DIR / bundle / "emt"


def _cases(run, bundle):
    return cases_from_json((_pack(run, bundle) / "battery.json").read_text(encoding="utf-8"))


def _ride_through_csv() -> bytes:
    """A fault case's results that hold on: v dips to 0.3 pu, reactive current is injected, P recovers."""
    ch = {
        "v_poc_pu": ("pu", np.where(FAULT, 0.3, 1.0)),
        "iq_pu": ("pu", np.where(FAULT, 1.2, 0.0)),
        "p_poc_mw": ("MW", np.where(T < 0.2, 10.0, np.where(FAULT, 0.0, 10.0 * np.clip((T - 0.35) / 0.2, 0, 1)))),
    }
    lines = [",".join(["time [s]"] + [f"{n} [{u}]" for n, (u, _) in ch.items()])]
    for k, t in enumerate(T):
        lines.append(",".join([repr(float(t))] + [repr(float(v[k])) for _, v in ch.values()]))
    return ("\n".join(lines) + "\n").encode()


def _fault_case(run, bundle):
    return next(c for c in _cases(run, bundle) if c.kind.startswith("fault"))


def _stored(run):
    return json.loads((run / COMPLIANCE_DIR / JUDGMENT_JSON).read_text(encoding="utf-8"))


def test_no_judgment_before_a_read_back_and_the_source_is_pending(island_run):
    project, _run_dir, _bundle = island_run
    assert cj.get_judgment(project) == {"has_judgment": False}
    assert sources.read(project, "GC-07") == ("pending", None)


def test_an_upload_judges_the_pack_and_the_stored_judgment_is_served_as_written(island_run):
    project, run, bundle = island_run
    case = _fault_case(run, bundle)
    got = cj.add_results(project, bundle, [(f"{case.case_id}.csv", _ride_through_csv())])
    assert (_pack(run, bundle) / "results" / f"{case.case_id}.csv").read_bytes() == _ride_through_csv()
    on_disk = _stored(run)
    assert got["has_judgment"] is True and got["bundle"] == bundle and got["judged_at"]
    assert got["header"] == on_disk["header"] and got["cases"] == on_disk["cases"]
    assert got["status_counts"] == status_counts(on_disk)
    assert sum(got["status_counts"].values()) == len(_cases(run, bundle))
    for key in ("data_hash", "contract_version", "ratings"):
        assert key in got["header"], key
    assert got["header"]["ratings"]["mw_source"], "the rating's source is carried"
    judged = {c["case_id"]: c for c in got["cases"]}
    assert judged[case.case_id]["detail"] != "no result file"
    assert cj.get_judgment(project) == got


def test_a_case_without_results_stays_unknown(island_run):
    project, run, bundle = island_run
    case = _fault_case(run, bundle)
    got = cj.add_results(project, bundle, [(f"{case.case_id}.csv", _ride_through_csv())])
    others = [c for c in got["cases"] if c["case_id"] != case.case_id]
    assert others and all(c["status"] == "unknown" for c in others)
    assert got["status_counts"]["unknown"] >= len(others)


def test_a_re_judge_replaces_the_stored_judgment(island_run):
    project, run, bundle = island_run
    case = _fault_case(run, bundle)
    first = cj.judge(project, bundle)
    assert all(c["status"] == "unknown" for c in first["cases"])
    second = cj.add_results(project, bundle, [(f"{case.case_id}.csv", _ride_through_csv())])
    assert second["cases"] != first["cases"] and second["cases"] == _stored(run)["cases"]
    (_pack(run, bundle) / "results" / f"{case.case_id}.csv").unlink()
    third = cj.judge(project, bundle)
    assert third["cases"] == first["cases"] == _stored(run)["cases"]


def test_a_new_run_drops_the_judgment(island_run):
    project, run, bundle = island_run
    cj.judge(project, bundle)
    _run(project)
    assert cj.get_judgment(project) == {"has_judgment": False}


def test_the_tracker_source_reads_the_stored_judgment(island_run):
    project, run, bundle = island_run
    case = _fault_case(run, bundle)
    got = cj.add_results(project, bundle, [(f"{case.case_id}.csv", _ride_through_csv())])
    state, tables = sources.read(project, "GC-07")
    assert state == "present"
    by_id = {t.table_id: t for t in tables}
    assert [r[:3] for r in by_id["gc07_cases"].rows] == [[c["case_id"], c["kind"], c["status"]] for c in got["cases"]]
    assert len(by_id["gc07_rows"].rows) == sum(len(c["rows"]) for c in got["cases"])
    counts = got["status_counts"]
    assert f"{counts['pass']} pass, {counts['fail']} fail, {counts['unknown']} unknown" in by_id["gc07_cases"].caption


def test_an_unreadable_judgment_is_refused_and_the_source_is_unavailable(island_run):
    project, run, bundle = island_run
    cj.judge(project, bundle)
    (run / COMPLIANCE_DIR / JUDGMENT_JSON).write_text("{not json")
    with pytest.raises(HTTPException) as exc:
        cj.get_judgment(project)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "campus_judgment_unreadable"
    assert sources.read(project, "GC-07") == ("unavailable", None)


@pytest.mark.parametrize("name", ["../x.csv", "sub/x.csv", ".hidden.csv", "notes.txt", "", "a\\b.csv"])
def test_a_file_no_case_claims_is_refused_and_nothing_is_judged(island_run, name):
    project, run, bundle = island_run
    with pytest.raises(HTTPException) as exc:
        cj.add_results(project, bundle, [(name, b"x")])
    assert exc.value.status_code == 422 and exc.value.detail["error_kind"] == "campus_result_name"
    assert not (run / COMPLIANCE_DIR).exists()
    results = _pack(run, bundle) / "results"
    assert not results.exists() or not any(results.iterdir())


def test_twice_the_same_name_too_many_or_no_files_are_refused(island_run, monkeypatch):
    project, run, bundle = island_run
    name = f"{_fault_case(run, bundle).case_id}.csv"
    for files, status, kind in (([(name, b"a"), (name, b"b")], 422, "campus_result_name"),
                                ([], 422, "campus_results_empty")):
        with pytest.raises(HTTPException) as exc:
            cj.add_results(project, bundle, files)
        assert (exc.value.status_code, exc.value.detail["error_kind"]) == (status, kind)
    monkeypatch.setattr(cj, "MAX_RESULT_FILES", 1)
    with pytest.raises(HTTPException) as exc:
        cj.add_results(project, bundle, [(name, b"a"), (f"{name[:-4]}.inf", b"b")])
    assert exc.value.status_code == 413
    assert not (run / COMPLIANCE_DIR).exists()


def test_a_bundle_outside_the_listing_is_404(island_run):
    project, _run_dir, _bundle = island_run
    for name in ("../run", "nope", ""):
        with pytest.raises(HTTPException) as exc:
            cj.judge(project, name)
        assert exc.value.status_code == 404


def test_no_run_is_404(hub):
    with pytest.raises(HTTPException) as exc:
        cj.judge(hub, "case_p0_h0")
    assert exc.value.status_code == 404
    assert cj.get_judgment(hub) == {"has_judgment": False}


def test_a_running_study_refuses_the_judge(island_run):
    project, run, bundle = island_run
    lock = ce._lock(run)
    lock.acquire()
    try:
        with pytest.raises(HTTPException) as exc:
            cj.judge(project, bundle)
    finally:
        lock.release()
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "campus_run_in_progress"
    assert not (run / COMPLIANCE_DIR).exists()


def test_a_profile_changed_since_the_run_is_refused(island_run):
    project, run, bundle = island_run
    used = json.loads((run / ce.PROFILE_USED_FILE).read_text())
    (run / ce.PROFILE_USED_FILE).write_text(json.dumps({**used, "sha256": "0" * 64}))
    with pytest.raises(HTTPException) as exc:
        cj.judge(project, bundle)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "campus_judgment_profile_changed"
    (run / ce.PROFILE_USED_FILE).unlink()
    with pytest.raises(HTTPException) as exc:
        cj.judge(project, bundle)
    assert exc.value.status_code == 409 and exc.value.detail["error_kind"] == "campus_judgment_no_profile"
    assert not (run / COMPLIANCE_DIR).exists()


def test_a_contract_error_is_422_and_writes_nothing(island_run):
    project, run, bundle = island_run
    manifest = _pack(run, bundle) / "manifest.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "pack": "unknown_pack"}))
    with pytest.raises(HTTPException) as exc:
        cj.judge(project, bundle)
    assert exc.value.status_code == 422
    assert not (run / COMPLIANCE_DIR).exists() and not (run / cj.JUDGMENT_SOURCE_FILE).exists()


def test_the_judgment_csv_matches_the_served_rows(island_run):
    """The CSV GC-07 writes next to the JSON is the same judgment, so a report reading either agrees."""
    project, run, bundle = island_run
    case = _fault_case(run, bundle)
    got = cj.add_results(project, bundle, [(f"{case.case_id}.csv", _ride_through_csv())])
    with (run / COMPLIANCE_DIR / "judgment.csv").open(newline="") as f:
        csv_cases = {r["case_id"]: r["case_status"] for r in csv.DictReader(f)}
    assert all(csv_cases[c["case_id"]] == c["status"] for c in got["cases"] if c["rows"])


def test_an_upload_replaces_the_whole_results_set(island_run):
    project, run, bundle = island_run
    ids = [c.case_id for c in _cases(run, bundle)]
    cj.add_results(project, bundle, [(f"{ids[0]}.csv", _ride_through_csv())])
    cj.add_results(project, bundle, [(f"{ids[1]}.csv", b"time [s]\n0.0\n")])
    results = _pack(run, bundle) / "results"
    assert sorted(p.name for p in results.iterdir()) == [f"{ids[1]}.csv"]
    assert not [p for p in _pack(run, bundle).iterdir() if p.name.startswith(".results")]


def test_a_read_back_that_fails_mid_judge_puts_results_and_judgment_back(island_run, monkeypatch):
    from gridspine.drivers import readback

    project, run, bundle = island_run
    case = _fault_case(run, bundle)
    cj.add_results(project, bundle, [(f"{case.case_id}.csv", _ride_through_csv())])
    files = [run / COMPLIANCE_DIR / JUDGMENT_JSON, run / COMPLIANCE_DIR / "judgment.csv", run / cj.JUDGMENT_SOURCE_FILE]
    before = {p: p.read_bytes() for p in files}
    results = _pack(run, bundle) / "results"
    results_before = {p.name: p.read_bytes() for p in results.iterdir()}
    real = readback.judge_pack_results

    def half_then_fail(run_dir, pack_dir, profile, ratings=None):
        real(run_dir, pack_dir, profile, ratings)       # the new judgment is on disk ...
        raise RuntimeError("disk gone")                 # ... and then the read-back dies

    monkeypatch.setattr(readback, "judge_pack_results", half_then_fail)
    with pytest.raises(RuntimeError):
        cj.add_results(project, bundle, [(f"{case.case_id}.csv", b"time [s]\n0.0\n")])
    assert {p: p.read_bytes() for p in files} == before
    assert {p.name: p.read_bytes() for p in results.iterdir()} == results_before
    assert not [p for p in _pack(run, bundle).iterdir() if p.name.startswith(".results")]


def test_a_first_read_back_that_fails_leaves_no_judgment(island_run, monkeypatch):
    from gridspine.drivers import readback

    project, run, bundle = island_run
    monkeypatch.setattr(readback, "judge_pack_results", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        cj.judge(project, bundle)
    assert not (run / COMPLIANCE_DIR / JUDGMENT_JSON).exists() and not (run / cj.JUDGMENT_SOURCE_FILE).exists()


def test_a_judgment_that_no_longer_matches_its_pack_is_stale_and_not_a_source(island_run):
    project, run, bundle = island_run
    cj.judge(project, bundle)
    assert cj.get_judgment(project)["stale"] is False
    assert sources.read(project, "GC-07")[0] == "present"
    path = run / COMPLIANCE_DIR / JUDGMENT_JSON
    j = json.loads(path.read_text())
    for key, value in (("data_hash", "0" * 64), ("contract_version", "0")):
        path.write_text(json.dumps({**j, "header": {**j["header"], key: value}}))
        assert cj.get_judgment(project)["stale"] is True, key
        assert sources.read(project, "GC-07") == ("pending", None), key
    path.write_text(json.dumps(j))
    side = run / cj.JUDGMENT_SOURCE_FILE
    side.write_text(json.dumps({**json.loads(side.read_text()), "bundle": "gone_p0_h0"}))
    assert cj.get_judgment(project)["stale"] is True


def test_judging_another_bundle_replaces_the_judgment_and_names_it(island_run):
    project, run, bundle = island_run
    names = [b["name"] for b in ce.psse_bundles(project)["bundles"]]
    if len(names) < 2:
        pytest.skip("the island campus writes one bundle")
    cj.judge(project, names[0])
    got = cj.judge(project, names[1])
    assert got["bundle"] == names[1] and got["stale"] is False


def test_a_status_the_read_back_does_not_write_counts_and_reads_unknown(island_run):
    project, run, bundle = island_run
    cj.judge(project, bundle)
    path = run / COMPLIANCE_DIR / JUDGMENT_JSON
    j = json.loads(path.read_text())
    j["cases"][0]["status"] = "passed-ish"
    path.write_text(json.dumps(j))
    got = cj.get_judgment(project)
    assert got["cases"][0]["status"] == "passed-ish"                # served as written
    assert got["status_counts"] == {"pass": 0, "fail": 0, "unknown": len(j["cases"])}
    tables = {t.table_id: t for t in sources.read(project, "GC-07")[1]}
    assert tables["gc07_cases"].rows[0][2] == "unknown"


def test_a_per_file_or_total_size_over_the_cap_is_413_and_writes_nothing(island_run, monkeypatch):
    project, run, bundle = island_run
    ids = [c.case_id for c in _cases(run, bundle)]
    monkeypatch.setattr(cj, "MAX_RESULT_FILE_BYTES", 3)
    with pytest.raises(HTTPException) as exc:
        cj.add_results(project, bundle, [(f"{ids[0]}.csv", b"abcd")])
    assert (exc.value.status_code, exc.value.detail["error_kind"]) == (413, "campus_result_too_large")
    monkeypatch.setattr(cj, "MAX_RESULT_FILE_BYTES", 10)
    monkeypatch.setattr(cj, "MAX_RESULT_BYTES", 5)
    with pytest.raises(HTTPException) as exc:
        cj.add_results(project, bundle, [(f"{ids[0]}.csv", b"abc"), (f"{ids[1]}.csv", b"abc")])
    assert (exc.value.status_code, exc.value.detail["error_kind"]) == (413, "campus_results_too_large")
    assert not (run / COMPLIANCE_DIR).exists() and not (_pack(run, bundle) / "results").exists()


def test_a_results_folder_that_is_a_link_is_refused(island_run, tmp_path):
    project, run, bundle = island_run
    (_pack(run, bundle) / "results").symlink_to(tmp_path)
    with pytest.raises(HTTPException) as exc:
        cj.judge(project, bundle)
    assert exc.value.status_code == 409
    assert not (run / COMPLIANCE_DIR).exists()


def test_a_pack_without_a_battery_says_what_the_campus_needs(hub):
    _sidecar(hub)
    _island_campus(hub)
    run = _run(hub)
    bundle = ce.psse_bundles(hub)["bundles"][0]["name"]
    with pytest.raises(HTTPException) as exc:
        cj.add_results(hub, bundle, [("x.csv", b"")])
    assert exc.value.status_code == 409 and "x0x_max" in exc.value.detail["message"]
    assert not (run / COMPLIANCE_DIR).exists()
