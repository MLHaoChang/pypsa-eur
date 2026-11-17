# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14 S-C (GK B (61) Q-SC1 = A, S1-S8; GK B (56) C8, C9):
eh_ipp_hybrid@3's native `ppa_price` step reads the PPA price from the report
the run's own investment-case step stored, solves nothing, binds what it read
to the run, and the hand-off reads it back from that record only."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import run_explore, run_handoff, run_ppa
from services import assistant_tasks, run_actions, run_envelope, run_profiles as R

V1, V2, V3 = "eh_ipp_hybrid@1", "eh_ipp_hybrid@2", "eh_ipp_hybrid@3"
V1_SHA = "849693e2af1aaef3475546c099f627b584957e05a5d058b5cecd54d6701fcc62"
V2_SHA = "e26c46c8dab13e048e38e15904fac40daef432ffa680f91ba205abb2117a172a"  # as #1575 builds it
K = run_actions.PPA_KIND
PID = "11111111-1111-1111-1111-111111111111"
TARGET = {"contract_id": "ppa_wind", "target_irr": 0.08, "target_year": 25}
TEMPLATE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "project_templates"
    / "eh_ipp_hybrid"
    / "solver_config.json"
)


def _sha(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


# ── fixtures: a run, its case step, the context and the case's reads ─────────


def _ctx(target=TARGET, started=100.0, a_hash="h1"):
    finance = {} if target is None else {"solve_ppa": dict(target)}
    return SimpleNamespace(
        project_uuid=PID,
        network_revision=4,
        solver_state_lock=threading.Lock(),
        solver_state={
            "solver_config": SimpleNamespace(finance=finance),
            "investment_case": {
                "status": "done",
                "started_at": started,
                "assumptions_hash": a_hash,
            },
        },
    )


@pytest.fixture
def world(monkeypatch):
    """The case's stored report and status body, the results' freshness and
    the input fingerprint, each a value the test sets."""
    state = {
        "report": {
            "assumptions_hash": "h1",
            "sections": {
                "project": {
                    "payload": {
                        "solve_ppa_status": "ok",
                        "solved_ppa_price": 62.5,
                        "currency": "EUR",
                        "solved_ppa_price_money_year": 2024,
                        "flags": [],
                    }
                }
            },
        },
        "body": {
            "status": "done",
            "report": {
                "present": True,
                "stale": False,
                "assumptions_hash": "h1",
                "current_hash": "h1",
            },
        },
        "current": True,
        "fp": ["fp1"],
    }
    monkeypatch.setattr(
        run_ppa, "_ic_reads", lambda ctx: (state["body"], state["report"])
    )
    monkeypatch.setattr(run_ppa, "_results_current", lambda ctx: state["current"])
    monkeypatch.setattr(
        run_ppa,
        "_fingerprint",
        lambda ctx: state["fp"].pop(0) if len(state["fp"]) > 1 else state["fp"][0],
    )
    return state


def _payload(state):
    return state["report"]["sections"]["project"]["payload"]


def _ic_step(started=100.0, job="j1", status="completed"):
    return {
        "id": "solve.2",
        "tool": "run_investment_case",
        "args": {},
        "status": status,
        "job": {"job_id": job, "kind": "investment_case", "started_at": started},
        "result": {
            "job_id": job,
            "final_status": "completed",
            "study": "investment_case",
            "started_at": started,
            "solves": 0,
        },
    }


def _task(tmp_path, ctx, *, ic=None):
    folder = tmp_path / "assistant_tasks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.json"
    steps = [ic or _ic_step(), *R.materialise(R.resolve(V3), "solve", PID)[2:]]
    token = assistant_tasks.RUN_CONTEXT.set(ctx)
    try:
        task = {
            "task_id": path.stem,
            "project_id": PID,
            "status": "running",
            "steps": steps,
            "run": {
                "profile": V3,
                "stage": "solve",
                "status": "running",
                "stages": {"solve": {"status": "active"}},
                "budget": {"used": {"solves": 1}},
                "loop": {"solves_used": 1, "probe_solves": 0},
            },
        }
        run_actions.fill(steps, run_actions.stage_frozen(task, K), K)
    finally:
        assistant_tasks.RUN_CONTEXT.reset(token)
    path.write_text(json.dumps(task))
    return path


def _read(path):
    return json.loads(Path(path).read_text())


def _run(tmp_path, ctx=None, **kw):
    ctx = ctx or _ctx()
    path = _task(tmp_path, ctx, **kw)
    step = _read(path)["steps"][1]
    assert run_actions.start(path, ctx, step, instance=None) == run_actions.PPA_PRICE
    return path, ctx


def _record(path):
    return _read(path)["run"]["ppa_price"]


# ── the profile (S7) ─────────────────────────────────────────────────────────


def test_v1_and_v2_stay_byte_equal_and_startable():
    assert _sha(R.PROFILES[V1]) == V1_SHA and _sha(R.PROFILES[V2]) == V2_SHA
    assert R.resolve(V1)["version"] == 1 and R.resolve(V2)["version"] == 2
    for ref in (V1, V2):
        assert run_actions.PPA_PRICE not in [
            a["tool"] for a in R.resolve(ref)["stages"]["solve"]["actions"]
        ]


def test_v3_adds_only_the_ppa_step_after_the_case_and_its_kpi():
    two, three = copy.deepcopy(R.PROFILES[V2]), copy.deepcopy(R.PROFILES[V3])
    assert three.pop("version") == 3 and two.pop("version") == 2
    assert [a["tool"] for a in three["stages"]["solve"]["actions"]] == [
        "solve_queue_enqueue",
        "run_investment_case",
        run_actions.PPA_PRICE,
    ]
    assert three["stages"]["solve"]["actions"].pop() == {
        "tool": run_actions.PPA_PRICE,
        "args": {"frozen": K},
        "native": run_actions.KIND,
    }
    assert three["kpis"].pop() == R.PPA_KPI and "section" not in R.PPA_KPI
    assert two == three  # the budget too: the read solves nothing
    assert R.resolve(V3)["envelope"]["budget"]["solves"] == 5


def test_a_run_started_on_v2_resolves_v2_at_every_later_stage():
    for _stage in ("solve", "result_gate", "explore", "report", "handoff"):
        profile = R.resolve(V2)
        assert profile["version"] == 2 and _sha(R.PROFILES[V2]) == V2_SHA
        assert not any(k.get("record") for k in profile["kpis"])
    assert run_explore._profile({"run": {"profile": V2}})["version"] == 2


def test_the_ppa_step_before_the_case_refuses_at_load():
    bad = copy.deepcopy(R.PROFILES[V3])
    actions = bad["stages"]["solve"]["actions"]
    actions.insert(1, actions.pop())
    with pytest.raises(R.ProfileError, match="after the run's own run_investment_case"):
        R.load(bad)


def test_a_record_kpi_with_no_step_writing_it_refuses_at_load():
    bad = copy.deepcopy(R.PROFILES[V3])
    bad["stages"]["solve"]["actions"].pop()
    with pytest.raises(R.ProfileError, match="written by no step"):
        R.load(bad)


# ── the card and the target (S5) ─────────────────────────────────────────────


def test_the_template_target_is_frozen_with_its_value_unit_year_contract_and_source(
    tmp_path, world
):
    finance = json.loads(TEMPLATE_CONFIG.read_text())["finance"]
    assert finance["solve_ppa"] == TARGET
    step = _read(_task(tmp_path, _ctx(target=finance["solve_ppa"])))["steps"][1]
    assert (
        step["frozen"]["target"] == TARGET
        and step["frozen"]["source_path"] == "finance.solve_ppa.target_irr"
    )
    words = [r["note"] for r in step["card"]["rows"]]
    assert words[:3] == [
        "Target return: 8% post-tax equity IRR",
        "Reached in operating year 25",
        "Contract priced: Wind PPA",
    ]
    assert (
        "Source: the project's investment settings, finance.solve_ppa.target_irr"
        in words
    )
    assert not any(
        re.search(r"[0-9a-f]{12}", w) or "ppa_wind" in w for w in words
    )  # UX #1332


@pytest.mark.parametrize(
    "target",
    [
        None,
        {"target_irr": math.nan, "target_year": 25},
        {"target_irr": math.inf, "target_year": 25},
        {"target_irr": "0.08", "target_year": 25},
        {"target_irr": True, "target_year": 25},
        {"target_irr": 0.08, "target_year": 2.5},
        {"target_irr": 0.08},
    ],
)
def test_no_or_a_non_finite_target_reads_not_evaluated_never_a_default(
    tmp_path, world, target
):
    path, _ = _run(tmp_path, _ctx(target=target))
    step = _read(path)["steps"][1]
    assert step["frozen"]["target"] is None and step["card"]["rows"][0][
        "note"
    ].startswith("Not evaluated")
    rec = _record(path)
    assert (
        rec["status"] == "not_evaluated"
        and rec["price"] is None
        and rec["target_irr"] is None
    )
    if target is None:
        assert rec["reason"] == run_ppa.NOT_SET


def test_the_run_reads_its_own_price_with_currency_and_money_year(tmp_path, world):
    path, ctx = _run(tmp_path)
    rec = _record(path)
    assert rec == {
        "version": 1,
        "run_id": path.stem,
        "step": "solve.3",
        "ic": {"job_id": "j1", "started_at": 100.0},
        "assumptions_hash": "h1",
        "network_revision": 4,
        "fingerprint": "fp1",
        "target_irr": 0.08,
        "target_year": 25,
        "contract_id": "ppa_wind",
        "source_path": "finance.solve_ppa.target_irr",
        "price": 62.5,
        "currency": "EUR",
        "money_year": 2024,
        "status": "solved",
        "reason": None,
    }
    assert _read(path)["steps"][1]["status"] == "completed"
    assert run_ppa.read(_read(path), ctx) == {
        "state": "read",
        "reason": None,
        "value": 62.5,
        "unit": "EUR/MWh (2024 money)",
    }


# ── S1: the run's own case, never the project's latest ───────────────────────


def test_a_newer_human_case_between_the_steps_is_not_read(tmp_path, world):
    path, _ = _run(
        tmp_path, _ctx(started=200.0)
    )  # the user ran the case again after the run's step
    rec = _record(path)
    assert (
        rec["status"] == "stale"
        and rec["price"] is None
        and "newer investment case" in rec["reason"]
    )


def test_a_case_step_that_did_not_complete_reads_not_evaluated(tmp_path, world):
    path, _ = _run(tmp_path, ic=_ic_step(status="failed"))
    assert _record(path)["status"] == "not_evaluated"


# ── S2: a status allow-list ──────────────────────────────────────────────────


ENGINE = Path(__file__).resolve().parents[1] / "services" / "finance" / "engine.py"
STATUSES = [
    "solve_ppa_ambiguous_contract",
    "solve_ppa_contract_not_found",
    "solve_ppa_not_linear",
    "solve_ppa_not_owner_sold",
    "solve_ppa_needs_redispatch",
    "solve_ppa_price_unknown",
    "solve_ppa_cash_not_established",
    "solve_ppa_no_root:target_met_at_zero_price",
    "solve_ppa_no_root:cash_not_established_above",
    "solve_ppa_no_root",
    "solve_ppa_irr_ambiguous:0.07",
    None,
    "solved",
    "OK",
]


def test_every_status_the_engine_emits_is_tested():
    emitted = set(
        re.findall(r'out\["solve_ppa_status"\] = \(?"([a-z_:]+)"', ENGINE.read_text())
    )
    assert emitted - {"ok"} <= {s.split(":0")[0] if s else s for s in STATUSES} | {
        "solve_ppa_irr_ambiguous:"
    }


@pytest.mark.parametrize("status", STATUSES)
def test_any_status_but_ok_reads_not_evaluated(tmp_path, world, status):
    _payload(world)["solve_ppa_status"] = status
    path, _ = _run(tmp_path)
    assert _record(path)["status"] == "not_evaluated" and _record(path)["price"] is None


@pytest.mark.parametrize(
    "flag", ["solve_ppa_target_year_clamped", "solve_ppa_irr_multiple_sign_changes"]
)
def test_an_ok_status_with_a_solve_flag_reads_not_evaluated(tmp_path, world, flag):
    _payload(world)["flags"] = ["counterfactual_basis:site_today", flag]
    path, _ = _run(tmp_path)
    assert _record(path)["status"] == "not_evaluated"


@pytest.mark.parametrize(
    "change",
    [
        {"solved_ppa_price": 0.0},
        {"solved_ppa_price": -3.0},
        {"solved_ppa_price": math.nan},
        {"solved_ppa_price": None},
        {"solved_ppa_price": "62.5"},
        {"currency": None},
        {"solved_ppa_price_money_year": None},
    ],
)
def test_a_price_at_the_search_floor_or_not_finite_reads_not_evaluated(
    tmp_path, world, change
):
    _payload(world).update(change)
    path, _ = _run(tmp_path)
    assert _record(path)["status"] == "not_evaluated"


# ── S3: zero solves ──────────────────────────────────────────────────────────


def test_the_step_charges_nothing_and_solves_nothing(tmp_path, world, monkeypatch):
    from pypsa.optimization.optimize import OptimizationAccessor

    from services.finance import engine

    def spy(*a, **k):
        raise AssertionError("the PPA price step started a solve")

    for owner, name in (
        (engine, "solve_ppa"),
        (engine, "run_case"),
        (OptimizationAccessor, "__call__"),
        (OptimizationAccessor, "solve_model"),
    ):
        monkeypatch.setattr(owner, name, spy)
    envelope = {
        "state": "granted",
        "budget": {"solves": 5},
        "audit": [{"event": "used", "call_id": "a", "tool": "solve_queue_enqueue"}],
    }
    saves = []
    monkeypatch.setattr(run_envelope, "_load", lambda path: envelope)
    monkeypatch.setattr(run_envelope, "_save", lambda path, env: saves.append(env))
    before = copy.deepcopy(envelope)
    path, _ = _run(tmp_path)
    task = _read(path)
    assert task["run"]["ppa_price"]["status"] == "solved"
    assert run_envelope.solves_used(envelope) == run_envelope.solves_used(before) == 1
    assert envelope == before and saves == []
    assert task["run"]["loop"]["solves_used"] + task["run"]["loop"]["probe_solves"] == 1
    assert task["run"]["budget"] == {"used": {"solves": 1}} and run_ppa.SOLVES == 0


# ── S4: the binding, at the step ─────────────────────────────────────────────


def test_a_stored_report_that_is_not_the_cases_own_reads_stale(tmp_path, world):
    world["report"]["assumptions_hash"] = "h2"  # the run's case record still says h1
    world["body"]["report"].update(assumptions_hash="h2", current_hash="h2")
    path, _ = _run(tmp_path)
    assert (
        _record(path)["status"] == "stale"
        and "not this run's own" in _record(path)["reason"]
    )


def test_a_case_whose_current_inputs_differ_reads_stale(tmp_path, world):
    world["body"]["report"].update(
        stale=True, current_hash="h9"
    )  # the report's own hash is still h1
    path, _ = _run(tmp_path)
    assert (
        _record(path)["status"] == "stale"
        and "inputs changed" in _record(path)["reason"]
    )


def test_results_that_no_longer_describe_the_network_read_stale(tmp_path, world):
    world["current"] = False
    path, _ = _run(tmp_path)
    assert _record(path)["status"] == "stale"


def test_a_fingerprint_that_moves_during_the_read_reads_stale(tmp_path, world):
    world["fp"] = ["fp1", "fp2"]
    path, _ = _run(tmp_path)
    assert (
        _record(path)["status"] == "stale"
        and "changed while" in _record(path)["reason"]
    )


def test_a_target_edited_after_the_card_reads_stale(tmp_path, world):
    ctx = _ctx()
    path = _task(tmp_path, ctx)
    ctx.solver_state["solver_config"].finance["solve_ppa"]["target_irr"] = 0.1
    run_actions.start(path, ctx, _read(path)["steps"][1], instance=None)
    assert _record(path)["status"] == "stale"


# ── C9 / S6: the read-back ───────────────────────────────────────────────────


def test_an_edit_after_the_step_reads_stale(tmp_path, world):
    path, ctx = _run(tmp_path)
    ctx.network_revision = 5
    assert run_ppa.read(_read(path), ctx)["state"] == "stale"


def test_another_fingerprint_at_the_read_reads_stale(tmp_path, world):
    path, ctx = _run(tmp_path)
    world["fp"] = ["fp9"]
    assert run_ppa.read(_read(path), ctx)["state"] == "stale"


def test_a_changed_case_at_the_read_reads_stale(tmp_path, world):
    path, ctx = _run(tmp_path)
    world["body"]["report"].update(stale=True, current_hash="h9")
    assert run_ppa.read(_read(path), ctx)["state"] == "stale"
    world["body"]["report"].update(stale=False, current_hash="h1")
    ctx.solver_state["investment_case"]["started_at"] = 300.0
    assert run_ppa.read(_read(path), ctx)["state"] == "stale"


def test_a_record_of_another_run_reads_stale_with_the_bindings_cleared(tmp_path, world):
    """#1123: the run id is checked from the record, not from any bound activity."""
    path, _ = _run(tmp_path)
    task = _read(path)
    task["run"]["ppa_price"]["run_id"] = "another-run"
    assert assistant_tasks.RUN_CONTEXT.get() is None
    assert run_ppa.read(task, None) == {
        "state": "stale",
        "reason": "the PPA price record is another run's",
        "value": None,
        "unit": None,
    }


def test_a_case_run_again_after_the_step_reads_stale(tmp_path, world):
    path, ctx = _run(tmp_path)
    task = _read(path)
    task["steps"].append({**_ic_step(started=150.0, job="j2"), "id": "solve.5"})
    assert run_ppa.read(task, ctx)["state"] == "stale"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: "not a record",
        lambda r: [],
        lambda r: {},
        lambda r: {**r, "price": math.nan},
        lambda r: {**r, "price": "62.5"},
        lambda r: {**r, "price": 0.0},
        lambda r: {**r, "run_id": 5},
        lambda r: {**r, "network_revision": True},
        lambda r: {**r, "fingerprint": None},
        lambda r: {**r, "status": "read"},
        lambda r: {**r, "version": 2},
        lambda r: {**r, "ic": None},
        lambda r: {**r, "target_irr": math.inf},
        lambda r: {**r, "currency": ""},
        lambda r: {**r, "status": "stale", "reason": None},
    ],
)
def test_a_malformed_record_reads_not_evaluated(tmp_path, world, mutate):
    path, ctx = _run(tmp_path)
    task = _read(path)
    task["run"]["ppa_price"] = mutate(task["run"]["ppa_price"])
    assert run_ppa.read(task, ctx) == {
        "state": "not_evaluated",
        "reason": run_ppa.UNREADABLE,
        "value": None,
        "unit": None,
    }


