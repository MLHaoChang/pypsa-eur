# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 24: one source of FMEA rows.

The FMEA worksheet (Guided "See the risk table" and Expert FMEA tab, both
GET /results/fmea_modes) must list the same rows as the Energy Hub panel and
the report, which read the study's ``fmea_top`` payload. While the last EH
study is the newest FMEA source, ``/fmea_modes`` serves that payload through
the report's own reader (``evidence.fmea_top_modes``); a contingency sweep
finished after the study, or no study at all, keeps the live COPT + sweep rows.
"""

from __future__ import annotations

import pypsa
import pytest

import routers.results as R
from models.energy_hub import ReferenceDesignReport, SectionState, empty_section_map
from services.adequacy.eh_report import store_eh_report
from services.pypsa_service import PyPSAService
from services.reports.evidence import fmea_top_modes


def _mode(mode_id, name, cls, comp, crit, **extra):
    return {
        "mode_id": mode_id,
        "name": name,
        "failure_class": cls,
        "component_class": comp,
        "occurrence_per_year": 0.5,
        "occurrence_basis": "FOR",
        "severity_eur": crit * 2,
        "criticality_eur_per_year": crit,
        "delta_eue_mwh": 0.1,
        "in_metric_scope": True,
        "mitigability": "redundancy",
        "engine": "lp",
        "fidelity": "dispatch",
        **extra,
    }


# The shape eh_study._stage_fmea_top writes: class B Link sweep under
# ``rows``, the class-A COPT screening of the same plan under ``class_a``.
PAYLOAD = {
    "rows": [
        _mode(
            "link:critical_feeder:forced_outage",
            "critical_feeder",
            "B",
            "Link",
            473_000.0,
        ),
        _mode(
            "link:genset_1:forced_outage",
            "genset_1",
            "B",
            "Link",
            0.0,
            zero_reason="no_shortfall",
        ),
    ],
    "top_n": 10,
    "class_a": {
        "status": "ok",
        "rows": [
            _mode(
                "gen:genset_new:forced_outage",
                "genset_new",
                "A",
                "Generator",
                900_000.0,
                engine="copt",
                fidelity="analytic_convolution",
            )
        ],
    },
    "voll_eur_per_mwh": 5000.0,
}


def _empty_network():
    """No outage data: the live COPT path alone would answer 204."""
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", carrier="wind", p_nom=20.0)
    return n


def _store_into(store, payload, status="ok"):
    sections = empty_section_map(default="skipped")
    sections["fmea_top"] = SectionState(status=status, payload=payload)
    store_eh_report(
        store,
        ReferenceDesignReport(
            archetype="strong_grid",
            pack_hash="p",
            assumptions_hash="a",
            sections=sections,
        ),
    )


def _store(monkeypatch, payload, status="ok"):
    store: dict = {}
    _store_into(store, payload, status)
    for key, value in store.items():
        monkeypatch.setitem(R._state, key, value)


@pytest.fixture
def study(monkeypatch):
    PyPSAService.set_network(_empty_network())
    _store(monkeypatch, PAYLOAD)
    monkeypatch.setitem(R._state, "eh_study", {"status": "done", "finished_at": 1000.0})
    monkeypatch.setitem(R._state, "fmea_sweep", None)


def _key(rows):
    return [
        (r["mode_id"], r["failure_class"], r["criticality_eur_per_year"]) for r in rows
    ]


def test_the_worksheet_serves_the_studys_rows_as_the_report_reads_them(study):
    out = R.get_fmea_modes()
    assert getattr(out, "status_code", 200) == 200
    assert out["source"] == "eh_study"
    assert _key(out["per_mode"]) == _key(fmea_top_modes(PAYLOAD))
    # The study's prices stand, as they do in the panel and the report.
    assert out["voll_eur_per_mwh"] == 5000.0
    assert out["per_mode"][0]["name"] == "critical_feeder"


def test_the_study_order_is_the_readers_not_a_criticality_sort(study):
    """GK C2: class B then class A, each ranked within its class, even when a
    class-A row costs more than every class-B row."""
    out = R.get_fmea_modes()
    assert [r["mode_id"] for r in out["per_mode"]] == [
        "link:critical_feeder:forced_outage",
        "link:genset_1:forced_outage",
        "gen:genset_new:forced_outage",
    ]
    assert [r["rank"] for r in out["per_mode"]] == [1, 2, 1]


def test_the_rows_carry_the_studys_finish_time_and_edit_state(study, monkeypatch):
    """GK C1 / UX 3: the worksheet dates the rows and marks them out of date."""
    import services.study_state as study_state

    monkeypatch.setattr(study_state, "edited_since", lambda record, **_: True)
    out = R.get_fmea_modes()
    assert out["study_finished_at"] == 1000.0
    assert out["edited_since_study"] is True
    monkeypatch.setattr(study_state, "edited_since", lambda record, **_: None)
    assert R.get_fmea_modes()["edited_since_study"] is None


def test_the_live_copt_is_not_computed_for_study_rows(study, monkeypatch):
    """GK C5: a COPT refusal cannot 422 a study-sourced read."""
    import services.adequacy.copt_endpoint as ce

    def boom(*_a, **_k):
        raise ValueError("COPT refused")

    monkeypatch.setattr(ce, "build_copt_payload", boom)
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"


def test_unpriced_study_rows_stay_unpriced_and_say_so(study, monkeypatch):
    """GK C4 (backend side): rows the study could not price stay None, and
    ``source`` tells the worksheet a new VoLL needs the study run again."""
    payload = {
        **PAYLOAD,
        "rows": [
            {**r, "severity_eur": None, "criticality_eur_per_year": None}
            for r in PAYLOAD["rows"]
        ],
    }
    payload.pop("voll_eur_per_mwh")
    _store(monkeypatch, payload)
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"
    assert out["voll_eur_per_mwh"] is None
    assert out["per_mode"][0]["criticality_eur_per_year"] is None


def test_a_legacy_top_payload_keeps_its_legacy_mark(study, monkeypatch):
    """GK C3: a report stored before the per-class split is read as written,
    its rows marked per-horizon legacy by the report's reader."""
    legacy = {
        "top": [_mode("gen:g:forced_outage", "g", "A", "Generator", 10.0, rank=1)]
    }
    _store(monkeypatch, legacy)
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"
    assert _key(out["per_mode"]) == _key(fmea_top_modes(legacy))
    assert out["per_mode"][0]["criticality_basis"] == "per_horizon_legacy"


