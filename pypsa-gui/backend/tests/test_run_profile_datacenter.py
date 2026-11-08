# ruff: noqa: F811 - pytest fixtures (hub, user_and_db) are imported and re-bound as arguments
"""Study harness 10a: the eh_datacenter@1 profile and its native actions
(GK B (36) D1-D8, Q1-Q5).

- D1: the run is held to one published grid-code profile, frozen with its id,
  label and digest; a republish between the freeze and a dispatch, or the
  result gate, stops the run.
- D2: scorecard rows read only with the run's own readiness step's profile,
  and give no evidence once its digest moved.
- D3, D4: an assumed pass never passes a block row; a warn row reading
  `unknown` is not evaluated, never a pass.
- D5, D6: the campus run optimises nothing and writes only under the campus
  directory; its step reserves and settles the stated bound.
- D7: the registry is closed and is no tool anywhere.
- D8: every row the engine emits is gated or listed as not gated.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import campus_electrical_service as ce
from services import run_actions, run_envelope, run_gates, run_profiles
from services.adequacy import grid_readiness as G
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures
from tests.test_campus_scan_and_grid_code_since_run import _publish

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

REF = "eh_datacenter@1"


def _profile():
    return run_profiles.resolve(REF)


def _rules(stage):
    return {r["id"]: r for r in _profile()["stages"][stage]["rules"]}


# ── the profile and its row table (D8) ───────────────────────────────────────


def test_the_profile_loads_with_the_campus_actions_in_the_solve_stage():
    solve = _profile()["stages"]["solve"]["actions"]
    assert [a["tool"] for a in solve] == [
        "solve_queue_enqueue",
        "run_eh_study",
        run_actions.CAMPUS,
        run_actions.READINESS,
    ]
    assert all(
        a["args"] == run_actions.FROZEN and a["native"] == run_actions.KIND
        for a in solve[2:]
    )
    assert "dc.gridcode.published" in _rules("inputs")
    assert _rules("input_gate")["dc.gridcode.frequency"]["severity"] == "block"


def test_the_row_table_has_no_wildcard_and_its_severities():
    scorecard = {
        r["fact"][len(run_gates.SCORECARD) :]: r
        for r in _profile()["stages"]["result_gate"]["rules"]
        if r["fact"].startswith(run_gates.SCORECARD)
    }
    assert not [f for f in scorecard if "*" in f or f == ""]
    assert {f for f, r in scorecard.items() if r["severity"] == "block"} == {
        "scr",
        "energization_peak:",
        "reactive_range",
    }
    assert (
        scorecard["voltage_stability_pv"]["severity"]
        == scorecard["voltage_stability_qv"]["severity"]
        == "warn"
    )
    assert all(
        r["basis"] and "Input:" in r["basis"] for r in scorecard.values()
    )  # which run produces it
    assert "lole" not in scorecard and run_profiles.DC_NOT_GATED["lole"].startswith(
        "not gated: "
    )


def _emitted(tmp_path, tid, profile, dc_load=None):
    d = tmp_path / f"{tid}-{profile}-{dc_load is not None}"
    d.mkdir()
    spec = T.dc_load_spec(tid, dc_load) if dc_load is not None else None
    n = T.BUILDERS[tid](load_spec=spec) if spec is not None else T.BUILDERS[tid]()
    T.write_sidecars(d, tid)
    if spec is not None:
        meta = json.loads((d / "eh_template.json").read_text())
        meta["dc_load"] = T.dc_load_meta(tid, spec)
        (d / "eh_template.json").write_text(json.dumps(meta))
    out = G.grid_readiness(d, n, profile)
    return [r["id"] for r in out["rows"]] + [r["id"] for r in out["excluded"]]


def _classified(row_id):
    rows = {row for row, *_ in run_profiles.DC_SCORECARD}
    family = next((f for f in run_gates.FAMILIES if row_id.startswith(f)), None)
    return row_id in rows or family in rows or row_id in run_profiles.DC_NOT_GATED


@pytest.mark.parametrize(
    "profile, dc_load",
    [("generic_assumed", None), ("eu_rfg_dcc_ce", None), ("eu_rfg_dcc_ce", {})],
)
def test_every_emitted_row_is_gated_or_listed_as_not_gated(tmp_path, profile, dc_load):
    ids = _emitted(tmp_path, "eh_datacenter", profile, dc_load)
    assert ids and not [i for i in ids if not _classified(i)]
    if profile != "generic_assumed":  # the sidecar rows are classified too
        assert any(i.startswith("ride_through:") for i in ids) and any(
            i.startswith("dynamic:") for i in ids
        )


# ── the scorecard in the result gate (D2-D4, D8) ─────────────────────────────

CODE = {"id": "sagc_dc", "label": "SAGC (project)", "digest": "a" * 64}


def _read_step(code=CODE, stored=None):
    return {
        "id": "solve.4",
        "tool": run_actions.READINESS,
        "native": run_actions.KIND,
        "status": "completed",
        "args": dict(run_actions.FROZEN),
        "frozen": {"grid_code": code},
        "result": {"profile": code["id"], "digest": stored or code["digest"]},
    }


def _env(steps):
    return run_gates.Env(
        task={"steps": steps}, ctx=None, project=object(), project_dir=None
    )


def _result_gate(monkeypatch, card, steps=None, live=CODE["digest"]):
    monkeypatch.setattr(G, "grid_readiness_for", lambda project, profile=None: card)
    for step in [_read_step()] if steps is None else steps:
        step.setdefault("result", {}).setdefault(
            "rows_digest", run_actions.rows_digest(card)
        )
    if steps is None:
        steps = [_read_step()]
        steps[0]["result"]["rows_digest"] = run_actions.rows_digest(card)
    monkeypatch.setattr(
        run_actions, "unchanged", lambda project, code: code["digest"] == live
    )
    rules = [
        r
        for r in _profile()["stages"]["result_gate"]["rules"]
        if r["fact"].startswith(run_gates.SCORECARD)
    ]
    return {
        r["rule"]: r
        for r in run_gates.evaluate(
            rules, _env([_read_step()] if steps is None else steps)
        )
    }


def _shipped_card(tmp_path):
    d = tmp_path / "shipped"
    d.mkdir()
    n = T.BUILDERS["eh_datacenter"]()
    T.write_sidecars(d, "eh_datacenter")
    return G.grid_readiness(d, n, "generic_assumed")


def test_on_the_shipped_template_warn_rows_do_not_end_the_run_and_never_count_as_passes(
    tmp_path, monkeypatch
):
    card = _shipped_card(tmp_path)
    results = _result_gate(monkeypatch, card)
    warn = [r for r in results.values() if r["severity"] == "warn"]
    unknown = {r["id"] for r in card["rows"] if r["verdict"] == "unknown"}
    assert unknown  # 15 of the template's rows read unknown
    for r in warn:
        rows = r["facts"][0].get("rows") or []
        if any(row["verdict"] == "unknown" for row in rows):
            assert r["verdict"] == "not_evaluated" and r["reason"].startswith(
                "not evaluated"
            )
    assert not [
        r
        for r in warn
        if r["verdict"] == "pass"
        and r["facts"][0].get("rows")
        and any(x["verdict"] == "unknown" for x in r["facts"][0]["rows"])
    ]
    assert run_gates.passed(warn)  # D4: warn rows alone never fail the gate
    block = {k: r for k, r in results.items() if r["severity"] == "block"}
    # D3: scr passes on generic_assumed data only, so it is not evaluated, never a pass.
    assert (
        card_row(card, "scr")["verdict"] == "pass" and card_row(card, "scr")["assumed"]
    )
    assert (
        block["dc.scr"]["verdict"] == "not_evaluated"
        and "assumed" in block["dc.scr"]["reason"]
    )
    assert not run_gates.passed(list(block.values()))


def card_row(card, row_id):
    return next(r for r in card["rows"] if r["id"] == row_id)


def test_an_assumed_pass_on_a_block_row_is_not_evaluated_and_on_a_warn_row_is_within_the_screening_limit(
    monkeypatch,
):
    rows = [
        {"id": "scr", "verdict": "pass", "assumed": True},
        {"id": "flicker", "verdict": "pass", "assumed": True},
    ]
    results = _result_gate(monkeypatch, {"rows": rows, "excluded": []})
    assert results["dc.scr"]["verdict"] == "not_evaluated"
    assert results["dc.flicker"]["verdict"] == "pass"
    assert results["dc.flicker"][
        "note"
    ] == "within the screening limit" and "verified" not in json.dumps(
        results["dc.flicker"]
    )
    verified = _result_gate(
        monkeypatch,
        {"rows": [{"id": "scr", "verdict": "pass", "assumed": False}], "excluded": []},
    )
    assert verified["dc.scr"]["verdict"] == "pass"


def test_a_block_row_reading_unknown_fails_closed(monkeypatch):
    results = _result_gate(
        monkeypatch,
        {
            "rows": [{"id": "scr", "verdict": "unknown", "assumed": False}],
            "excluded": [],
        },
    )
    assert results["dc.scr"]["verdict"] in ("fail", "not_evaluated")
    assert not run_gates.passed([results["dc.scr"]])


def test_ride_through_and_dynamic_never_pass(monkeypatch):
    rows = [
        {"id": "ride_through:ups_bridged", "verdict": "pass", "assumed": False},
        {"id": "dynamic:rocof", "verdict": "pass", "assumed": False},
    ]
    results = _result_gate(monkeypatch, {"rows": rows, "excluded": []})
    for rule in ("dc.ride_through", "dc.dynamic"):
        assert (
            results[rule]["verdict"] == "not_evaluated"
            and "never passes" in results[rule]["reason"]
        )


def test_an_off_grid_exclusion_is_not_evaluated_with_its_reason(monkeypatch):
    card = {
        "rows": [],
        "excluded": [{"id": "voltage_stability_pv", "reason": "off grid: no P-V"}],
    }
    results = _result_gate(monkeypatch, card)
    assert results["dc.voltage_stability_pv"]["verdict"] == "not_evaluated"
    assert "off grid" in results["dc.voltage_stability_pv"]["reason"]


def test_a_family_rule_evaluates_every_row_of_the_family(monkeypatch):
    rows = [
        {"id": "energization_peak:2027", "verdict": "pass", "assumed": False},
        {"id": "energization_peak:2030", "verdict": "fail", "assumed": False},
    ]
    results = _result_gate(monkeypatch, {"rows": rows, "excluded": []})
    peak = results["dc.energization_peak"]
    assert peak["verdict"] == "fail" and [
        r["id"] for r in peak["facts"][0]["rows"]
    ] == [r["id"] for r in rows]


@pytest.mark.parametrize(
    "case", ["republished", "stored_other", "no_step", "step_failed"]
)
def test_a_moved_profile_gives_no_evidence_on_every_scorecard_row(monkeypatch, case):
    """D1, D2: a republish between the solve and the gate never passes."""
    rows = [{"id": "scr", "verdict": "pass", "assumed": False}]
    step = _read_step()
    live = CODE["digest"]
    if case == "republished":
        live = "b" * 64
    elif case == "stored_other":
        step = _read_step(stored="c" * 64)
    elif case == "step_failed":
        step["status"] = "failed"
    results = _result_gate(
        monkeypatch,
        {"rows": rows, "excluded": []},
        steps=[] if case == "no_step" else [step],
        live=live,
    )
    assert all(r["verdict"] == "not_evaluated" for r in results.values())
    assert not run_gates.passed(list(results.values()))


def test_the_scorecard_reads_with_the_frozen_profile_id(monkeypatch):
    asked = []
    monkeypatch.setattr(
        G,
        "grid_readiness_for",
        lambda project, profile=None: (
            asked.append(profile) or {"rows": [], "excluded": []}
        ),
    )
    monkeypatch.setattr(run_actions, "unchanged", lambda project, code: True)
    rule = _rules("result_gate")["dc.scr"]
    run_gates.evaluate([rule], _env([_read_step()]))
    assert asked == [CODE["id"]]


# ── the grid-code facts (D1, Q1, Q2) ─────────────────────────────────────────


def test_no_published_profile_waits_for_the_user_to_publish(hub):  # noqa: F811
    env = run_gates.Env(task={}, ctx=None, project=hub, project_dir=None)
    rule = _rules("inputs")["dc.gridcode.published"]
    [result] = run_gates.evaluate([rule], env)
    assert (
        result["verdict"] == "not_evaluated"
        and result["reason"] == "publish the grid-code profile"
    )
    assert not list(
        ce.grid_codes_dir(hub).glob("*.yaml")
    )  # nothing is drafted or published by the run


def test_one_published_profile_is_frozen_with_its_id_label_and_digest(hub):  # noqa: F811
    _publish(hub, "sagc_dc", title="SAGC data centre")
    code = run_actions.choose(hub)
    assert code == {
        "id": "sagc_dc",
        "label": "SAGC data centre",
        "digest": ce.profile_fingerprint(hub, "sagc_dc"),
    }
    frozen = run_actions.freeze(hub)
    assert (
        frozen["settings"]["profile"] == "sagc_dc"
        and frozen["solves"] == run_actions.SOLVES
    )
    assert frozen["digest"] == run_actions.digest(run_actions._core(frozen))
    assert any("settings.json" in w for w in frozen["writes"]) and any(
        "run/" in w for w in frozen["writes"]
    )


def test_with_two_published_the_user_picks_never_the_runner(hub):  # noqa: F811
    _publish(hub, "sagc_a")
    _publish(hub, "sagc_b")
    with pytest.raises(run_actions.FreezeError) as exc:
        run_actions.choose(hub)
    assert exc.value.state == "not_picked" and "campus card" in str(exc.value)
    d = ce.campus_dir(hub)
    (d / ce.SETTINGS_FILE).write_text(json.dumps({"profile": "sagc_b"}))
    assert run_actions.choose(hub)["id"] == "sagc_b"


def test_a_republished_profile_is_not_the_frozen_one(hub):  # noqa: F811
    _publish(hub, "sagc_dc")
    code = run_actions.choose(hub)
    assert run_actions.unchanged(hub, code)
    _publish(
        hub,
        "sagc_dc",
        harmonic_resonance_clearance_h={"value": 0.7, "clause": "x", "source": "code"},
    )
    assert not run_actions.unchanged(hub, code)


def _freq_env(tmp_path, f_hz, given):
    d = tmp_path / f"p{f_hz}{given}"
    (d / ce.SUBDIR).mkdir(parents=True)
    if f_hz is not None:
        (d / ce.SUBDIR / ce.CAMPUS_FILE).write_text(f"campus:\n  f_hz: {f_hz}\n")
    rows = (
        []
        if given is None
        else [
            {"param": run_gates.FREQUENCY_PARAM, "value": given, "status": "confirmed"}
        ]
    )
    return run_gates.Env(
        task={"run": {"intake": {"rows": rows}}},
        ctx=None,
        project=object(),
        project_dir=d,
    )


@pytest.mark.parametrize(
    "f_hz, given, verdict",
    [
        (60, "60", "pass"),
        (50, "60", "fail"),
        (50, "50", "pass"),
        (60, None, "not_evaluated"),
        (None, "50", "not_evaluated"),
    ],
)
def test_the_frequency_rule(tmp_path, monkeypatch, f_hz, given, verdict):
    import services.solver.island as island

    monkeypatch.setattr(island, "campus_f_hz", lambda project_dir: float(f_hz))
    rule = _rules("input_gate")["dc.gridcode.frequency"]
    [result] = run_gates.evaluate([rule], _freq_env(tmp_path, f_hz, given))
    assert result["verdict"] == verdict and result["severity"] == "block"
    if f_hz is None:
        assert result["reason"] == "draft the campus"


def test_the_frequency_param_is_never_tunable():
    # Its intake (and keeping it out of the template's params) comes with 10b's DC chat intake.
    with pytest.raises(run_profiles.ProfileError):
        run_profiles._check_tunable([{"component": "Settings", "attr": "frequency"}])


# ── the closed registry (D7) ─────────────────────────────────────────────────


def test_the_registry_is_no_tool_anywhere():
    from harness import toolsets
    from harness.catalogue import TOOLS

    names = set(run_actions.REGISTRY)
    assert not names & {
        t["name"] for t in TOOLS
    }  # the catalogue, and so the provider offer
    for tools in (
        run_profiles.L1_TOOLS,
        run_profiles.L2_TOOLS,
        run_profiles.EXPLORE_TOOLS,
        run_profiles.BOUND_CONTEXT_TOOLS,
        run_envelope.RUN_TOOLS,
        run_envelope.ENGINE_TOOLS,
        run_envelope.REPORT_TOOLS,
    ):
        assert not names & set(tools)
    blob = json.dumps(
        {
            k: sorted(v) if isinstance(v, (set, frozenset)) else v
            for k, v in vars(toolsets).items()
            if isinstance(v, (list, tuple, set, frozenset, dict))
            and not k.startswith("__")
        },
        default=str,
    )
    assert not [n for n in names if n in blob]
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope.check_block(
            {
                **copy.deepcopy(run_profiles.EH_SITE["envelope"]),
                "runs": [{"tool": run_actions.CAMPUS}],
            }
        )


def test_a_model_turn_call_of_the_campus_study_is_refused():
    envelope = {"runs": [], "edits": [], "gaps": []}
    assert (
        run_envelope._refusal(
            None,
            envelope,
            {},
            run_actions.CAMPUS,
            {},
            "execution",
            False,
            None,
            None,
            {},
        )
        == "unknown_tool"
    )


@pytest.mark.parametrize(
    "action, stage",
    [
        (
            {
                "tool": run_actions.CAMPUS,
                "args": {"profile": "sagc"},
                "native": run_actions.KIND,
            },
            "solve",
        ),
        ({"tool": run_actions.CAMPUS, "args": {}, "native": run_actions.KIND}, "solve"),
        (
            {
                "tool": "dc_publish_code",
                "args": dict(run_actions.FROZEN),
                "native": run_actions.KIND,
            },
            "solve",
        ),
        (
            {
                "tool": run_actions.READINESS,
                "args": dict(run_actions.FROZEN),
                "native": run_actions.KIND,
            },
            "inputs",
        ),
        ({"tool": run_actions.CAMPUS, "args": dict(run_actions.FROZEN)}, "solve"),
    ],
)
def test_load_refuses_other_args_and_actions_outside_the_registry(action, stage):
    profile = copy.deepcopy(run_profiles.EH_DATACENTER)
    profile["stages"][stage]["actions"] = [*profile["stages"][stage]["actions"], action]
    with pytest.raises(run_profiles.ProfileError):
        run_profiles.load(profile)


def test_the_grid_code_tools_are_never_listed():
    for tool in (
        "campus_publish_grid_code",
        "campus_save_grid_code_draft",
        "campus_new_grid_code_draft",
        "campus_delete_grid_code_draft",
        "campus_confirm_grid_code_limit",
        "campus_extract_grid_code",
        "campus_delete_published_grid_code",
    ):
        assert tool in run_envelope.NEVER_LISTED
    assert (
        run_envelope._refusal(
            None,
            {"runs": [], "edits": [], "gaps": []},
            {},
            "campus_publish_grid_code",
            {},
            "execution",
            False,
            None,
            None,
            {},
        )
        == "grid_code_publish"
    )


# ── dispatch, settle and the budget (D1, D5, D6, Q2) ─────────────────────────


def _task_file(hub, frozen, tool=run_actions.CAMPUS, status="pending"):  # noqa: F811
    from services import project_registry

    d = Path(project_registry.project_dir(hub)) / "assistant_tasks"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{uuid.uuid4().hex}.json"
    step = {
        "id": "solve.3",
        "tool": tool,
        "native": run_actions.KIND,
        "args": dict(run_actions.FROZEN),
        "status": status,
        "frozen": frozen,
        **(
            {"card": run_actions.card(frozen)}
            if isinstance(frozen, dict) and "grid_code" in frozen
            else {}
        ),
    }
    stages = {s: {"status": "pending", "attempts": 0} for s in run_profiles.STAGES}
    stages["solve"]["status"] = "active"
    task = {
        "task_id": path.stem,
        "project_id": str(hub.id),
        "owner_id": "u",
        "status": "running",
        "steps": [step],
        "run": {
            "profile": REF,
            "status": "running",
            "stage": "solve",
            "stages": stages,
        },
    }
    path.write_text(json.dumps(task))
    return path


def _ctx(hub):  # noqa: F811
    return SimpleNamespace(project_uuid=hub.id)


def _read(path):
    return json.loads(Path(path).read_text())


def _start(path, hub, approved=True):  # noqa: F811
    """Start the stored step as the runner does after the user approved its
    card (F1): with the digest of the card for the record it stores."""
    if approved is True:
        approved = (run_actions.approval_card(_read(path)["steps"][0]) or {}).get(
            "digest"
        )
    return run_actions.start(
        path, _ctx(hub), {"id": "solve.3"}, instance="a", approved=approved
    )


def test_a_republish_after_the_freeze_stops_the_campus_study(hub, monkeypatch):  # noqa: F811
    _publish(hub, "sagc_dc")
    frozen = run_actions.freeze(hub)
    _publish(
        hub,
        "sagc_dc",
        harmonic_resonance_clearance_h={"value": 0.7, "clause": "x", "source": "code"},
    )
    started = []
    monkeypatch.setattr(ce, "enqueue_run", lambda *a, **k: started.append(a))
    path = _task_file(hub, frozen)
    assert _start(path, hub) == "grid_code_changed"
    task = _read(path)
    assert (
        not started
        and task["run"]["status"] == "needs_user"
        and task["steps"][0]["status"] == "failed"
    )


@pytest.mark.parametrize("change", ["args", "settings", "no_record"])
def test_the_campus_study_runs_only_with_its_frozen_record(hub, monkeypatch, change):  # noqa: F811
    _publish(hub, "sagc_dc")
    frozen = run_actions.freeze(hub)
    if change == "settings":
        frozen["settings"]["k"] = 9
    path = _task_file(hub, None if change == "no_record" else frozen)
    if change == "args":
        task = _read(path)
        task["steps"][0]["args"] = {"profile": "sagc_dc"}
        path.write_text(json.dumps(task))
    monkeypatch.setattr(ce, "enqueue_run", lambda *a, **k: pytest.fail("never queued"))
    assert _start(path, hub) == "dc_args"


def test_a_missing_campus_file_asks_the_user_to_draft_it(hub, monkeypatch):  # noqa: F811
    _publish(hub, "sagc_dc")
    path = _task_file(hub, run_actions.freeze(hub))
    monkeypatch.setattr(ce, "enqueue_run", lambda *a, **k: pytest.fail("never queued"))
    assert _start(path, hub) == "campus_missing"
    assert "draft the campus" in _read(path)["steps"][0]["error"]


def _granted(path, solves):
    envelope = run_envelope.propose(
        path, _read(path), run_profiles.resolve("eh_site@1")
    )
    envelope.update(
        state="granted",
        granted_at="2026-11-08T00:00:00+00:00",
        expires_at="2999-01-01T00:00:00+00:00",
        budget={**envelope["budget"], "solves": solves},
    )
    run_envelope._save(path, envelope)
    assert run_envelope._load(path) is not None


def test_the_campus_study_queues_the_frozen_settings_and_settles_from_its_own_job(
    hub, monkeypatch
):  # noqa: F811
    _publish(hub, "sagc_dc")
    ce.draft(hub)
    frozen = run_actions.freeze(hub)
    path = _task_file(hub, frozen)
    _granted(path, 2)
    job_id = uuid.uuid4()
    queued = []
    monkeypatch.setattr(
        ce,
        "enqueue_run",
        lambda project, settings, user=None: (
            queued.append(settings) or {"id": str(job_id)}
        ),
    )
    assert _start(path, hub) == run_actions.CAMPUS
    assert queued == [frozen["settings"]]
    step = _read(path)["steps"][0]
    assert step["status"] == "waiting" and step["job"]["job_id"] == str(job_id)
    envelope = run_envelope._load(path)
    assert (
        run_envelope.solves_used(envelope) == run_actions.SOLVES
    )  # D5: reserved before the job
    monkeypatch.setattr(run_actions, "_job", lambda jid: {"status": "running"})
    assert run_actions.settle(path) is None  # still running
    ce.run(hub, frozen["settings"])  # the job's body, held to the frozen profile
    monkeypatch.setattr(
        run_actions,
        "_job",
        lambda jid: {"status": "completed"} if jid == str(job_id) else None,
    )
    assert run_actions.settle(path) == "done"
    task = _read(path)
    assert (
        task["steps"][0]["status"] == "completed"
        and task["steps"][0]["result"]["solves"] == run_actions.SOLVES
    )
    row = next(
        a for a in run_envelope._load(path)["audit"] if a["event"] == run_actions.EVENT
    )
    assert row["solves"] == row["reserved"] == run_actions.SOLVES
    assert (
        run_gates._dc_campus_study(
            run_gates.Env(task=task, ctx=None, project=hub, project_dir=None)
        )
        == "done"
    )


@pytest.mark.parametrize(
    "job, used, outcome",
    [
        (None, None, "lost"),
        ({"status": "failed", "error": "boom"}, None, "failed"),
        ({"status": "completed"}, {"profile": "other", "sha256": "x"}, "stale"),
    ],
)
def test_a_campus_job_that_did_not_finish_held_to_the_profile_fails_the_step(
    hub,
    monkeypatch,
    job,
    used,  # noqa: F811
    outcome,
):
    _publish(hub, "sagc_dc")
    path = _task_file(hub, run_actions.freeze(hub), status="waiting")
    task = _read(path)
    task["steps"][0]["job"] = {
        "kind": run_actions.JOB_KIND,
        "job_id": str(uuid.uuid4()),
        "call_id": "c1",
    }
    path.write_text(json.dumps(task))
    _granted(path, 2)
    envelope = run_envelope._load(path)
    run_envelope._audit(
        envelope, run_actions.EVENT, call_id="c1", tool=run_actions.CAMPUS, reserved=1
    )
    run_envelope._save(path, envelope)
    monkeypatch.setattr(run_actions, "_job", lambda jid: job)
    monkeypatch.setattr(ce, "_profile_used", lambda run: used)
    assert run_actions.settle(path) == outcome
    task = _read(path)
    assert (
        task["steps"][0]["status"] == "failed" and task["run"]["status"] == "needs_user"
    )
    assert (
        run_envelope.solves_used(run_envelope._load(path)) == run_actions.SOLVES
    )  # never released down


def test_no_solve_left_refuses_the_campus_study_before_it_queues(hub, monkeypatch):  # noqa: F811
    _publish(hub, "sagc_dc")
    ce.draft(hub)
    path = _task_file(hub, run_actions.freeze(hub))
    _granted(path, 0)
    monkeypatch.setattr(ce, "enqueue_run", lambda *a, **k: pytest.fail("never queued"))
    assert _start(path, hub) == "budget"


def test_the_readiness_step_stores_the_frozen_digest(hub):  # noqa: F811
    _publish(hub, "sagc_dc")
    ce.draft(hub)
    frozen = run_actions.freeze(hub)
    ce.run(hub, frozen["settings"])
    path = _task_file(hub, frozen, tool=run_actions.READINESS)
    assert _start(path, hub) == run_actions.READINESS
    step = _read(path)["steps"][0]
    assert (
        step["status"] == "completed"
        and step["result"]["digest"] == frozen["grid_code"]["digest"]
    )
    assert step["result"]["profile"] == "sagc_dc" and step["result"]["rows"]


def test_the_readiness_step_refuses_a_campus_run_held_to_another_profile(hub):  # noqa: F811
    _publish(hub, "sagc_dc")
    ce.draft(hub)
    frozen = run_actions.freeze(hub)
    ce.run(hub, {**frozen["settings"], "profile": "generic_assumed"})
    path = _task_file(hub, frozen, tool=run_actions.READINESS)
    assert _start(path, hub) == "campus_run_stale"


def _tree(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_the_campus_run_optimises_nothing_and_writes_only_its_run_and_settings(
    hub, monkeypatch
):  # noqa: F811
    """D5, D6: no PyPSA optimisation; never the network or the solver config."""
    import linopy
    from services import project_registry

    _publish(hub, "sagc_dc")
    ce.draft(hub)
    frozen = run_actions.freeze(hub)
    assert frozen["settings"]["invest"] is True
    monkeypatch.setattr(
        linopy.Model, "solve", lambda *a, **k: pytest.fail("the campus run optimised")
    )
    root = Path(project_registry.project_dir(hub))
    before = _tree(root)
    ce.run(hub, frozen["settings"])
    after = _tree(root)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    campus = ce.SUBDIR + "/"
    assert changed and all(
        k.startswith(campus + "run/") or k == campus + ce.SETTINGS_FILE for k in changed
    ), sorted(changed)
    assert before["network.nc"] == after["network.nc"]


def test_the_campus_stage_card_is_always_the_users(hub):  # noqa: F811
    _publish(hub, "sagc_dc")
    path = _task_file(hub, run_actions.freeze(hub))
    assert run_envelope.stage_card_is_users(path, "solve")
    assert not run_envelope.stage_card_is_users(path, "report")
    from services import assistant_tasks

    task = _read(path)
    assert assistant_tasks._carded(task["steps"][0])
    assert not assistant_tasks._carded(
        {**task["steps"][0], "tool": run_actions.READINESS}
    )


def test_a_stage_entered_again_keeps_the_runs_first_freeze(hub):  # noqa: F811
    _publish(hub, "sagc_dc")
    first = run_actions.freeze(hub)
    path = _task_file(hub, first, status="completed")
    _publish(
        hub,
        "sagc_dc",
        harmonic_resonance_clearance_h={"value": 0.7, "clause": "x", "source": "code"},
    )
    again = run_actions.stage_frozen(_read(path))
    assert again == first and not run_actions.unchanged(hub, again["grid_code"])
    steps = run_profiles.materialise(_profile(), "solve", str(hub.id), start=5)
    run_actions.fill(steps, again)
    assert [s["frozen"] for s in steps if run_actions.is_action(s)] == [first, first]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"param": run_gates.FREQUENCY_PARAM, "value": "60", "status": "proposed"}],
        [{"param": run_gates.FREQUENCY_PARAM, "value": 60, "status": "confirmed"}],
    ],
)
def test_with_no_confirmed_row_the_frequency_is_never_read_from_the_network(
    tmp_path, monkeypatch, rows
):
    """GK B (36): the campus and the network both say 60, yet no confirmed
    intake row: not evaluated, with its reason; never a pass, never a fail."""
    import services.solver.island as island

    monkeypatch.setattr(island, "campus_f_hz", lambda project_dir: 60.0)
    env = _freq_env(tmp_path, 60, None)
    env.task["run"]["intake"]["rows"] = rows
    rule = _rules("input_gate")["dc.gridcode.frequency"]
    [result] = run_gates.evaluate([rule], env)
    assert (
        result["verdict"] == "not_evaluated"
        and result["reason"] == "state the grid frequency (Hz)"
    )
    assert not run_gates.passed([result])


def test_the_card_names_the_profile_and_writes_and_an_unfrozen_step_stops_with_its_reason(
    hub,
):
    _publish(hub, "sagc_dc", title="SAGC data centre")
    frozen = run_actions.freeze(hub)
    steps = run_profiles.materialise(_profile(), "solve", str(hub.id))
    run_actions.fill(steps, frozen)
    shown = [s["card"] for s in steps if run_actions.is_action(s)]
    assert shown and all(
        c["grid_code"] == frozen["grid_code"] and c["writes"] == frozen["writes"]
        for c in shown
    )
    path = _task_file(
        hub,
        {
            "unfrozen": {
                "state": "none_published",
                "message": "publish the grid-code profile",
            }
        },
    )
    task = _read(path)
    task["steps"][0]["unfrozen"] = task["steps"][0].pop("frozen")["unfrozen"]
    path.write_text(json.dumps(task))
    assert _start(path, hub) == "dc_none_published"
    assert "publish the grid-code profile" in _read(path)["steps"][0]["error"]


def test_a_step_left_running_by_a_restart_is_uncertain_and_never_run_again(
    hub, monkeypatch
):
    _publish(hub, "sagc_dc")
    path = _task_file(hub, run_actions.freeze(hub), status="running")
    monkeypatch.setattr(ce, "enqueue_run", lambda *a, **k: pytest.fail("never queued"))
    assert run_actions.settle(path) == "uncertain"
    task = _read(path)
    assert (
        task["steps"][0]["status"] == "uncertain"
        and task["run"]["status"] == "needs_user"
    )


def test_a_scorecard_that_changed_after_the_read_gives_no_evidence(monkeypatch):
    read = {
        "rows": [{"id": "scr", "verdict": "fail", "assumed": False}],
        "excluded": [],
    }
    step = _read_step()
    step["result"]["rows_digest"] = run_actions.rows_digest(read)
    now = {"rows": [{"id": "scr", "verdict": "pass", "assumed": False}], "excluded": []}
    results = _result_gate(monkeypatch, now, steps=[step])
    assert all(r["verdict"] == "not_evaluated" for r in results.values())


def test_a_withdrawn_profile_is_not_the_frozen_one(hub):  # noqa: F811
    from services import campus_grid_code_service as gc

    _publish(hub, "sagc_dc")
    code = run_actions.choose(hub)
    gc.delete_published(hub, "sagc_dc")
    assert "sagc_dc" not in run_actions.published(hub)
    assert not run_actions.unchanged(hub, code)


# ── GK B (36) F1-F3: the campus run's own card, its settings, no grant ───────


def test_the_campus_settings_are_the_profiles_whatever_was_saved(hub):  # noqa: F811
    """F2 (a): the profile's campus_study block wins over the saved settings."""
    _publish(hub, "sagc_dc")
    d = ce.campus_dir(hub)
    (d / ce.SETTINGS_FILE).write_text(
        json.dumps({"invest": False, "n_minus_1": False, "k": 2})
    )
    assert run_profiles.resolve(REF)["campus_study"] == {
        "n_minus_1": True,
        "invest": True,
    }
    task = _read(_task_file(hub, None))
    frozen = run_actions.stage_frozen(task)
    assert (
        frozen["settings"]["invest"] is True and frozen["settings"]["n_minus_1"] is True
    )
    assert (
        frozen["settings"]["k"] == 2
    )  # a key the block does not name stays the user's
    card = run_actions.approval_card({"tool": run_actions.CAMPUS, "frozen": frozen})
    assert "N-1 on, investment on" in " ".join(r["note"] for r in card["rows"])