def test_a_raising_binding_read_reads_not_evaluated(tmp_path, world, monkeypatch):
    path, ctx = _run(tmp_path)
    monkeypatch.setattr(run_ppa, "_ic_reads", lambda ctx: 1 / 0)
    assert run_ppa.read(_read(path), ctx)["state"] == "not_evaluated"


def test_the_kpi_reads_the_record_only_never_retained_results(
    tmp_path, world, monkeypatch
):
    from services import retained_results
    from services.pypsa_service import PyPSAService

    def adapter(section):
        raise AssertionError("a record KPI never reads retained results")

    monkeypatch.setattr(retained_results, "adapter", adapter)
    path, ctx = _run(tmp_path)
    monkeypatch.setattr(PyPSAService, "get_active_context", staticmethod(lambda: ctx))
    profile = {"kpis": [R.PPA_KPI]}
    task = _read(path)
    [row] = run_handoff.read_kpis(profile, task)
    assert (row["status"], row["value"], row["unit"], row["source"]) == (
        "read",
        62.5,
        "EUR/MWh (2024 money)",
        "run.ppa_price",
    )
    task["run"]["ppa_price"]["price"] = math.nan  # a malformed record never falls back
    [row] = run_handoff.read_kpis(profile, task)
    assert (row["status"], row["value"]) == ("not_evaluated", None)
    del task["run"]["ppa_price"]
    assert run_handoff.read_kpis(profile, task)[0]["status"] == "not_evaluated"
    assert run_handoff.read_kpis(profile)[0]["status"] == "not_evaluated"


