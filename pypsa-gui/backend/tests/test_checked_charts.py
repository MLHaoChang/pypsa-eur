"""Checked copilot charts (checked-results S10): drawn from the answer's evidence only."""

# ruff: noqa: F811 -- imported shared fixtures.
import json

import pytest

from harness import assurance_scope
from services import assurance_records, checked_charts
from tests.assurance_acceptance.support import approval
from tests.test_answer_assurance import checked  # noqa: F401
from tests.test_assurance_metrics import NumericalProvider, solved  # noqa: F401
from tests.test_branch_loading_assurance import LINE, grid, solve  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

GENERATOR = {"dispatch_energy": 17, "marginal_expenditure": 170, "peak_dispatch": 7}


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYPSAGUI_APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("PYPSA_GUI_ANSWER_ASSURANCE_PROFILES", "generator-dispatch/1,branch-loading/1")


def ask(checked, ctx, profile, entity, values, chart, **extra):
    scope = {"version": 1, "profile": profile, "project_id": ctx.project_uuid, "entity_id": entity,
             "retain": False, "chart": chart, **extra}
    frames = checked.run(NumericalProvider(values), message="Check and chart.", checked_scope=scope)
    return approval(frames)["receipt"]


def sidecar(chart):
    from services.assistant_tools import _project

    _, root, _ = _project()
    return json.loads((root / "assistant_artifacts" / (chart["file_id"] + ".json")).read_text())["manifest"]


def evidence(receipt):
    return assurance_records.load(assurance_records.actor_id(), receipt["evidence_ids"][0], "evidence")


