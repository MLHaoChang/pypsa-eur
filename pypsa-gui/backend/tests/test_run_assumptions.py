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


ROW_KEYS = {
    "key",
    "label",
    "value",
    "shown",
    "source",
    "reason",
    "section",
    "card",
    "component",
    "entity",
    "field",
}


def test_unset_goal_fields_are_assumed_engine_defaults():
    rows = run_assumptions(None, {}, None)
    draws = _row(rows, "mc_draws")
    assert set(draws) == ROW_KEYS
    assert (draws["value"], draws["source"], draws["field"]) == (
        500,
        "default",
        "mc_draws",
    )
    assert draws["section"] == "scenarios" and draws["card"] == "reliability"
    assert "500" in draws["shown"]
    site = _row(rows, "site_archetype")
    assert (site["value"], site["source"]) == ("strong_grid", "default")
    # Plain words, never the raw id (checklist §6.9).
    assert "strong_grid" not in site["shown"] and "Strong grid" in site["shown"]


def test_a_template_recommendation_is_assumed_and_says_so():
    site = _row(
        run_assumptions(None, {}, {"recommended_archetype": "off_grid"}),
        "site_archetype",
    )
    assert (site["value"], site["source"]) == ("off_grid", "template")
    assert "template" in site["reason"]


def test_stored_goal_fields_are_not_assumed():
    rows = run_assumptions(
        None, {"mc_draws": 2000, "site_archetype": "weak_flexible"}, None
    )
    assert not _keys(rows) & {"mc_draws", "site_archetype"}


def test_carrier_default_outage_rates_are_one_row_per_carrier():
    rows = run_assumptions(_net(), {}, None)
    gas = _row(rows, "outage_rate:Generator:gas")
    assert gas["source"] == "carrier_default"
    assert (gas["component"], gas["entity"], gas["field"]) == (
        "Generator",
        "gt",
        "outage_rate_value",
    )
    assert gas["section"] == "assets"
    assert "5 %" in gas["shown"] and "50 h" in gas["shown"]
    assert _row(rows, "outage_rate:StorageUnit:battery")["entity"] == "bess"


def test_an_asset_rate_is_not_assumed_but_a_missing_repair_time_is():
    n = _net()
    n.generators.loc["gt", "outage_rate_value"] = 0.02
    rows = run_assumptions(n, {}, None)
    assert "outage_rate:Generator:gas" not in _keys(rows)
    mttr = _row(rows, "mttr:Generator:gas")
    assert (mttr["field"], mttr["value"], mttr["source"]) == (
        "mttr_hours",
        50.0,
        "carrier_default",
    )
    n.generators.loc["gt", "mttr_hours"] = 12.0
    assert "mttr:Generator:gas" not in _keys(run_assumptions(n, {}, None))


def test_an_asset_rate_without_a_basis_is_assumed_forced_outage():
    n = _net()
    n.generators.loc["gt", "outage_rate_value"] = 0.02
    basis = _row(run_assumptions(n, {}, None), "outage_basis:Generator")
    assert (basis["value"], basis["entity"], basis["field"], basis["source"]) == (
        "FOR",
        "gt",
        "outage_rate_basis",
        "default",
    )
    n.generators.loc["gt", "outage_rate_basis"] = "EFOR"
    assert "outage_basis:Generator" not in _keys(run_assumptions(n, {}, None))


def test_a_carrier_rate_zeroed_by_the_availability_flag_is_not_assumed():
    from services.adequacy.occurrence import FLAG_COL

    n = _net()
    n.generators.loc["gt", "p_max_pu"] = 0.9
    n.generators.loc["gt", FLAG_COL] = True
    assert "outage_rate:Generator:gas" not in _keys(run_assumptions(n, {}, None))
    # The flag on a unit with availability 1 is ignored, so the rate applies.
    n.generators.loc["gt", "p_max_pu"] = 1.0
    assert "outage_rate:Generator:gas" in _keys(run_assumptions(n, {}, None))


def test_a_link_without_an_efficiency_is_assumed_lossless():
    n = _net()
    n.add("Link", "ely", bus0="ac", bus1="h2", p_nom=5, efficiency=0.7)
    n.add("Link", "blank", bus0="ac", bus1="h2", p_nom=5)
    n.links.loc["blank", "efficiency"] = np.nan
    rows = run_assumptions(n, {}, None)
    eff = _row(rows, "efficiency:blank")
    assert (eff["value"], eff["component"], eff["entity"], eff["field"]) == (
        1.0,
        "Link",
        "blank",
        "efficiency",
    )
    assert "efficiency:ely" not in _keys(rows)


def test_rows_are_json_serialisable():
    json.dumps(run_assumptions(_net(), {}, None))