def test_the_handoff_gate_holds_until_an_edit_after_the_step(
    tmp_path, world, monkeypatch
):
    from services.pypsa_service import PyPSAService

    path, ctx = _run(tmp_path)
    monkeypatch.setattr(PyPSAService, "get_active_context", staticmethod(lambda: ctx))
    profile = {"kpis": [R.PPA_KPI]}
    task = _read(path)
    task["run"]["handoff"] = {"kpis": run_handoff.read_kpis(profile, task)}
    assert run_handoff.kpis_read(task, profile) is True
    ctx.network_revision = 5
    assert run_handoff.kpis_read(task, profile) is False


# ── C8: no model turn solves the price ───────────────────────────────────────


def _refusal(monkeypatch, tool, *, call, step, kept=()):
    from services.results import baseline, finance_case

    plan = baseline.BaselinePlan(removed=(), kept=kept, flags=())
    monkeypatch.setattr(finance_case, "baseline_plan", lambda n, cfg, owner=None: plan)
    monkeypatch.setattr(run_envelope, "_standing", lambda *a, **k: None)
    monkeypatch.setattr(run_envelope, "_names_another_project", lambda *a: False)
    envelope = copy.deepcopy(R.resolve(V3)["envelope"])
    envelope["runs"].append({"tool": tool})  # even a grant that names it
    ctx = SimpleNamespace(network=object(), solver_state={"solver_config": object()})
    return run_envelope._refusal(
        SimpleNamespace(run_step_call=step),
        envelope,
        {},
        tool,
        {},
        "write",
        False,
        None,
        ctx,
        {"call_id": call},
    )