def test_the_approval_card_names_what_the_step_is_held_to(hub):  # noqa: F811
    """F1: the rows the chat's Confirmation card shows (note rows), bound by the record's digest."""
    _publish(hub, "sagc_dc", title="SAGC data centre")
    frozen = run_actions.freeze(hub)
    card = run_actions.approval_card({"tool": run_actions.CAMPUS, "frozen": frozen})
    notes = [r["note"] for r in card["rows"]]
    assert (
        card["digest"] == frozen["digest"] and card["version"] == 1 and card["complete"]
    )
    assert all(
        r["role"] == "note" and r["unit"] == "" and isinstance(r["field"], str)
        for r in card["rows"]
    )
    assert notes == [
        "Grid-code profile: SAGC data centre",
        "Campus study settings: N-1 on, investment on, held to SAGC data centre",
        "Contingency depth (k): 3",
        "Sizing margin: 20%",
        "PCC switchgear owned by the operator: no",
        "PCC transformer taps moved by: campus",
        "Power factor: not set",
        "Saves the campus study results",
        "Saves the campus study settings, held to SAGC data centre",
        "Reserves 1 solve",
        f"Source: grid-code profile sagc_dc · digest {frozen['grid_code']['digest'][:12]} · "
        f"{ce.SUBDIR}/run/, {ce.SUBDIR}/{ce.SETTINGS_FILE}",
    ]
    assert card["says"] == "Run the campus study held to SAGC data centre"
    # F4: a row for every frozen setting (its digest binds them all).
    shown = {
        r["field"].removeprefix("setting.")
        for r in card["rows"]
        if r["field"].startswith("setting.")
    }
    assert shown | {"n_minus_1", "invest", "profile"} == set(frozen["settings"])
    unset = copy.deepcopy(frozen)
    unset["settings"].pop("invest")
    assert (
        "investment not set"
        in run_actions.approval_card({"tool": run_actions.CAMPUS, "frozen": unset})[
            "rows"
        ][1]["note"]
    )
    assert run_actions.approval_card({"tool": run_actions.CAMPUS}) is None
    assert run_actions.needs_card(
        {"tool": run_actions.CAMPUS, "native": run_actions.KIND}
    )
    assert not run_actions.needs_card(
        {"tool": run_actions.READINESS, "native": run_actions.KIND}
    )