def test_the_studys_rows_keep_their_prices_without_a_live_voll(study, monkeypatch):
    cfg = PyPSAService.get_solver_state()["solver_config"]
    monkeypatch.setattr(cfg, "voll", 0.0)
    out = R.get_fmea_modes()
    assert [r["criticality_eur_per_year"] for r in out["per_mode"]] == [
        473_000.0,
        0.0,
        900_000.0,
    ]


def test_a_sweep_finished_after_the_study_takes_over(study, monkeypatch):
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    monkeypatch.setitem(
        R._state,
        "fmea_sweep",
        {
            "status": "done",
            "finished_at": 2000.0,
            "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}],
        },
    )
    out = R.get_fmea_modes()
    assert out["source"] == "live"
    assert [r["mode_id"] for r in out["per_mode"]] == ["link:x:forced_outage"]


def test_a_sweep_finished_at_the_same_second_leaves_the_study(study, monkeypatch):
    """GK C3: the sweep must finish strictly after the study to take over."""
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    monkeypatch.setitem(
        R._state,
        "fmea_sweep",
        {
            "status": "done",
            "finished_at": 1000.0,
            "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}],
        },
    )
    assert R.get_fmea_modes()["source"] == "eh_study"


def test_an_aborted_sweep_after_the_study_takes_over(study, monkeypatch):
    """GK C3: aborted counts as finished, as on the live path."""
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    monkeypatch.setitem(
        R._state,
        "fmea_sweep",
        {
            "status": "aborted",
            "finished_at": 2000.0,
            "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}],
        },
    )
    out = R.get_fmea_modes()
    assert out["source"] == "live"
    assert [r["mode_id"] for r in out["per_mode"]] == ["link:x:forced_outage"]


def test_a_running_sweep_leaves_the_study(study, monkeypatch):
    monkeypatch.setitem(
        R._state, "fmea_sweep", {"status": "running", "finished_at": None, "rows": []}
    )
    assert R.get_fmea_modes()["source"] == "eh_study"


def test_a_sweep_older_than_the_study_does_not(study, monkeypatch):
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    monkeypatch.setitem(
        R._state,
        "fmea_sweep",
        {
            "status": "done",
            "finished_at": 500.0,
            "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}],
        },
    )
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"
    assert "link:x:forced_outage" not in [r["mode_id"] for r in out["per_mode"]]


