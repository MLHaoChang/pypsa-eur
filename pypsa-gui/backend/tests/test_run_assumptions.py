# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Template-e2e 08 (DECISION-GAPS-1): every run input the study falls back on
is an "assumed" row — before the run (readiness), in the run record and in
the report. One function, ``run_assumptions``, makes the rows."""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pypsa
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402
from tests._year_base import build_for_solve  # noqa: E402

from services.adequacy.run_assumptions import run_assumptions  # noqa: E402


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(range(3))
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "h2", carrier="H2")
    n.add("Generator", "gt", bus="ac", carrier="gas", p_nom=10)
    n.add("Generator", "pv", bus="ac", carrier="solar", p_nom=10)
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom=5)
    return n


def _keys(rows):
    return {r["key"] for r in rows}


def _row(rows, key):
    (row,) = [r for r in rows if r["key"] == key]
    return row


ROW_KEYS = {"key", "label", "value", "shown", "source", "reason", "section",
            "card", "component", "entity", "field"}


def test_unset_goal_fields_are_assumed_engine_defaults():
    rows = run_assumptions(None, {}, None)
    draws = _row(rows, "mc_draws")
    assert set(draws) == ROW_KEYS
    assert (draws["value"], draws["source"], draws["field"]) == (500, "default", "mc_draws")
    assert draws["section"] == "scenarios" and draws["card"] == "reliability"
    assert "500" in draws["shown"]
    site = _row(rows, "site_archetype")
    assert (site["value"], site["source"]) == ("strong_grid", "default")
    # Plain words, never the raw id (checklist §6.9).
    assert "strong_grid" not in site["shown"] and "Strong grid" in site["shown"]


def test_a_template_recommendation_is_assumed_and_says_so():
    site = _row(run_assumptions(None, {}, {"recommended_archetype": "off_grid"}),
                "site_archetype")
    assert (site["value"], site["source"]) == ("off_grid", "template")
    assert "template" in site["reason"]


def test_stored_goal_fields_are_not_assumed():
    rows = run_assumptions(None, {"mc_draws": 2000, "site_archetype": "weak_flexible"}, None)
    assert not _keys(rows) & {"mc_draws", "site_archetype"}


def test_carrier_default_outage_rates_are_one_row_per_carrier():
    rows = run_assumptions(_net(), {}, None)
    gas = _row(rows, "outage_rate:Generator:gas")
    assert gas["source"] == "carrier_default"
    assert (gas["component"], gas["entity"], gas["field"]) == ("Generator", "gt", "outage_rate_value")
    assert gas["section"] == "assets"
    assert "5 %" in gas["shown"] and "50 h" in gas["shown"]
    assert _row(rows, "outage_rate:StorageUnit:battery")["entity"] == "bess"


def test_an_asset_rate_is_not_assumed_but_a_missing_repair_time_is():
    n = _net()
    n.generators.loc["gt", "outage_rate_value"] = 0.02
    rows = run_assumptions(n, {}, None)
    assert "outage_rate:Generator:gas" not in _keys(rows)
    mttr = _row(rows, "mttr:Generator:gas")
    assert (mttr["field"], mttr["value"], mttr["source"]) == ("mttr_hours", 50.0, "carrier_default")
    n.generators.loc["gt", "mttr_hours"] = 12.0
    assert "mttr:Generator:gas" not in _keys(run_assumptions(n, {}, None))


def test_a_link_without_an_efficiency_is_assumed_lossless():
    n = _net()
    n.add("Link", "ely", bus0="ac", bus1="h2", p_nom=5, efficiency=0.7)
    n.add("Link", "blank", bus0="ac", bus1="h2", p_nom=5)
    n.links.loc["blank", "efficiency"] = np.nan
    rows = run_assumptions(n, {}, None)
    eff = _row(rows, "efficiency:blank")
    assert (eff["value"], eff["component"], eff["entity"], eff["field"]) == (
        1.0, "Link", "blank", "efficiency")
    assert "efficiency:ely" not in _keys(rows)


def test_rows_are_json_serialisable():
    json.dumps(run_assumptions(_net(), {}, None))


STUDY_IDS = sorted(t for t in T.BUILDERS
                   if not T.TEMPLATE_META.get(t, {}).get("generation_only"))


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_every_template_names_what_its_run_assumes(tid):
    """E2E-TEMPLATES-1: each template's study, with the goal fields unset and
    its shipped carrier-default storage, lists those assumptions."""
    n = build_for_solve(T, tid)
    template = json.loads((BACKEND / "project_templates" / tid / "eh_template.json").read_text())
    rows = run_assumptions(n, {}, template)
    assert "mc_draws" in _keys(rows)
    assert "site_archetype" in _keys(rows)
    assert any(r["source"] == "carrier_default" for r in rows), tid


# ── Before the run: readiness raises each row where its value is set ────────

def _run_default_items(body):
    return [i for s in body["sections"] for i in s["items"] if i["source"] == "run_defaults"]


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_readiness_shows_each_assumed_run_input_before_the_run(tid, client, tmp_projects_dir):
    from services import flow_sections as F

    r = client.post(f"/api/projects/from_template/{tid}", params={"name": "assumed"})
    assert r.status_code == 200, r.text
    name = r.json().get("name", "assumed")
    items = _run_default_items(client.get(f"/api/projects/{name}/flow").json())
    codes = {i["code"] for i in items}
    assert "assumed_run_mc_draws" in codes, tid
    assert any(c.startswith("assumed_run_outage_rate:") for c in codes), tid
    for i in items:
        # The Assumptions card's rule (AssumptionsCard.tsx) picks them up.
        assert i["severity"] == "warning" and i["code"].startswith("assumed_")
        assert "(assumed" in i["message"]
        assert i["target"]["section"] in F.INPUT_SECTIONS
        assert i["card"] == "reliability"


def test_a_stored_run_input_retires_its_row(client, tmp_projects_dir):
    r = client.post("/api/projects/from_template/eh_microgrid", params={"name": "stored"})
    name = r.json().get("name", "stored")
    cfg = client.get("/api/simulation/solver_config").json()
    assert client.put("/api/simulation/solver_config",
                      json={**cfg, "mc_draws": 1000}).status_code == 200
    codes = {i["code"] for i in _run_default_items(client.get(f"/api/projects/{name}/flow").json())}
    assert "assumed_run_mc_draws" not in codes
    assert any(c.startswith("assumed_run_outage_rate:") for c in codes)


# ── VOLL: the API / assistant path refuses as the Run button does (06) ──────

def _voll(client, value):
    cfg = client.get("/api/simulation/solver_config").json()
    assert client.put("/api/simulation/solver_config",
                      json={**cfg, "voll": value}).status_code == 200


def test_the_study_start_refuses_without_a_voll(client, tmp_projects_dir):
    r = client.post("/api/projects/from_template/eh_microgrid", params={"name": "novoll"})
    assert r.status_code == 200, r.text
    _voll(client, 0.0)
    r = client.post("/api/results/eh_study", json={"archetype": "strong_grid"})
    assert r.status_code == 422, r.text
    assert "VOLL" in r.json()["detail"]


def test_readiness_blocks_run_on_a_missing_voll_once(client, tmp_projects_dir):
    r = client.post("/api/projects/from_template/eh_microgrid", params={"name": "novoll"})
    name = r.json().get("name", "novoll")
    _voll(client, 0.0)
    body = client.get(f"/api/projects/{name}/flow").json()
    assert body["run_ready"] is False
    voll = [i for s in body["sections"] for i in s["items"]
            if i["target"]["field"] == "voll" or "VOLL" in i["message"]]
    assert len(voll) == 1, [i["message"] for i in voll]
    assert voll[0]["blocking"] and voll[0]["target"]["section"] == "scenarios"
    assert voll[0]["target"]["field"] == "voll"


# ── The run record carries what the run assumed ──────────────────────────────

def test_a_request_value_that_differs_retires_the_goal_row():
    from services.adequacy.run_assumptions import study_assumptions

    rows = run_assumptions(None, {}, {"recommended_archetype": "off_grid"})
    chosen = study_assumptions(rows, {"site_archetype": "override", "mc_draws": "override"},
                               {"site_archetype": "weak_flexible", "mc_draws": 500})
    # The site type the request chose is the user's; draws equal to the
    # default the request copied are still the default.
    assert {r["key"] for r in chosen} == {"mc_draws"}
    kept = study_assumptions(rows, {"site_archetype": "override", "mc_draws": "default"},
                             {"site_archetype": "off_grid", "mc_draws": None})
    assert {r["key"] for r in kept} == {"mc_draws", "site_archetype"}


def test_the_study_record_carries_its_assumptions(client, tmp_projects_dir):
    r = client.post("/api/projects/from_template/eh_microgrid", params={"name": "record"})
    assert r.status_code == 200, r.text
    try:
        r = client.post("/api/results/eh_study", json={"stages": ["apply_pack", "ens_solve"]})
        assert r.status_code == 200, r.text
        rec = client.get("/api/results/eh_study").json()
        keys = {a["key"] for a in rec["assumptions"]}
        assert "mc_draws" in keys
        assert any(a["source"] == "carrier_default" for a in rec["assumptions"])
    finally:
        client.post("/api/results/eh_study/abort")
        _poll_done(client)


def _poll_done(client, timeout=120.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get("/api/results/eh_study")
        if r.status_code != 200 or r.json().get("status") != "running":
            return
        time.sleep(0.2)


# ── The report prints them beside the template's own assumptions ────────────

def test_the_report_lists_what_the_run_assumed():
    from services.reports import chapters as C
    from services.reports.evidence import collect_evidence

    rows = run_assumptions(_net(), {}, None)
    meta = {"ledger": [{"item": "pue", "value": 1.3, "source": "assumed"}]}
    ev = collect_evidence(study_report=None, eh_report=None, worksheet=None,
                          template_ledger=meta, run_assumptions=rows)
    payload = ev.sections[C.TEMPLATE_LEDGER].payload
    table = C.template_ledger_table(payload, "/x")
    labels = [r[0] for r in table.rows]
    assert any("Simulation runs" in label for label in labels)
    assert any("battery" in label.lower() for label in labels)
    run = [r for r in table.rows if "Simulation runs" in r[0]][0]
    assert run[1] == "500 runs" and "assumed by the run" in run[3].lower()
    # Without a run, the payload is as before (the evidence hash unchanged).
    plain = collect_evidence(study_report=None, eh_report=None, worksheet=None,
                             template_ledger=meta)
    assert "run_rows" not in plain.sections[C.TEMPLATE_LEDGER].payload
