"""Study harness issue 04: the input loop L1, headless agent turns and the
change ledger (`harness/run_loops.py`, design §6.1, §6.3-6.5; owner decisions
SH-1, SH-ENVELOPE-1, AI-558, TOOLSEL-1).

Real project, real runner, the envelope the user granted and the ordinary
dispatch path. The model is a scripted provider (no network); nobody clicks:
what runs ran because the envelope admitted it."""
# ruff: noqa: F811 -- imported pytest fixtures
import copy
import json
import re

import pytest
from harness import run_loops, runner as harness_runner
from harness.protocol import LLMEvent
from services import run_envelope, run_profiles
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import BLOCK, envelope_of, granted, profile  # noqa: F401
from tests.test_harness_run_skeleton import site, start  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401

GAPS = [{"tool": "apply_generated_profile", "target": {"component": "Load", "name": "L*", "attr": "p_set"}},
        {"tool": "set_weather_choice", "target": {"component": "Settings", "name": "Weather", "attr": "source"}},
        {"tool": "set_weather_choice", "target": {"component": "Settings", "name": "Weather", "attr": "year"}}]
WITH_GAPS = {**BLOCK, "gaps": GAPS}
USAGE = {"input_tokens": 1200, "output_tokens": 300, "cache_read_tokens": 4000, "cache_create_tokens": 0}


class Script:
    """A provider that answers from a script; a turn may be a function of the
    request (to use an earlier tool result)."""

    name = "script"

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def stream(self, request):
        self.requests.append(copy.deepcopy(request))
        if not self.turns:
            raise AssertionError("Script: exhausted")
        turn = self.turns.pop(0)
        turn = turn(request) if callable(turn) else turn
        yield LLMEvent(type="message_done", blocks=copy.deepcopy(turn["blocks"]), usage=dict(USAGE))


def call(name, args, n=0):
    return {"blocks": [{"type": "tool_use", "id": f"tu-{name}-{n}", "name": name, "input": args}]}


def say(text):
    return {"blocks": [{"type": "text", "text": text}]}


def last_job(request):
    """The job id in the last tool result the model was sent."""
    text = json.dumps(request.messages, default=str)
    found = re.findall(r'job_id[^A-Za-z0-9-]+([A-Za-z0-9_-]{6,})', text)
    if not found:
        raise AssertionError("no job id in the tool results")
    return found[-1]


@pytest.fixture
def rated(monkeypatch):
    """Every model the run session may report gets a rate, so tokens meter."""
    rate = {"input_tokens": 1.0, "cache_read_tokens": 0.1, "cache_create_tokens": 1.25, "output_tokens": 5.0}
    monkeypatch.setattr(harness_runner, "_rate", lambda model: rate)
    return rate


def no_series():
    """L1 loses its demand series: the eh_site input gate's data gap."""
    network = PyPSAService.get_active_context().network
    network.loads_t.p_set = network.loads_t.p_set.drop(columns=["L1"])


def record_of(path):
    return json.loads(path.read_text())


def stopped(record):
    return record["run"]["status"] in ("completed", "needs_user", "paused", "failed")


def go(runner, path, task):
    return settle(runner, path, task["task_id"], stopped)


def refused(path):
    return [(a["tool"], a["reason"]) for a in envelope_of(path)["audit"] if a["event"] == "refused"]


def generate(project_id):
    return ("generate_load_profile", {"project_id": project_id, "archetype": "industrial", "loads": ["L1"],
                                      "params": {"shifts": 3, "annual_mwh": 43800.0}, "year": 2026})


def apply_from(project_id):
    def turn(request):
        job = last_job(request)
        return call("apply_generated_profile", {"project_id": project_id, "job_id": job, "component": "L1",
                                                "time_basis": "UTC"})
    return turn


# ── Acceptance 1: a missing load series is attached within one round ────────


