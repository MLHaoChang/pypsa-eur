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
    return {"mode_id": mode_id, "name": name, "failure_class": cls,
            "component_class": comp, "occurrence_per_year": 0.5,
            "occurrence_basis": "FOR", "severity_eur": crit * 2,
            "criticality_eur_per_year": crit, "delta_eue_mwh": 0.1,
            "in_metric_scope": True, "mitigability": "redundancy",
            "engine": "lp", "fidelity": "dispatch", **extra}


# The shape eh_study._stage_fmea_top writes: class B Link sweep under
# ``rows``, the class-A COPT screening of the same plan under ``class_a``.
PAYLOAD = {
    "rows": [_mode("link:critical_feeder:forced_outage", "critical_feeder",
                   "B", "Link", 473_000.0),
             _mode("link:genset_1:forced_outage", "genset_1", "B", "Link",
                   0.0, zero_reason="no_shortfall")],
    "top_n": 10,
    "class_a": {"status": "ok", "rows": [
        _mode("gen:genset_new:forced_outage", "genset_new", "A", "Generator",
              12_529.0, engine="copt", fidelity="analytic_convolution")]},
    "voll_eur_per_mwh": 5000.0,
}


def _empty_network():
    """No outage data: the live COPT path alone would answer 204."""
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=10.0)
    n.add("Generator", "g", bus="b", carrier="wind", p_nom=20.0)
    return n


@pytest.fixture
def study(monkeypatch):
    PyPSAService.set_network(_empty_network())
    sections = empty_section_map(default="skipped")
    sections["fmea_top"] = SectionState(status="ok", payload=PAYLOAD)
    report = ReferenceDesignReport(archetype="strong_grid", pack_hash="p",
                                   assumptions_hash="a", sections=sections)
    store: dict = {}
    store_eh_report(store, report)
    for key, value in store.items():
        monkeypatch.setitem(R._state, key, value)
    monkeypatch.setitem(R._state, "eh_study",
                        {"status": "done", "finished_at": 1000.0})
    monkeypatch.setitem(R._state, "fmea_sweep", None)


def _key(rows):
    return [(r["mode_id"], r["failure_class"], r["criticality_eur_per_year"])
            for r in rows]


def test_the_worksheet_serves_the_studys_rows_as_the_report_reads_them(study):
    out = R.get_fmea_modes()
    assert getattr(out, "status_code", 200) == 200
    assert out["source"] == "eh_study"
    assert _key(out["per_mode"]) == _key(fmea_top_modes(PAYLOAD))
    # The study's prices stand, as they do in the panel and the report.
    assert out["voll_eur_per_mwh"] == 5000.0
    assert out["per_mode"][0]["name"] == "critical_feeder"


def test_the_studys_rows_keep_their_prices_without_a_live_voll(study, monkeypatch):
    cfg = PyPSAService.get_solver_state()["solver_config"]
    monkeypatch.setattr(cfg, "voll", 0.0)
    out = R.get_fmea_modes()
    assert [r["criticality_eur_per_year"] for r in out["per_mode"]] \
        == [473_000.0, 0.0, 12_529.0]


def test_a_sweep_finished_after_the_study_takes_over(study, monkeypatch):
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    monkeypatch.setitem(R._state, "fmea_sweep", {
        "status": "done", "finished_at": 2000.0,
        "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}]})
    out = R.get_fmea_modes()
    assert out["source"] == "live"
    assert [r["mode_id"] for r in out["per_mode"]] == ["link:x:forced_outage"]


def test_a_sweep_older_than_the_study_does_not(study, monkeypatch):
    sweep_mode = _mode("link:x:forced_outage", "x", "B", "Link", 7.0)
    monkeypatch.setitem(R._state, "fmea_sweep", {
        "status": "done", "finished_at": 500.0,
        "rows": [{"failure_mode": sweep_mode, "delta_eue_mwh": 0.1}]})
    out = R.get_fmea_modes()
    assert out["source"] == "eh_study"
    assert "link:x:forced_outage" not in [r["mode_id"] for r in out["per_mode"]]


def test_a_running_study_does_not_serve_the_previous_reports_rows(study, monkeypatch):
    monkeypatch.setitem(R._state, "eh_study", {"status": "running", "finished_at": None})
    out = R.get_fmea_modes()
    assert getattr(out, "status_code", 200) == 204


def test_a_study_without_fmea_rows_leaves_the_live_path(study, monkeypatch):
    sections = empty_section_map(default="skipped")
    sections["fmea_top"] = SectionState(status="not_established", payload={
        "rows": [], "class_a": {"status": "not_established", "rows": []}})
    store: dict = {}
    store_eh_report(store, ReferenceDesignReport(
        archetype="strong_grid", pack_hash="p", assumptions_hash="a",
        sections=sections))
    for key, value in store.items():
        monkeypatch.setitem(R._state, key, value)
    out = R.get_fmea_modes()
    assert getattr(out, "status_code", 200) == 204