@pytest.mark.parametrize("approved", [None, "0" * 64])
def test_the_campus_study_never_runs_without_the_approved_cards_digest(
    hub, monkeypatch, approved
):  # noqa: F811
    _publish(hub, "sagc_dc")
    path = _task_file(hub, run_actions.freeze(hub))
    monkeypatch.setattr(ce, "enqueue_run", lambda *a, **k: pytest.fail("never queued"))
    assert _start(path, hub, approved=approved) == "dc_card"
    assert _read(path)["steps"][0]["status"] == "failed"


class _CardSession:
    """The run's session, as far as a native card uses it."""

    session_id = "run:t"

    def __init__(self, decision):
        import threading

        self.decision, self.go, self.withdrawn = decision, threading.Event(), []

    def issue_confirmation(self, *, tool_name, args, safety_tier):
        self.issued = (tool_name, args, safety_tier)
        return SimpleNamespace(token="tok")

    def wait_for_decision(self, token):
        self.go.wait(10)
        return self.decision

    def withdraw_confirmation(self, token):
        self.withdrawn.append(token)


def _card_run(hub, monkeypatch, decision, *, change=None):  # noqa: F811
    from harness import approvals, runner

    _publish(hub, "sagc_dc")
    frozen = run_actions.freeze(hub)
    path = _task_file(hub, frozen)
    task = _read(path)
    logged = []
    monkeypatch.setattr(
        approvals, "asked", lambda *a, **k: logged.append(("asked", k["tool"])) or True
    )
    monkeypatch.setattr(
        approvals,
        "decided",
        lambda *a, **k: logged.append((k["outcome"], k["by"])) or True,
    )
    queued = []
    monkeypatch.setattr(
        ce, "enqueue_run", lambda *a, **k: queued.append(a) or pytest.fail("held here")
    )
    r, session = runner.Runner(instance="a"), _CardSession(decision)
    assert r._native_card(path, _ctx(hub), session, task, task["steps"][0]) == "waiting"
    live = r.live_card(task["task_id"])
    if change is not None:
        change(path)
    session.go.set()
    r._inflight[task["task_id"]][1].join(10)
    return path, frozen, live, logged, queued, r