def test_l1_attaches_a_missing_load_series_in_one_round_with_one_ledger_row(granted, profile, site, rated):
    project_id, _ = site
    profile(WITH_GAPS)
    no_series()
    task, path, _ = granted()
    script = Script(call(*generate(project_id)), apply_from(project_id),
                    say("Attached the generated demand series to L1."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "completed", (run.get("loop"), run.get("ledger"), run.get("reason"), run["stages"]["input_gate"])
    assert run["stages"]["input_gate"]["status"] == "passed"
    assert run["loop"]["rounds"] == 1 and run["loop"]["ended"] == "passed" and run["loop"]["refusals"] == 0
    assert "L1" in PyPSAService.get_active_context().network.loads_t.p_set.columns
    [row] = run["ledger"]
    envelope = envelope_of(path)
    assert row["target"] == {"component": "Load", "name": "L1", "attr": "p_set"}
    assert row["tool"] == "apply_generated_profile" and row["stage"] == "input_gate" and row["loop"] == "L1"
    assert row["round"] == 1 and row["approval"] == "envelope:" + envelope["id"] and row["reverted"] is False
    assert row["receipt"].endswith(":tu-apply_generated_profile-0")
    used = [(a["tool"]) for a in envelope["audit"] if a["event"] == "used"]
    assert used[:2] == ["generate_load_profile", "apply_generated_profile"]
    # The chain followed the run's own change: the solve after it was admitted, not drift.
    assert "solve_queue_enqueue" in used and not refused(path)


# ── Acceptance 2: a defect the agent cannot fix ends the loop ───────────────


def test_a_defect_outside_the_tunables_ends_no_progress_and_needs_the_user(granted, profile, site, rated):
    profile(BLOCK)  # no gaps listed: the series cannot be filled inside the envelope
    no_series()
    task, path, _ = granted()
    script = Script(call("validate_network", {}), say("Nothing I may change fixes this; L1 needs a series."),
                    say("Still nothing within the envelope."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    run = record["run"]
    assert run["status"] == "needs_user" and run["stages"]["input_gate"]["status"] == "failed"
    assert run["loop"]["ended"] == "no_progress" and run["loop"]["rounds"] == 1
    assert run.get("ledger", []) == []
    assert [r["rule"] for r in run["stages"]["input_gate"]["gate"]["rules"] if r["verdict"] != "pass"] == \
        ["in.loads.series"]


def test_changes_that_never_clear_the_rule_end_after_three_rounds_with_the_ledger_intact(granted, profile, site,
                                                                                          rated):
    profile(BLOCK)
    no_series()
    task, path, _ = granted()
    turns = []
    for n, cost in enumerate((11.0, 12.0, 13.0)):
        turns += [call("update_component", {"component_class": "Generator", "name": "G1",
                                            "attrs": {"marginal_cost": cost}}, n), say("Tried a cost.")]
    record = go(harness_runner.Runner("a", agent_provider=Script(*turns)), path, task)
    run = record["run"]
    assert run["status"] == "needs_user" and run["stages"]["input_gate"]["status"] == "failed"
    assert run["loop"]["ended"] == "exhausted" and run["loop"]["rounds"] == 3
    assert [(r["round"], r["target"]["attr"], r["new"]) for r in run["ledger"]] == \
        [(1, "marginal_cost", 11.0), (2, "marginal_cost", 12.0), (3, "marginal_cost", 13.0)]
    assert all(r["receipt"] and r["approval"].startswith("envelope:") for r in run["ledger"])
    assert PyPSAService.get_active_context().network.generators.at["G1", "marginal_cost"] == 13.0


# ── Acceptance 3: an edit outside the tunables or the envelope is a refusal ─


def test_an_edit_outside_the_tunables_or_the_envelope_is_carded_not_run_and_counted(granted, profile, site, rated):
    profile(BLOCK)
    no_series()
    task, path, _ = granted()
    script = Script(call("update_component", {"component_class": "Generator", "name": "G1",
                                              "attrs": {"p_nom": 1000.0}}),  # outside the bounds
                    call("update_component", {"component_class": "Load", "name": "L1",
                                              "attrs": {"p_set": 9.0}}, 1),  # demand: never tunable
                    say("Both were refused."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    network = PyPSAService.get_active_context().network
    assert network.generators.at["G1", "p_nom"] == 20.0 and network.loads.at["L1", "p_set"] == 5.0
    run = record["run"]
    seen = [(f["frame"], f.get("error_kind")) for f in frames_of(task["task_id"], record["owner_id"])
            if f.get("loop") == "L1"]
    assert run["loop"]["refusals"] == 2 and run["loop"]["history"][0]["refusals"] == 2, (seen, refused(path))
    assert run.get("ledger", []) == []
    assert refused(path) == [("update_component", "out_of_bounds"), ("update_component", "outside_entry")]
    frames = [f["frame"] for f in frames_of(task["task_id"], record["owner_id"]) if f.get("loop") == "L1"]
    assert frames.count("tool_pending_confirmation") == 2 and "tool_running" not in frames


# ── Acceptance 4: the headless turn offers the allowlist only ───────────────


def test_the_headless_turn_offers_only_the_stages_allowlisted_tools(granted, profile, site, rated):
    profile(BLOCK)
    no_series()
    task, path, _ = granted()
    script = Script(say("Nothing to change."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    offered = [t["name"] for t in script.requests[0].tools]
    allow = run_profiles.resolve("eh_site@1")["stages"]["input_gate"]["agent"]["tools"]
    assert sorted(offered) == sorted(allow) == sorted(run_profiles.L1_TOOLS)
    assert record["run"]["loop"]["history"][0]["offered"] == sorted(allow)
    # The stage instruction is per-turn content, never the system prompt.
    instruction = run_loops.instruction("eh_site@1", "input_gate")
    assert instruction.splitlines()[0] in json.dumps(script.requests[0].messages, ensure_ascii=False)
    assert instruction.splitlines()[0] not in json.dumps(script.requests[0].system_blocks, ensure_ascii=False,
                                                         default=str)


def test_a_tool_the_turn_did_not_offer_is_refused_and_never_runs(granted, profile, site, rated):
    profile(BLOCK)
    no_series()
    task, path, _ = granted()
    script = Script(call("delete_component", {"component_class": "Generator", "name": "G1"}), say("Refused."))
    go(harness_runner.Runner("a", agent_provider=script), path, task)
    assert "G1" in PyPSAService.get_active_context().network.generators.index


def test_a_stage_instruction_is_read_from_the_package_only():
    with pytest.raises(ValueError):
        run_loops.instruction("../../etc@1", "input_gate")
    with pytest.raises(ValueError):
        run_loops.instruction("eh_site@1", "nowhere")


# ── Acceptance 5: budget use is counted by harness code ─────────────────────


def test_rounds_and_tokens_in_the_run_budget_match_the_session_counts_not_the_text(granted, profile, site, rated):
    profile(BLOCK)
    no_series()
    task, path, _ = granted()
    script = Script(call("validate_network", {}), say("I used 1 round and 7 tokens in total."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    used = record["run"]["budget"]["used"]
    provider_calls = len(script.requests)
    assert provider_calls == 2
    assert used["rounds"] == record["run"]["loop"]["rounds"] == 1
    for key, value in USAGE.items():
        assert used[key] == value * provider_calls
    assert record["run"]["loop"]["history"][0]["usage"] == {k: v * provider_calls for k, v in USAGE.items()}
    assert used["cost_eur"] > 0 and not used["unmetered"]


def test_the_envelopes_round_budget_caps_the_loop(granted, profile, site, rated):
    profile({**BLOCK, "budget": {**BLOCK["budget"], "rounds": 1}})
    no_series()
    task, path, _ = granted()
    script = Script(call("update_component", {"component_class": "Generator", "name": "G1",
                                              "attrs": {"marginal_cost": 11.0}}), say("Tried."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    assert record["run"]["loop"]["rounds"] == 1 and record["run"]["loop"]["ended"] == "exhausted"
    assert record["run"]["status"] == "needs_user"


# ── Acceptance 6: data-gap tools need a gap entry with a target ─────────────


def test_unlisted_data_gap_tools_each_get_a_card(granted, profile, site, rated):
    project_id, _ = site
    profile(BLOCK)  # no gaps
    no_series()
    task, path, _ = granted()
    script = Script(call(*generate(project_id)),
                    call("set_weather_choice", {"project_id": project_id, "source": "synthetic", "year": 2020}, 1),
                    say("Both were refused."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    assert refused(path) == [("generate_load_profile", "unlisted_tool"), ("set_weather_choice", "unlisted_tool")]
    assert record["run"]["loop"]["refusals"] == 2
    assert "L1" not in PyPSAService.get_active_context().network.loads_t.p_set.columns


def test_listed_data_gap_tools_are_admitted_for_their_target_only(granted, profile, site, rated):
    project_id, _ = site
    profile(WITH_GAPS)
    no_series()
    task, path, _ = granted()
    script = Script(call("set_weather_choice", {"project_id": project_id, "source": "synthetic", "year": 2020}),
                    call(*generate(project_id), n=1), apply_from(project_id), say("Filled."))
    record = go(harness_runner.Runner("a", agent_provider=script), path, task)
    used = [a["tool"] for a in envelope_of(path)["audit"] if a["event"] == "used"]
    assert used[:3] == ["set_weather_choice", "generate_load_profile", "apply_generated_profile"]
    assert [r["tool"] for r in record["run"]["ledger"]] == ["set_weather_choice", "apply_generated_profile"]


def test_a_gap_entry_never_replaces_a_series_that_is_there(session, site, profile):
    project_id, root = site
    profile(WITH_GAPS)
    task = start()
    run_envelope.decide(session, task["task_id"], "unattended")
    envelope = envelope_of(task_path(root, task))
    gap = next(g for g in envelope["gaps"] if g["tool"] == "apply_generated_profile")
    assert gap["at_grant"] == {}  # L1 has a series at the grant: nothing to fill
    assert run_envelope.covers(task_path(root, task), "generate_load_profile")


def test_a_gap_entry_is_refused_for_a_target_its_tool_does_not_fill():
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope.check_block({**BLOCK, "gaps": [{"tool": "apply_generated_profile",
                                                     "target": {"component": "Generator", "name": "G1",
                                                                "attr": "p_nom"}}]})
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope.check_block({**BLOCK, "gaps": [{"tool": "update_component",
                                                     "target": {"component": "Load", "name": "L1",
                                                                "attr": "p_set"}}]})


def tariff_card(old):
    return {"complete": True, "rows": [{"component_class": "Settings", "name": "Commercial",
                                        "field": "import_tariff_ref", "old": old, "new": "grid-import v1",
                                        "role": "typed"}]}


def test_attach_tariff_is_admitted_only_from_a_listed_gap_and_only_where_none_is_set():
    """The tariff case at the admission seam (the Library item it needs is
    outside the baseline project): unlisted → the card; listed → admitted
    for an unset tariff only."""
    tariff = {"tool": "attach_tariff",
              "target": {"component": "Settings", "name": "Commercial", "attr": "import_tariff_ref"},
              "at_grant": {"Commercial": "gap"}}
    unlisted = {"edits": [], "runs": [], "gaps": []}
    listed = {"edits": [], "runs": [], "gaps": [tariff]}
    assert run_envelope._match_gap(unlisted, "attach_tariff", {}, tariff_card(None), None) == "unlisted_tool"
    assert run_envelope._match_gap(listed, "attach_tariff", {}, tariff_card(None), None) is None
    assert run_envelope._match_gap(listed, "attach_tariff", {}, tariff_card("old-tariff v2"), None) == "not_a_gap"
    weather = {"complete": True, "rows": [{"component_class": "Settings", "name": "Weather", "field": "source",
                                           "old": None, "new": "synthetic", "role": "typed"}]}
    assert run_envelope._match_gap(listed, "set_weather_choice", {}, weather, None) == "unlisted_tool"
