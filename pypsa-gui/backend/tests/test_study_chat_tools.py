"""
U3: the six decision-study chat tools (plan §5 U3 row; U2 plan "U3 outline").

* Their tiers: in Guided every study tool except the two reads asks for a
  confirmation card; in Expert only the run does.
* A novice's whole question, driven through the tools alone — start, one key
  parameter, the options, the tornado, the findings, the explanation, the
  report — on the real routes, forks, campaign and readers, with the fake
  solver of `tests/test_study_runner.py` (as the route tests use it).
* Refusals pass through typed: multi-user mode, a bad stage or format.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import HTTPException

from harness import catalogue
from harness.confirm import AUTO_APPROVE_TIERS, _confirm_tiers
from services import chat_tools
from tests.study_s4_support import enable_studies
from tests.test_study_runner import FakeSolver
from tests.test_study_tornado_routes import NO_PV_TOU

TOOLS = {
    "start_decision_study": "write",
    "set_study_parameter": "write",
    "run_decision_study": "execution_long_running",
    "get_study_findings": "read",
    "explain_verdict": "read",
    "generate_study_report": "write",
}
CAP = 4000


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _gated(tier: str, guided: bool) -> bool:
    return tier in _confirm_tiers(guided) and tier not in AUTO_APPROVE_TIERS


def test_the_six_tools_are_registered_with_their_tiers():
    names = {t["name"] for t in catalogue.TOOLS}
    for name, tier in TOOLS.items():
        assert name in names and name in chat_tools.DISPATCHERS, name
        assert catalogue.safety_tier_for(name) == tier, name
        assert catalogue.TOOL_ROUTES[name], name


def test_guided_confirms_every_study_tool_but_the_reads_and_expert_only_the_run():
    guided = {n for n, t in TOOLS.items() if _gated(t, guided=True)}
    expert = {n for n, t in TOOLS.items() if _gated(t, guided=False)}
    assert guided == set(TOOLS) - {"get_study_findings", "explain_verdict"}
    assert expert == {"run_decision_study"}


def _poll(project: str, sid: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = chat_tools.get_study_findings(project, sid)
        if "running" not in out:
            return out
        assert len(json.dumps(out)) < CAP
        time.sleep(0.1)
    raise AssertionError("the study did not finish")


def test_a_novice_goes_from_question_to_report_by_chat(client, api_project, studies_on,
                                                       fake, monkeypatch):
    saved: list[tuple[str, str, int]] = []
    monkeypatch.setattr(chat_tools, "_save_agent_export",
                        lambda data, filename, mime, **_: saved.append(
                            (filename, mime, len(data))) or {"file_id": "f1"})
    path = api_project("chat-src")

    started = chat_tools.start_decision_study(path, "bess_at_site", "Site battery", NO_PV_TOU,
                                              base_project_name="chat-base")
    sid, base = started["study_id"], started["project"]
    assert base == "chat-base" and sid

    edited = chat_tools.set_study_parameter(base, sid, "discount_rate", 0.06, "per unit",
                                            source="the owner's hurdle rate")
    assert edited["row"]["value"] == pytest.approx(0.06)
    assert edited["row"]["provenance"] == "user" and edited["row"]["status"] == "customised"

    run = chat_tools.run_decision_study(base, sid)
    assert run["stage"] == "options" and run["run"]["status"] in ("queued", "running", "done")
    first = _poll(base, sid)
    assert first["verdict"]["status"] == "not_established"
    assert "stage='tornado'" in first["next_step"]

    chat_tools.run_decision_study(base, sid, stage="tornado")
    found = _poll(base, sid)
    assert found["available"] is True
    assert found["robustness"]["status"] == "ok"
    assert {o["option_id"] for o in found["options"]} >= {"none", "bess_1h"}
    assert len(json.dumps(found)) < CAP

    why = chat_tools.explain_verdict(base, sid)
    assert why["status"] == found["verdict"]["status"]
    assert len(json.dumps(why)) < CAP

    rep = chat_tools.generate_study_report(base, sid, format="docx")
    assert rep["file"] == {"file_id": "f1"} and rep["stale"] is False
    assert saved and saved[0][0].endswith(".docx") and saved[0][2] > 0


def test_bad_stage_and_format_are_typed_refusals(client, api_project, studies_on):
    with pytest.raises(HTTPException) as exc:
        chat_tools.run_decision_study("p", "s", stage="everything")
    assert exc.value.detail["error_kind"] == "study_stage_invalid"
    with pytest.raises(HTTPException) as exc:
        chat_tools.generate_study_report("p", "s", format="pdf")
    assert exc.value.detail["error_kind"] == "study_report_format_invalid"


def test_in_multi_user_mode_the_tools_refuse_as_the_routes_do(client, api_project):
    path = api_project("chat-auth")
    with pytest.raises(HTTPException) as exc:
        chat_tools.start_decision_study(path, "bess_at_site", "x", NO_PV_TOU)
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "decision_studies_unavailable"


def test_the_start_returns_the_key_parameters_with_their_defaults(client, api_project,
                                                                   studies_on):
    path = api_project("chat-kp-src")
    out = chat_tools.start_decision_study(path, "bess_at_site", "Site battery", NO_PV_TOU,
                                          base_project_name="chat-kp")
    keys = {p["key"] for p in out["key_parameters"]}
    assert {"battery_storage_eur_per_kwh", "battery_inverter_eur_per_kw",
            "discount_rate"} <= keys
    assert all(p.get("source") and p.get("unit") for p in out["key_parameters"])
    assert len(json.dumps(out)) < CAP