def test_the_runner_issues_the_campus_card_and_runs_on_its_approval(hub, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(
        run_actions, "start", lambda path, ctx, step, **k: started.append(k) or "ok"
    )
    path, frozen, live, logged, _, r = _card_run(hub, monkeypatch, "approve")
    assert live["edit_card"] == run_actions.approval_card(_read(path)["steps"][0])
    assert (
        live["tool_name"] == run_actions.CAMPUS and live["confirmation_token"] == "tok"
    )
    assert live["args"] == {"digest": frozen["digest"]}
    assert started == [{"instance": "a", "approved": frozen["digest"]}]
    assert logged == [("asked", run_actions.CAMPUS), ("approved", "user")]
    assert r.live_card(_read(path)["task_id"]) is None  # answered: the card is gone


@pytest.mark.parametrize("decision", ["deny", "expired", "revoked"])
def test_a_campus_card_not_approved_never_runs(hub, monkeypatch, decision):  # noqa: F811
    started = []  # T1: recorded, not failed inside the card's thread (which would swallow it)
    monkeypatch.setattr(run_actions, "start", lambda *a, **k: started.append(k) or "ok")
    path, _, _, logged, _, r = _card_run(hub, monkeypatch, decision)
    assert started == []
    assert (
        logged[-1][0] in ("rejected", "expired", "revoked")
        and r.live_card(_read(path)["task_id"]) is None
    )
    task = _read(path)
    if (
        decision == "deny"
    ):  # W1: the user said no; the step stops for them, no new card while they are there
        assert (
            task["steps"][0]["status"] == "failed"
            and task["run"]["status"] == "needs_user"
        )
        assert "denied" in task["steps"][0]["error"]
    else:  # the user left: still to do, on a new card
        assert task["steps"][0]["status"] == "pending"


def test_an_approval_of_a_card_for_another_record_is_refused_at_dispatch(
    hub, monkeypatch
):  # noqa: F811
    """F1: the record changed after the card was issued (a consistent record,
    card and digest): the approval names the old digest, so nothing runs."""

    def change(path):
        task = _read(path)
        step = task["steps"][0]
        frozen = copy.deepcopy(step["frozen"])
        frozen["settings"]["k"] = 9
        frozen["digest"] = run_actions.digest(run_actions._core(frozen))
        step.update(frozen=frozen, card=run_actions.card(frozen))
        path.write_text(json.dumps(task))

    path, _, _, _, queued, _ = _card_run(hub, monkeypatch, "approve", change=change)
    step = _read(path)["steps"][0]
    assert not queued and step["status"] == "failed" and "approved" in step["error"]


def test_a_grant_naming_the_datacenter_profile_is_refused(hub, monkeypatch):  # noqa: F811
    """F3 (point 4): 10a is stepwise only; no envelope can be proposed or granted."""
    from fastapi import HTTPException

    from services import assistant_tasks

    path = _task_file(hub, None)
    task = {**_read(path), "title": "t"}
    assert run_envelope.propose(path, task, run_profiles.resolve(REF)) is None
    monkeypatch.setattr(assistant_tasks, "_read", lambda task_id: (path, task))
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(SimpleNamespace(), task["task_id"], "unattended")
    assert exc.value.detail["error_kind"] == "envelope_not_grantable"
    forged = run_envelope.propose(
        path, task, run_profiles.resolve("eh_site@2")
    )  # a block from another profile
    assert forged["profile"] == REF
    run_envelope._save(path, forged)
    with pytest.raises(HTTPException) as exc:
        run_envelope.decide(SimpleNamespace(), task["task_id"], "unattended")
    assert exc.value.detail["error_kind"] == "envelope_not_grantable"


# ── the dc.* readers, each edge (mutant survivors at a4dbee10) ──────────────


def _reader_env(hub, steps=(), rows=None, project=True, project_dir=None):  # noqa: F811
    return run_gates.Env(
        task={
            "steps": list(steps),
            "run": {"intake": {"rows": rows}} if rows is not None else {},
        },
        ctx=None,
        project=hub if project else None,
        project_dir=project_dir,
    )


def test_the_published_reader_edges(hub):  # noqa: F811
    assert run_gates._dc_gridcode_published(_reader_env(hub, project=False)) is None
    none = run_gates._dc_gridcode_published(_reader_env(hub))
    assert isinstance(none, run_gates.NoEvidence) and none.value == "none_published"
    _publish(hub, "sagc_dc")
    assert run_gates._dc_gridcode_published(_reader_env(hub)) is True


def test_the_unchanged_reader_edges(hub):  # noqa: F811
    _publish(hub, "sagc_dc")
    step = {
        "id": "solve.4",
        "tool": run_actions.READINESS,
        "native": run_actions.KIND,
        "status": "completed",
        "frozen": {"grid_code": run_actions.choose(hub)},
    }
    for env in (_reader_env(hub), _reader_env(hub, [step], project=False)):
        got = run_gates._dc_gridcode_unchanged(env)
        assert isinstance(got, run_gates.NoEvidence) and got.value == "not_frozen"
    assert run_gates._dc_gridcode_unchanged(_reader_env(hub, [step])) is True
    _publish(
        hub,
        "sagc_dc",
        harmonic_resonance_clearance_h={"value": 0.7, "clause": "x", "source": "code"},
    )
    assert run_gates._dc_gridcode_unchanged(_reader_env(hub, [step])) is False


@pytest.mark.parametrize(
    "status, job, want",
    [
        (None, None, None),
        ("running", None, None),
        ("waiting", None, None),
        ("completed", None, "done"),
        ("failed", {"outcome": "aborted"}, "aborted"),
        ("failed", None, "failed"),
    ],
)
def test_the_campus_study_reader(hub, status, job, want):  # noqa: F811
    steps = (
        []
        if status is None
        else [
            {
                "id": "solve.3",
                "tool": run_actions.CAMPUS,
                "native": run_actions.KIND,
                "status": status,
                **({"job": job} if job else {}),
            }
        ]
    )
    assert run_gates._dc_campus_study(_reader_env(hub, steps)) == want


def test_the_frequency_reader_without_intake_or_campus(hub, tmp_path):  # noqa: F811
    got = run_gates._dc_gridcode_frequency(
        run_gates.Env(task={"run": {}}, ctx=None, project=hub, project_dir=tmp_path)
    )
    assert isinstance(got, run_gates.NoEvidence) and got.value == "not_given"
    row = {"param": run_gates.FREQUENCY_PARAM, "value": "50", "status": "confirmed"}
    got = run_gates._dc_gridcode_frequency(
        _reader_env(hub, rows=[row], project_dir=None)
    )
    assert isinstance(got, run_gates.NoEvidence) and got.value == "campus_missing"


@pytest.mark.parametrize(
    "steps, state",
    [
        ([], "not_read"),
        ([{**_read_step(), "status": "failed"}], "not_read"),
        ([{**_read_step(), "frozen": {}}], "grid_code_changed"),
    ],
)
def test_the_scorecard_without_its_own_read_gives_no_evidence(steps, state):
    got = run_gates._frozen_scorecard(_env(steps))
    assert isinstance(got, run_gates.NoEvidence) and got.value == state


def test_a_never_pass_row_without_a_read_says_so_first():
    got = run_gates._scorecard_reader(run_gates.SCORECARD + "ride_through:")(_env([]))
    assert isinstance(got, run_gates.NoEvidence) and got.value == "not_read"


def test_only_a_warn_pass_on_assumed_rows_is_screening():
    scored = run_gates.Scored(
        "fail", [{"id": "r", "verdict": "unknown", "assumed": True}]
    )
    assert (
        run_gates._scored_problem({"severity": "block"}, {"verdict": "fail"}, scored)
        is None
    )
    failed = {"verdict": "fail"}
    assert (
        run_gates._scored_problem(
            {"severity": "block"},
            failed,
            run_gates.Scored("fail", [{"id": "r", "verdict": "fail", "assumed": True}]),
        )
        is None
    )
    assert "screening" not in failed


@pytest.mark.parametrize(
    "action",
    [
        {
            "tool": "not_an_action",
            "native": run_actions.KIND,
            "args": dict(run_actions.FROZEN),
        },
        {"tool": run_actions.CAMPUS, "args": dict(run_actions.FROZEN)},
    ],
)
def test_a_profile_naming_half_an_action_is_refused(action):
    profile = copy.deepcopy(run_profiles.EH_DATACENTER)
    profile["stages"]["solve"]["actions"].append(action)
    with pytest.raises(run_profiles.ProfileError, match="native actions"):
        run_profiles.load(profile)


def _tick(hub, monkeypatch, *, present=False, seam=None):  # noqa: F811
    """T2: one runner tick (`Runner._step`) on a run whose next step is the campus study."""
    from harness import approvals, runner

    _publish(hub, "sagc_dc")
    path = _task_file(hub, run_actions.freeze(hub))
    task = _read(path)
    monkeypatch.setattr(approvals, "asked", lambda *a, **k: True)
    monkeypatch.setattr(approvals, "decided", lambda *a, **k: True)
    started = []
    monkeypatch.setattr(
        run_actions,
        "start",
        lambda path, ctx, step, **k: started.append(k) or "started",
    )
    r = runner.Runner(instance="a", decisions=seam)
    if present:
        r._present[task["task_id"]] = r.clock() + 300
    session = _CardSession("deny")
    out = r._step(path, _ctx(hub), session)
    return path, task, r, session, started, out


def test_a_tick_with_the_user_there_issues_the_card_and_starts_nothing(
    hub, monkeypatch
):  # noqa: F811
    path, task, r, session, started, out = _tick(hub, monkeypatch, present=True)
    assert out == "waiting" and started == []
    assert (
        r.live_card(task["task_id"])["edit_card"]["digest"]
        == task["steps"][0]["frozen"]["digest"]
    )
    assert _read(path)["run"]["waiting"]["ref"] == "solve.3"
    session.go.set()
    r._inflight[task["task_id"]][1].join(10)


def test_a_tick_with_nobody_there_waits_for_the_user(hub, monkeypatch):  # noqa: F811
    path, task, r, _, started, out = _tick(hub, monkeypatch)
    assert out == "waiting" and started == [] and r.live_card(task["task_id"]) is None
    assert _read(path)["run"]["waiting"] == {
        **_read(path)["run"]["waiting"],
        "kind": "card",
        "ref": "solve.3",
    }


def test_a_seam_approval_runs_with_the_frozen_digest(hub, monkeypatch):  # noqa: F811
    path, task, _, _, started, out = _tick(
        hub, monkeypatch, seam=lambda task_id, ref, args: "approve"
    )
    assert out == "started" and started == [
        {"instance": "a", "approved": task["steps"][0]["frozen"]["digest"]}
    ]


def test_a_seam_deny_starts_nothing(hub, monkeypatch):  # noqa: F811
    _, _, _, _, started, out = _tick(
        hub, monkeypatch, seam=lambda task_id, ref, args: "deny"
    )
    assert out == "waiting" and started == []


def test_every_campus_setting_has_its_words():
    """UX (14): no raw key on the card; each frozen setting outside the settings row has words."""
    assert set(run_actions.SETTING_WORDS) == set(ce.DEFAULTS) - {
        "n_minus_1",
        "invest",
        "profile",
    }