STUDY_IDS = sorted(
    t for t in T.BUILDERS if not T.TEMPLATE_META.get(t, {}).get("generation_only")
)


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_every_template_names_what_its_run_assumes(tid):
    """E2E-TEMPLATES-1: each template's study, with the goal fields unset and
    its shipped carrier-default storage, lists those assumptions."""
    n = build_for_solve(T, tid)
    template = json.loads(
        (BACKEND / "project_templates" / tid / "eh_template.json").read_text()
    )
    rows = run_assumptions(n, {}, template)
    assert "mc_draws" in _keys(rows)
    assert "site_archetype" in _keys(rows)
    assert any(r["source"] == "carrier_default" for r in rows), tid


# ── Before the run: readiness raises each row where its value is set ────────


def _run_default_items(body):
    return [
        i for s in body["sections"] for i in s["items"] if i["source"] == "run_defaults"
    ]


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_readiness_shows_each_assumed_run_input_before_the_run(
    tid, client, tmp_projects_dir
):
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
    r = client.post(
        "/api/projects/from_template/eh_microgrid", params={"name": "stored"}
    )
    name = r.json().get("name", "stored")
    cfg = client.get("/api/simulation/solver_config").json()
    assert (
        client.put(
            "/api/simulation/solver_config", json={**cfg, "mc_draws": 1000}
        ).status_code
        == 200
    )
    codes = {
        i["code"]
        for i in _run_default_items(client.get(f"/api/projects/{name}/flow").json())
    }
    assert "assumed_run_mc_draws" not in codes
    assert any(c.startswith("assumed_run_outage_rate:") for c in codes)


# ── VOLL: the API / assistant path refuses as the Run button does (06) ──────


