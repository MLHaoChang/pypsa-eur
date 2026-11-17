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
    hub, monkeypatch
):  # noqa: F811
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