@pytest.mark.parametrize(
    "call,step", [("c1", "c1"), ("c1", None), ("c1", "c2"), (None, None)]
)
def test_a_model_turn_never_solves_the_ppa_price_under_the_grant(
    monkeypatch, call, step
):
    assert (
        _refusal(monkeypatch, "solve_ppa_price", call=call, step=step)
        == "own_step_only"
    )


def test_no_stage_or_grant_of_v3_offers_solve_ppa_price():
    profile = R.resolve(V3)
    assert "solve_ppa_price" not in {t["tool"] for t in profile["envelope"]["runs"]}
    for stage in profile["stages"].values():
        assert "solve_ppa_price" not in [a["tool"] for a in stage.get("actions", [])]
        assert "solve_ppa_price" not in (stage.get("agent") or {}).get("tools", ())
    with pytest.raises(run_envelope.EnvelopeError):
        run_envelope._check_run({"tool": "solve_ppa_price"})


def test_the_case_still_refuses_a_kept_asset_under_v3(monkeypatch):
    kept = (("StorageUnit", "old_bess"),)
    assert (
        _refusal(monkeypatch, "run_investment_case", call="c1", step="c1", kept=kept)
        == "baseline_solve"
    )
    assert _refusal(monkeypatch, "run_investment_case", call="c1", step="c1") is None