def _voll(client, value):
    cfg = client.get("/api/simulation/solver_config").json()
    assert (
        client.put(
            "/api/simulation/solver_config", json={**cfg, "voll": value}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_the_study_start_refuses_without_a_voll(client, tmp_projects_dir, tid):
    r = client.post(
        f"/api/projects/from_template/{tid}", params={"name": "novoll"}
    )
    assert r.status_code == 200, r.text
    _voll(client, 0.0)
    r = client.post("/api/results/eh_study", json={"archetype": "strong_grid"})
    assert r.status_code == 422, r.text
    # Typed for the assistant, as the frontier's VOLL refusal is.
    assert r.json()["detail"]["error_kind"] == "unsupported_scope"
    assert "VOLL" in r.json()["detail"]["message"]


def test_readiness_blocks_run_on_a_missing_voll_once(client, tmp_projects_dir):
    r = client.post(
        "/api/projects/from_template/eh_microgrid", params={"name": "novoll"}
    )
    name = r.json().get("name", "novoll")
    _voll(client, 0.0)
    body = client.get(f"/api/projects/{name}/flow").json()
    assert body["run_ready"] is False
    voll = [
        i
        for s in body["sections"]
        for i in s["items"]
        if i["target"]["field"] == "voll" or "VOLL" in i["message"]
    ]
    assert len(voll) == 1, [i["message"] for i in voll]
    assert voll[0]["blocking"] and voll[0]["target"]["section"] == "scenarios"
    assert voll[0]["target"]["field"] == "voll"


# ── The run record carries what the run assumed ──────────────────────────────


def test_a_request_value_that_differs_retires_the_goal_row():
    from services.adequacy.run_assumptions import study_assumptions

    rows = run_assumptions(None, {}, {"recommended_archetype": "off_grid"})
    chosen = study_assumptions(
        rows,
        {"site_archetype": "override", "mc_draws": "override"},
        {"site_archetype": "weak_flexible", "mc_draws": 500},
    )
    # The site type the request chose is the user's; draws equal to the
    # default the request copied are still the default.
    assert {r["key"] for r in chosen} == {"mc_draws"}
    kept = study_assumptions(
        rows,
        {"site_archetype": "override", "mc_draws": "default"},
        {"site_archetype": "off_grid", "mc_draws": None},
    )
    assert {r["key"] for r in kept} == {"mc_draws", "site_archetype"}


GOAL_KEYS = {"mc_draws", "site_archetype"}


@pytest.mark.parametrize("tid", STUDY_IDS)
def test_the_study_record_carries_its_assumptions(client, tmp_projects_dir, tid):
    """E2E-TEMPLATES-1: each template's study, run with its inputs missing,
    records every assumed run input its network has (outage, basis, efficiency)."""
    expected = _keys(run_assumptions(build_for_solve(T, tid), {}, None)) - GOAL_KEYS
    r = client.post(
        f"/api/projects/from_template/{tid}", params={"name": "record"}
    )
    assert r.status_code == 200, r.text
    try:
        r = client.post(
            "/api/results/eh_study", json={"stages": ["apply_pack", "ens_solve"]}
        )
        assert r.status_code == 200, r.text
        rec = _poll_until(
            client, lambda rec: "assumptions" in rec or rec.get("status") != "running"
        )
        # Read from the study's private copy, as it is made (run_eh_study's
        # on_private_copy), so present before the first stage ends.
        keys = {a["key"] for a in rec["assumptions"]}
        assert "mc_draws" in keys
        assert expected, tid
        assert keys - GOAL_KEYS == expected, tid
    finally:
        client.post("/api/results/eh_study/abort")
        _poll_done(client)


def _poll_until(client, done, timeout=120.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = client.get("/api/results/eh_study").json()
        if done(rec):
            return rec
        time.sleep(0.1)
    raise AssertionError("the study record never carried its assumptions")


def test_a_recorder_failure_leaves_the_record_saying_not_recorded():
    from types import SimpleNamespace

    from services.adequacy.eh_study_runner import _assumptions_recorder

    record: dict = {}
    body = SimpleNamespace(archetype="strong_grid", mc=None)

    class Torn:  # a network that cannot be read
        def __getattr__(self, name):
            raise RuntimeError(name)

    _assumptions_recorder(record, {}, body, {})(Torn())
    assert "assumptions" not in record
    _assumptions_recorder(record, {}, body, {})(_net())
    assert record["assumptions"]


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
    ev = collect_evidence(
        study_report=None,
        eh_report=None,
        worksheet=None,
        template_ledger=meta,
        run_assumptions={"recorded": True, "rows": rows},
    )
    payload = ev.sections[C.TEMPLATE_LEDGER].payload
    table = C.template_ledger_table(payload, "/x")
    labels = [r[0] for r in table.rows]
    assert any("Simulation runs" in label for label in labels)
    assert any("battery" in label.lower() for label in labels)
    run = [r for r in table.rows if "Simulation runs" in r[0]][0]
    assert run[1] == "500 runs" and "assumed by the run" in run[3].lower()
    # Without a run, the payload is as before (the evidence hash unchanged).
    plain = collect_evidence(
        study_report=None, eh_report=None, worksheet=None, template_ledger=meta
    )
    assert "run" not in plain.sections[C.TEMPLATE_LEDGER].payload


@pytest.mark.parametrize(
    ("run", "said"),
    [
        ({"recorded": True, "rows": []}, "none"),
        ({"recorded": False, "rows": []}, "not recorded for this study"),
    ],
)
def test_the_report_tells_none_from_not_recorded(run, said):
    from services.reports import chapters as C

    payload = C.template_ledger_payload({"ledger": []}, run)
    table = C.template_ledger_table(payload, "/x")
    assert table.rows == [
        ["Inputs the study run assumed", said, "", "assumed by the run", ""]
    ]


# ── GK B plan conditions (Q2, A1, A3, A5) ────────────────────────────────────


@pytest.mark.parametrize(
    "record, stored, want",
    [
        ({}, None, None),
        ({}, {"sections": {}}, {"recorded": False, "rows": []}),
        ({"report": {}}, None, {"recorded": False, "rows": []}),
        ({"report": {}, "assumptions": []}, None, {"recorded": True, "rows": []}),
    ],
    ids=["no-study", "stored-report-record-gone", "old-record", "none-assumed"],
)
def test_the_report_reads_the_runs_rows_or_says_not_recorded(
    monkeypatch, tmp_path, record, stored, want
):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from routers import reports
    from routers import results as results_router
    from services import flow_service

    def no_case(**_):
        raise HTTPException(404, "no case")

    monkeypatch.setattr(results_router, "get_investment_case_report", no_case)
    monkeypatch.setattr(results_router, "_state", {"eh_study": record})
    monkeypatch.setattr(flow_service, "read_template", lambda d: {"id": "t"})
    monkeypatch.setattr(reports, "_current_eh_report", lambda: stored)
    out = reports._current_chapter_sources(SimpleNamespace(directory=tmp_path))
    assert out.get("run_assumptions") == want


def test_a_conversion_link_at_exactly_one_is_assumed_lossless():
    n = _net()
    n.add("Link", "ely_blank", bus0="ac", bus1="h2", p_nom=5)  # PyPSA's 1.0
    n.add("Link", "tie", bus0="ac", bus1="ac", p_nom=5)  # same carrier
    n.add("Link", "__voll_x", bus0="ac", bus1="h2", p_nom=5)  # a slack
    n.add("Link", "n1", bus0="ac", bus1="h2", p_nom=5)
    n.links["eh_role"] = ""
    n.links.loc["n1", "eh_role"] = "eh_n1_conversion"  # study-internal
    keys = _keys(run_assumptions(n, {}, None))
    assert "efficiency:ely_blank" in keys
    assert not keys & {"efficiency:tie", "efficiency:__voll_x", "efficiency:n1"}


def test_a_gap_in_an_efficiency_series_is_assumed():
    n = _net()
    n.add("Link", "ely", bus0="ac", bus1="h2", p_nom=5, efficiency=[0.7, np.nan, 0.7])
    assert "efficiency:ely" in _keys(run_assumptions(n, {}, None))
    n.links_t.efficiency.loc[1, "ely"] = 0.7
    assert "efficiency:ely" not in _keys(run_assumptions(n, {}, None))


def test_a_carrier_row_names_every_unit():
    n = _net()
    for k in range(7):
        n.add("StorageUnit", f"b{k}", bus="ac", carrier="battery", p_nom=1)
    row = _row(run_assumptions(n, {}, None), "outage_rate:StorageUnit:battery")
    for name in ["bess", *(f"b{k}" for k in range(7))]:
        assert name in row["label"] and name in row["entity"].split(", ")
    assert "more" not in row["label"]


def test_run_assumptions_is_a_pure_read(tmp_path):
    import copy
    from types import SimpleNamespace

    from services.workflow_tools import _network_input_fingerprint

    n = _net()
    n.add("Link", "blank", bus0="ac", bus1="h2", p_nom=5)
    from dataclasses import asdict

    from services.solver_service import SolverConfig

    cfg = SolverConfig()
    before_cfg = copy.deepcopy(asdict(cfg))
    ctx = SimpleNamespace(
        network=n,
        solver_state={"solver_config": cfg},
        user_ts={},
        storage_dir=str(tmp_path),
    )
    before = _network_input_fingerprint(ctx)
    run_assumptions(n, cfg, {"recommended_archetype": "off_grid"})
    assert _network_input_fingerprint(ctx) == before
    assert asdict(cfg) == before_cfg


def test_an_acknowledged_assumption_returns_when_its_value_changes(
    client, tmp_projects_dir, project_storage_dir
):
    r = client.post("/api/projects/from_template/eh_h2_hub", params={"name": "ack"})
    name = r.json().get("name", "ack")

    def site_item():
        body = client.get(f"/api/projects/{name}/flow").json()
        return next(
            i
            for s in body["sections"]
            for i in s["items"]
            if i["code"] == "assumed_run_site_archetype"
        )

    assert site_item()["acknowledged"] is False
    r = client.post(
        f"/api/projects/{name}/flow/ack",
        json={"code": "assumed_run_site_archetype", "entity": None},
    )
    assert r.status_code == 200, r.text
    assert site_item()["acknowledged"] is True
    path = project_storage_dir(name) / "eh_template.json"
    meta = json.loads(path.read_text())
    meta["recommended_archetype"] = (
        "weak_flexible"
        if meta.get("recommended_archetype") != "weak_flexible"
        else "strong_grid"
    )
    path.write_text(json.dumps(meta))
    assert site_item()["acknowledged"] is False


@pytest.mark.parametrize(
    "voll",
    ["absent", None, float("nan"), float("inf"), float("-inf"), 0.0, -1.0, "abc"],
)
def test_every_unusable_voll_is_refused(voll):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from services.adequacy.eh_study_runner import VOLL_REFUSAL, check_eh_study_start

    cfg = SimpleNamespace() if voll == "absent" else SimpleNamespace(voll=voll)
    with pytest.raises(HTTPException) as exc:
        check_eh_study_start(None, solver_state={"solver_config": cfg})
    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "error_kind": "unsupported_scope",
        "message": VOLL_REFUSAL,
        "field": "voll",
    }


def test_a_positive_voll_passes_the_voll_check():
    from types import SimpleNamespace

    from services.adequacy.run_assumptions import voll_is_set

    assert voll_is_set(SimpleNamespace(voll=0.01))
    assert voll_is_set(SimpleNamespace(voll=5000))
    assert voll_is_set({"voll": 5000}) and not voll_is_set({})


def test_a_carrier_row_uses_the_carriers_own_name():
    n = _net()
    n.add("Carrier", "battery", nice_name="Lithium-ion battery")
    row = _row(run_assumptions(n, {}, None), "outage_rate:StorageUnit:battery")
    assert "lithium-ion battery" in row["label"]
    n.carriers.loc["battery", "nice_name"] = "  "
    row = _row(run_assumptions(n, {}, None), "outage_rate:StorageUnit:battery")
    assert row["label"].startswith("Outage rate of battery storage units")


def test_an_empty_network_assumes_only_the_goal():
    rows = run_assumptions(pypsa.Network(), {}, None)
    assert _keys(rows) == {"mc_draws", "site_archetype"}