def test_a_dispatch_chart_draws_the_evidence_and_names_what_it_did_not_draw(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p", "marginal_cost"])
    chart = receipt["chart"]
    assert chart["status"] == "drawn" and chart["evidence_id"] == receipt["evidence_ids"][0]
    assert chart["series"] == [
        {"series": "p", "disposition": "drawn", "unit": "MW"},
        {"series": "marginal_cost", "disposition": "not_drawn", "reason": "not_in_evidence",
         "title": "Not part of this answer's checked evidence"}]
    assert chart["caption"][0] == "Chart drawn from checked evidence · 1 series · 1 Jan 2026, 00:00–01:00"
    assert chart["caption"][1] == "Not drawn: marginal cost — Not part of this answer's checked evidence"
    manifest = sidecar(chart)
    record = evidence(receipt)
    # Every value as stored: the drawn dataset is the evidence array, bit for bit.
    assert manifest["dataset"]["values"] == [record["raw"]["generators"]["G1"]["inputs"]["p"]]
    assert manifest["dataset"]["axis_labels"]["p"] == "MW per snapshot (not energy-weighted)"
    assert manifest["source_sha256"] == record["source_sha256"]
    response = checked.client.get(chart["download_url"])
    assert response.status_code == 200 and response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_loading_is_the_branch_profiles_own_series(grid, checked):
    from harness import branch_calculations

    ctx = solve(grid)
    receipt = ask(checked, ctx, "branch-loading/1", "L12", LINE, ["loading", "p0"],
                  component_class="Line")
    record = evidence(receipt)
    branch = next(iter(record["raw"]["branches"].values()))
    flows, u = branch_calculations.utilisation_series(branch, 0, 2)
    manifest = sidecar(receipt["chart"])
    assert manifest["dataset"]["columns"] == ["loading", "p0"]
    assert manifest["dataset"]["values"][0] == [100 * v for v in u] == [50, 100, 100]
    assert manifest["dataset"]["values"][1] == branch["p0"]


def test_an_outdated_solve_draws_nothing(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    solved.network.loads.at["L1", "p_set"] = 6  # the solve no longer matches its inputs
    chart = checked_charts.render(solved, evidence(receipt), ["p"])
    assert chart["status"] == "not_drawn" and chart["reason"] == "outdated_evidence"


def test_a_tampered_chart_file_is_not_served(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    from services.assistant_tools import _project

    _, root, _ = _project()
    path = root / "assistant_artifacts" / (receipt["chart"]["file_id"] + ".json")
    record = json.loads(path.read_text())
    record["manifest"]["dataset"]["values"][0][0] += 1
    path.write_text(json.dumps(record))
    assert checked.client.get(receipt["chart"]["download_url"]).status_code == 409


def test_a_chart_is_not_drawn_for_an_answer_that_failed_its_check(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", {**GENERATOR, "dispatch_energy": 999}, ["p"])
    assert receipt["chart"]["status"] == "not_drawn" and receipt["chart"]["reason"] == "draft_not_checked"


def test_the_chart_suffix_is_part_of_the_whole_message_grammar():
    scope = assurance_scope.from_message("Show the loading for line L12; chart loading, p0", "p", {})
    assert scope["chart"] == ["loading", "p0"] and scope["profile"] == "branch-loading/1"
    assert assurance_scope.from_message("Show the loading for line L12; chart loading; and more", "p", {}) is None


def test_a_plain_download_without_the_chat_user_is_served(solved, checked):
    from services import chat_tools

    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    chat_tools.set_acting_user(None)  # the browser's GET carries no chat-tool identity
    response = checked.client.get(receipt["chart"]["download_url"])
    assert response.status_code == 200, response.text


def test_a_follow_up_that_reuses_the_scope_draws_no_chart(solved, checked):
    scope = {"version": 1, "profile": "generator-dispatch/1", "project_id": solved.project_uuid,
             "entity_id": "G1", "retain": False, "chart": ["p"]}
    first = checked.run(NumericalProvider(GENERATOR), message="Check and chart.", checked_scope=scope,
                        session_id="assurance-chart-follow-up")
    assert approval(first)["receipt"]["chart"]["status"] == "drawn"
    again = checked.run(NumericalProvider(GENERATOR), message="Show the same results again",
                        session_id="assurance-chart-follow-up")
    assert "chart" not in approval(again)["receipt"]


def test_a_retained_run_draws_no_chart(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"], **{"retain": True})
    assert receipt["chart"]["reason"] == "current_run_only"


def test_loading_needs_only_the_windows_snapshots(grid, checked):
    ctx = solve(grid)
    ctx.network.lines_t.p0.iloc[2, 0] = float("nan")  # outside the requested window
    first, second = (v.isoformat() for v in ctx.network.snapshots[:2])
    receipt = ask(checked, ctx, "branch-loading/1", "L12",
                  {"peak_branch_utilisation": 100, "hours_above_rating": 0, "peak_branch_flow": 8,
                   "branch_rating": 8}, ["loading"], component_class="Line", start=first, end=second)
    assert receipt["chart"]["status"] == "drawn"
    assert sidecar(receipt["chart"])["dataset"]["values"] == [[50, 100]]


def test_after_its_evidence_expires_a_chart_says_it_was_not_re_verified(solved, checked):
    receipt = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    chart = receipt["chart"]
    drawn_at = sidecar(chart)["drawn_at"]
    assert chart["caption"][-1].startswith(f"Checked against the solve of {drawn_at} · evidence ")
    fresh = checked.client.get(chart["download_url"])
    assert fresh.headers["X-Checked-Chart"] == "evidence re-verified now"
    path = assurance_records._path(assurance_records.actor_id(), receipt["evidence_ids"][0])
    envelope = json.loads(path.read_text())
    envelope["record"]["expires_at"] = 0
    envelope["sha256"] = assurance_records.digest(envelope["record"])
    path.write_text(json.dumps(envelope))
    old = checked.client.get(chart["download_url"])
    assert old.status_code == 200
    assert old.headers["X-Checked-Chart"] == (
        f"checked against the solve of {drawn_at}; evidence expired, not re-verified now")
    # A checked turn never re-presents it: a new request draws a fresh chart from new evidence.
    again = ask(checked, solved, "generator-dispatch/1", "G1", GENERATOR, ["p"])
    assert again["chart"]["evidence_id"] != chart["evidence_id"]
    assert checked.client.get(again["chart"]["download_url"]).headers["X-Checked-Chart"] == "evidence re-verified now"
