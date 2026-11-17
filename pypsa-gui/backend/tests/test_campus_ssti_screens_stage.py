# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The campus run's SSTI and sub-synchronous screens stage (grid-compliance GC-08).

The run screens SSTI with the unit interaction factor and scans the sub-synchronous impedance from the PCC
after the dip and restart screens. A failure is shown with the results, its verdicts read ``error``, never
``clear``, and it never fails the run.
"""

from services import campus_electrical_service as ce
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures
from tests.test_campus_scan_and_grid_code_since_run import SETTINGS, _publish, _run

THRESHOLD = {"value": 0.2, "clause": "TRGC 19 test clause", "source": "code"}
CLEARANCE = {"value": 1.5, "clause": "TRGC 20 test clause", "source": "code"}


def test_the_run_screens_after_the_dips_and_restarts(hub):  # noqa: F811
    _publish(hub)
    stages = []
    ce.draft(hub)
    ce.run(
        hub,
        {**SETTINGS, "profile": "pq_test"},
        progress=lambda name, done, total: stages.append(name),
    )
    assert stages.index("ssti_screens") == stages.index("dips_restart") + 1
    res = ce.get_state(hub)["results"]["ssti_screens"]
    assert res["label"] == "screening; binding answer is the EMT study"
    assert (
        res["uif"]["threshold"] == 0.1
        and res["uif"]["source"] is None
        and res["uif"]["clause"] is None
    )
    assert res["uif"]["verdict"] in ("flag", "clear", "not evaluated")
    assert isinstance(res["uif"]["rows"], list) and isinstance(
        res["uif"]["notes"], list
    )
    # the campus enters no series capacitor: the scan never reads clear
    assert res["subsync"]["verdict"] == "not evaluated"
    assert res["subsync"]["clearance_hz"] == 1.0
    kinds = {(r["kind"], r["verdict"]) for r in res["subsync"]["resonance"]}
    assert kinds == {("series resonance", "not evaluated"), ("SSCI", "not evaluated")}
    run = ce.campus_dir(hub) / "run"
    assert not (run / ce.SSTI_SCREENS_ERROR).exists()


def test_the_profile_keys_and_their_clauses_reach_the_results(hub):  # noqa: F811
    _publish(
        hub, ssti_uif_threshold=THRESHOLD, subsync_torsional_clearance_hz=CLEARANCE
    )
    _run(hub)
    res = ce.get_state(hub)["results"]["ssti_screens"]
    assert (res["uif"]["threshold"], res["uif"]["source"], res["uif"]["clause"]) == (
        0.2,
        "code",
        "TRGC 19 test clause",
    )
    assert (
        res["subsync"]["clearance_hz"],
        res["subsync"]["source"],
        res["subsync"]["clause"],
    ) == (1.5, "code", "TRGC 20 test clause")
    assert all(r["clearance_hz"] == 1.5 for r in res["subsync"]["resonance"])


def test_a_screen_error_reads_error_never_fails_the_run_and_clears_on_success(
    hub,  # noqa: F811
    monkeypatch,
):
    _publish(hub)
    _run(hub)
    run = ce.campus_dir(hub) / "run"
    assert (run / ce.cs.SUBSYNC_RESONANCE_CSV).is_file()

    real = ce.cs.ssti_screens_campus

    def broken(run_dir, *a, **kw):
        real(run_dir, *a, **kw)  # it wrote its CSVs, then fails: none of them may stay
        raise ce.ContractError("SSTI screens: test failure")

    monkeypatch.setattr(ce.cs, "ssti_screens_campus", broken)
    failed = ce.run(hub, {**SETTINGS, "profile": "pq_test"})["results"]["ssti_screens"]
    assert failed["error"] == "SSTI screens: test failure"
    assert (
        failed["uif"]["verdict"] == "error" and failed["subsync"]["verdict"] == "error"
    )
    for name in (
        ce.cs.SSTI_UIF_CSV,
        ce.cs.SUBSYNC_SCAN_CSV,
        ce.cs.SUBSYNC_RESONANCE_CSV,
        ce.SSTI_SCREENS_META,
    ):
        assert not (run / name).exists(), name
    # the rest of the run still published
    assert ce.get_state(hub)["results"]["harmonic_scan"] is not None

    monkeypatch.setattr(ce.cs, "ssti_screens_campus", real)
    ok = ce.run(hub, {**SETTINGS, "profile": "pq_test"})["results"]["ssti_screens"]
    assert "error" not in ok and ok["subsync"]["verdict"] == "not evaluated"
    assert not (run / ce.SSTI_SCREENS_ERROR).exists()


def test_an_unexpected_failure_reads_error_too(hub, monkeypatch):  # noqa: F811
    _publish(hub)

    def boom(*a, **kw):
        raise RuntimeError

    monkeypatch.setattr(ce.cs, "ssti_screens_campus", boom)
    res = _run(hub)["results"]["ssti_screens"]
    assert res["error"] == "RuntimeError"
    assert (res["uif"]["verdict"], res["subsync"]["verdict"]) == ("error", "error")


def test_a_run_from_before_the_stage_has_no_screens(hub):  # noqa: F811
    _publish(hub)
    _run(hub)
    run = ce.campus_dir(hub) / "run"
    (run / ce.SSTI_SCREENS_META).unlink()
    assert ce.get_state(hub)["results"]["ssti_screens"] is None


def test_the_results_carry_every_meta_field_and_the_rows(tmp_path):
    """Read back from a run's files: the meta's fields, the UIF rows and the resonance rows; a missing
    CSV reads as no rows."""
    import json

    meta = {
        "uif": {
            "verdict": "flag",
            "worst_uif": 0.14,
            "threshold": 0.1,
            "source": None,
            "clause": None,
            "notes": ["n1"],
        },
        "subsync": {
            "verdict": "not evaluated",
            "clearance_hz": 1.0,
            "source": None,
            "clause": None,
            "notes": ["n2"],
        },
        "label": "screening; binding answer is the EMT study",
    }
    (tmp_path / ce.SSTI_SCREENS_META).write_text(json.dumps(meta))
    (tmp_path / ce.cs.SSTI_UIF_CSV).write_text(
        "bus,machine,uif,verdict\nMV,GAS,0.14,flag\n"
    )
    (tmp_path / ce.cs.SUBSYNC_RESONANCE_CSV).write_text(
        "case,kind,verdict\nmax,SSCI,not evaluated\n"
    )
    res = ce._ssti_screens_results(tmp_path)
    assert res["uif"]["rows"] == [
        {"bus": "MV", "machine": "GAS", "uif": 0.14, "verdict": "flag"}
    ]
    assert res["subsync"]["resonance"] == [
        {"case": "max", "kind": "SSCI", "verdict": "not evaluated"}
    ]
    assert res["uif"]["worst_uif"] == 0.14 and res["subsync"]["notes"] == ["n2"]
    (tmp_path / ce.cs.SSTI_UIF_CSV).unlink()
    (tmp_path / ce.cs.SUBSYNC_RESONANCE_CSV).unlink()
    res = ce._ssti_screens_results(tmp_path)
    assert res["uif"]["rows"] == [] and res["subsync"]["resonance"] == []


def test_the_meta_keeps_worst_uif_and_both_notes(hub):  # noqa: F811
    from gridspine.static import subsync_scan as ss

    _publish(hub)
    _run(hub)
    res = ce.get_state(hub)["results"]["ssti_screens"]
    assert "worst_uif" in res["uif"] and isinstance(res["uif"]["notes"], list)
    assert ss.NO_MACHINES in res["subsync"]["notes"]


def test_only_an_unexpected_failure_is_logged_with_its_trace(
    tmp_path, monkeypatch, caplog
):
    def fail(exc):
        def f(*a, **kw):
            raise exc

        return f

    monkeypatch.setattr(
        ce.cs, "ssti_screens_campus", fail(ce.ContractError("expected"))
    )
    with caplog.at_level("ERROR"):
        ce._ssti_screens(tmp_path, "generic_assumed", ())
    assert not any("SSTI screens failed" in r.message for r in caplog.records)
    monkeypatch.setattr(ce.cs, "ssti_screens_campus", fail(RuntimeError("boom")))
    with caplog.at_level("ERROR"):
        ce._ssti_screens(tmp_path, "generic_assumed", ())
    assert any("SSTI screens failed" in r.message for r in caplog.records)


def test_a_rerun_of_the_step_clears_stale_files_first(hub):  # noqa: F811
    """The step starts by removing every file it owns: a stale error, CSV or meta from before never
    survives a successful screen."""
    import json

    _publish(hub, ssti_uif_threshold=THRESHOLD)
    _run(hub)
    run = ce.campus_dir(hub) / "run"
    (run / ce.SSTI_SCREENS_ERROR).write_text(json.dumps({"error": "stale"}))
    (run / ce.SSTI_SCREENS_META).write_text(
        json.dumps({"uif": {"verdict": "stale"}, "subsync": {}})
    )
    for name in (
        ce.cs.SSTI_UIF_CSV,
        ce.cs.SUBSYNC_SCAN_CSV,
        ce.cs.SUBSYNC_RESONANCE_CSV,
    ):
        (run / name).write_text("stale\n1\n")
    ce._ssti_screens(run, "pq_test", (ce.grid_codes_dir(hub),))
    assert not (run / ce.SSTI_SCREENS_ERROR).exists()
    res = ce._ssti_screens_results(run)
    assert "error" not in res
    assert (
        res["uif"]["threshold"] == 0.2 and res["subsync"]["verdict"] == "not evaluated"
    )
    assert all("stale" not in r for r in res["subsync"]["resonance"])