def test_a_running_study_does_not_serve_the_previous_reports_rows(study, monkeypatch):
    monkeypatch.setitem(
        R._state, "eh_study", {"status": "running", "finished_at": None}
    )
    out = R.get_fmea_modes()
    assert getattr(out, "status_code", 200) == 204


def test_a_study_without_fmea_rows_leaves_the_live_path(study, monkeypatch):
    _store(
        monkeypatch,
        {"rows": [], "class_a": {"status": "not_established", "rows": []}},
        status="not_established",
    )
    out = R.get_fmea_modes()
    assert getattr(out, "status_code", 200) == 204


def test_a_running_sweep_keeps_its_status_over_the_study_rows(study, monkeypatch):
    """The worksheet's polling, abort control and edit lock read sweep_status:
    a sweep running over the study's rows must still say it runs."""
    monkeypatch.setitem(
        R._state,
        "fmea_sweep",
        {"status": "running", "finished_at": None, "rows": [], "error": None},
    )
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"
    assert out["sweep_status"] == "running"


def test_the_results_workbook_lists_the_study_rows_and_says_so(study, monkeypatch):
    """The Excel FMEA sheet reads the same rows and notes where they come from."""
    from types import SimpleNamespace

    import services.study_state as study_state
    from services.results_workbook.views_adequacy import _fmea

    monkeypatch.setattr(study_state, "edited_since", lambda record, **_: True)
    state = dict(R._state)
    run = SimpleNamespace(
        name="Base",
        network=PyPSAService.get_network(),
        cfg=state.get("solver_config"),
        context=None,
        solver_state=state,
    )
    view = _fmea(run)
    frame = view.tables[0].frame
    assert len(frame) == len(fmea_top_modes(PAYLOAD))
    note = next(n for n in view.notes if "Energy Hub study" in n)
    assert "out of date" in note
    assert "a contingency sweep run after the study replaces them" in note


# ── edges the mutant run found untested (GK C6) ──────────────────────────


def _done_sweep(finished_at, status="done"):
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    return {
        "status": status,
        "finished_at": finished_at,
        "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}],
    }


def test_a_failed_sweep_after_the_study_does_not_take_over(study, monkeypatch):
    """Only a done or aborted sweep has rows to serve; a failed one does not."""
    monkeypatch.setitem(R._state, "fmea_sweep", _done_sweep(2000.0, status="failed"))
    assert R.get_fmea_modes()["source"] == "eh_study"


def test_a_sweep_without_a_finish_time_does_not_take_over(study, monkeypatch):
    monkeypatch.setitem(R._state, "fmea_sweep", _done_sweep(None))
    assert R.get_fmea_modes()["source"] == "eh_study"


def test_a_finished_sweep_wins_over_a_study_without_a_finish_time(study, monkeypatch):
    """A study record that never recorded when it finished is not newer."""
    monkeypatch.setitem(R._state, "eh_study", {"status": "done"})
    monkeypatch.setitem(R._state, "fmea_sweep", _done_sweep(500.0))
    assert R.get_fmea_modes()["source"] == "live"


def test_study_args_without_a_study_record(monkeypatch):
    """A stored report with no study record (a reopened project) still serves."""
    from services.adequacy.copt_endpoint import study_fmea_args

    state: dict = {}
    _store_into(state, PAYLOAD)
    args = study_fmea_args(state)
    assert args["study_fmea_top"] == PAYLOAD
    assert args["study_finished_at"] is None
    assert study_fmea_args({}) == {}


def test_study_args_need_an_fmea_top_payload():
    from services.adequacy.copt_endpoint import study_fmea_args

    no_section: dict = {}
    store_eh_report(
        no_section,
        ReferenceDesignReport(
            archetype="strong_grid", pack_hash="p", assumptions_hash="a"
        ),
    )
    assert study_fmea_args(no_section) == {}
    no_payload: dict = {}
    _store_into(no_payload, None, status="skipped")
    assert study_fmea_args(no_payload) == {}


def test_the_sweeps_restore_and_error_travel_with_the_study_rows(study, monkeypatch):
    """A sweep older than the study whose closing re-solve failed still says so."""
    monkeypatch.setitem(
        R._state,
        "fmea_sweep",
        {
            **_done_sweep(500.0, status="aborted"),
            "base_restored": False,
            "base_restore_status": "infeasible",
            "error": "stopped",
        },
    )
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"
    assert (
        out["sweep_status"],
        out["sweep_base_restored"],
        out["sweep_base_restore_status"],
        out["sweep_error"],
    ) == ("aborted", False, "infeasible", "stopped")
