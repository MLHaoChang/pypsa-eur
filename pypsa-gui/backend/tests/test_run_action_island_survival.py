# ruff: noqa: F811 - pytest fixtures (hub, user_and_db) are imported and re-bound as arguments
"""Study harness 12b (GK B (45) M1-M12, GK B (46) C1-C6): the native action
`mg_island_survival` and the `mg.*` gate facts.

The action's guards run on a stubbed `island_survival.survival` (each test
says what the stub returns); one live test runs the real check on the
shipped `eh_microgrid` template's own solve."""

from __future__ import annotations

import copy
import json
import contextlib
import math
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from services import assistant_tasks, run_actions, run_envelope, run_gates, run_profiles
from services.adequacy import island_config as IC
from services.adequacy import island_survival as S
from tests.test_campus_electrical_service import hub, user_and_db  # noqa: F401 - fixtures

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_templates as T  # noqa: E402

MG = run_actions.MG_SURVIVAL
TID = "eh_microgrid"


def _project_dir(hub):
    from services import project_registry

    return Path(project_registry.project_dir(hub))


def _sidecar(hub, raw=None):
    path = _project_dir(hub) / IC.SIDECAR_NAME
    path.write_text(json.dumps(T.ISLAND_CONFIG[TID] if raw is None else raw))
    return path


def _change_sidecar(hub):
    raw = copy.deepcopy(T.ISLAND_CONFIG[TID])
    raw["requirements"]["sustained_h"]["value"] = (
        float(raw["requirements"]["sustained_h"]["value"]) + 1
    )
    _sidecar(hub, raw)


def _task_file(hub, frozen, tool=MG, *, lease="a", steps=None):
    d = _project_dir(hub) / "assistant_tasks"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{uuid.uuid4().hex}.json"
    kind = run_actions.REGISTRY[tool]["kind"]
    step = {
        "id": "solve.3",
        "tool": tool,
        "native": run_actions.KIND,
        "args": dict(run_actions.MARKERS[kind]),
        "status": "pending",
        "frozen": frozen,
    }
    if isinstance(frozen, dict) and run_actions.record_kind(frozen) == kind:
        try:
            step["card"] = run_actions._card_for(kind, frozen)
        except KeyError:  # a malformed record the test hands in on purpose
            pass
    stages = {s: {"status": "pending", "attempts": 0} for s in run_profiles.STAGES}
    stages["solve"]["status"] = "active"
    task = {
        "task_id": path.stem,
        "project_id": str(hub.id),
        "owner_id": "u",
        "status": "running",
        "steps": steps if steps is not None else [step],
        "run": {
            "profile": "eh_site@1",
            "status": "running",
            "stage": "solve",
            "stages": stages,
            "runner": {"lease_owner": lease},
        },
    }
    path.write_text(json.dumps(task))
    return path


def _read(path):
    return json.loads(Path(path).read_text())


def _ctx(hub, network=None, revision=7):
    return SimpleNamespace(
        project_uuid=hub.id,
        network=network if network is not None else pypsa.Network(),
        network_revision=revision,
        mutation_lock=threading.RLock(),
    )


def _start(path, ctx, *, grant=True):
    """Dispatch the step; with `grant`, on a granted budget of 3 solves
    unless the test wrote an envelope of its own."""
    if grant and run_envelope._load(path) is None:
        _granted(path, 3)
    return run_actions.start(path, ctx, {"id": "solve.3"}, instance="a")


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


def _row(path):
    rows = [
        a
        for a in run_envelope._load(path)["audit"]
        if a["event"] == run_actions.MG_EVENT
    ]
    assert len(rows) == 1
    return rows[0]


def _out(state=S.SURVIVES, **over):
    out = {
        "state": state,
        "reason": None if state == S.SURVIVES else f"{state} words",
        "shortfalls": [],
        "binding": [],
        "fixed": ["generators/p_nom"],
        "solves": 1,
        "solver": {"status": "ok", "condition": "optimal"},
        "sustained_h": 24.0,
        "scenarios": ["seamless"],
        "periods": {},
        "critical_loads": ["hospital_load"],
        "seamless_bridge": None,
        "fuel": {},
        "missing": {},
    }
    return {**out, **over}


def _stub(monkeypatch, out=None, during=None, solves=1):
    """`survival` makes `solves` calls of the action's solve seam, runs
    `during()` between them, and returns `out`."""
    seen = []

    def fake(n, island, *, solve=None):
        seen.append(n)
        for _ in range(solves):
            solve(n, None)  # the action's own seam
        if during:
            during()
        return copy.deepcopy(out or _out())

    monkeypatch.setattr(S, "survival", fake)
    monkeypatch.setattr(S, "_solve", lambda n, ef, **kw: ("ok", "optimal"))
    return seen


# ── the registry and the record kinds (M1, M2, M9) ───────────────────────────


def test_the_island_check_is_a_read_only_registry_row_of_its_own_kind():
    assert run_actions.REGISTRY[MG] == {
        "stage": "solve",
        "reads_only": True,
        "kind": run_actions.MG_KIND,
    }
    run_actions.check(
        {"tool": MG, "native": run_actions.KIND, "args": {"frozen": "mg_island"}},
        "solve",
    )
    with pytest.raises(ValueError, match="frozen marker"):
        run_actions.check(
            {"tool": MG, "native": run_actions.KIND, "args": dict(run_actions.FROZEN)},
            "solve",
        )
    with pytest.raises(ValueError, match="frozen marker"):
        run_actions.check(
            {
                "tool": run_actions.CAMPUS,
                "native": run_actions.KIND,
                "args": {"frozen": "mg_island"},
            },
            "solve",
        )
    assert not run_actions.needs_card({"tool": MG, "native": run_actions.KIND})


def test_an_unknown_record_kind_is_refused_at_load_and_at_dispatch(hub, monkeypatch):
    _sidecar(hub)
    frozen = run_actions.mg_freeze(hub)
    monkeypatch.setitem(
        run_actions.REGISTRY, MG, {**run_actions.REGISTRY[MG], "kind": "zz"}
    )
    with pytest.raises(ValueError, match="no record kind"):
        run_actions.check(
            {"tool": MG, "native": run_actions.KIND, "args": {"frozen": "zz"}}, "solve"
        )
    assert run_actions.kind_of({"tool": MG, "native": run_actions.KIND}) is None
    assert run_actions.kinds([{"tool": MG, "native": run_actions.KIND}]) == []
    monkeypatch.setitem(run_actions.MARKERS, "zz", {"frozen": "zz"})
    path = _task_file(hub, frozen)
    monkeypatch.delitem(run_actions.MARKERS, "zz")
    _stub(monkeypatch, during=lambda: pytest.fail("never run"))
    assert _start(path, _ctx(hub)) == "dc_args"


def test_the_data_centre_record_is_unchanged(hub):
    from tests.test_campus_scan_and_grid_code_since_run import _publish

    _publish(hub, "sagc_dc")
    frozen = run_actions.freeze(hub)
    assert set(frozen) == {"grid_code", "settings", "writes", "solves", "digest"}
    assert frozen["digest"] == run_actions.digest(
        {k: frozen[k] for k in ("grid_code", "settings", "writes", "solves")}
    )
    assert run_actions.record_kind(frozen) == run_actions.DC_KIND
    assert run_actions.FROZEN == {"frozen": "dc_grid_code"}


def test_the_mg_record_digests_the_validated_settings_canonically(hub):
    _sidecar(hub)
    frozen = run_actions.mg_freeze(hub)
    assert set(frozen) == {"kind", "version", "settings_digest", "solves", "digest"}
    assert frozen["settings_digest"] == run_actions.digest(
        IC.validate(T.ISLAND_CONFIG[TID])
    )
    # Key order in the file does not move the digest.
    raw = T.ISLAND_CONFIG[TID]
    _sidecar(hub, dict(reversed(list(raw.items()))))
    assert run_actions.mg_freeze(hub)["settings_digest"] == frozen["settings_digest"]
    assert run_actions.mg_card(frozen) == {
        "island_settings": frozen["settings_digest"][:12],
        "solves": 1,
    }


def test_no_island_or_an_invalid_one_cannot_be_frozen(hub):
    with pytest.raises(run_actions.FreezeError) as exc:
        run_actions.mg_freeze(hub)
    assert exc.value.state == "no_island"
    (_project_dir(hub) / IC.SIDECAR_NAME).write_text("{not json")
    with pytest.raises(run_actions.FreezeError) as exc:
        run_actions.mg_freeze(hub)
    assert exc.value.state == "island_unreadable"


def test_records_never_cross_kinds_at_fill(hub):
    from tests.test_campus_scan_and_grid_code_since_run import _publish

    _publish(hub, "sagc_dc")
    _sidecar(hub)
    dc, mg = run_actions.freeze(hub), run_actions.mg_freeze(hub)

    def steps():
        return [
            {
                "id": "solve.1",
                "tool": run_actions.READINESS,
                "native": run_actions.KIND,
                "args": dict(run_actions.FROZEN),
            },
            {
                "id": "solve.2",
                "tool": MG,
                "native": run_actions.KIND,
                "args": dict(run_actions.MARKERS[run_actions.MG_KIND]),
            },
        ]

    both = steps()
    assert run_actions.kinds(both) == ["dc_grid_code", "mg_island"]
    run_actions.fill(both, dc, run_actions.DC_KIND)
    run_actions.fill(both, mg, run_actions.MG_KIND)
    assert both[0]["frozen"] == dc and both[1]["frozen"] == mg
    assert both[1]["card"] == run_actions.mg_card(mg)
    crossed = steps()
    run_actions.fill(crossed, mg, run_actions.DC_KIND)
    run_actions.fill(crossed, dc, run_actions.MG_KIND)
    assert crossed[0]["unfrozen"]["state"] == "kind_mismatch"
    assert crossed[1]["unfrozen"]["state"] == "kind_mismatch"
    assert "frozen" not in crossed[0] and "frozen" not in crossed[1]
    assert run_actions.approval_card({"tool": MG, "frozen": mg}) is None


def test_a_stage_entered_again_keeps_each_kinds_first_record(hub):
    from tests.test_campus_scan_and_grid_code_since_run import _publish

    _publish(hub, "sagc_dc")
    _sidecar(hub)
    dc, mg = run_actions.freeze(hub), run_actions.mg_freeze(hub)
    task = {
        "steps": [
            {"tool": run_actions.READINESS, "native": run_actions.KIND, "frozen": dc},
            {"tool": MG, "native": run_actions.KIND, "frozen": mg},
        ]
    }
    assert run_actions.stage_frozen(task, run_actions.MG_KIND) == mg
    assert run_actions.stage_frozen(task) == dc
    # A record of another kind on a step is never taken as this kind's: the
    # mg step's dc record is skipped and the stage freezes anew.
    task["steps"][1]["frozen"] = dc
    task["project_id"] = str(hub.id)
    assert run_actions.stage_frozen(task, run_actions.MG_KIND) == mg


# ── dispatch: the frozen record (M2, M9, C2) ─────────────────────────────────


@pytest.mark.parametrize(
    "change",
    ["no_digest", "short_digest", "version", "dc_record", "card", "solves", "kind"],
)
def test_the_check_runs_only_with_its_own_valid_record(hub, monkeypatch, change):
    from tests.test_campus_scan_and_grid_code_since_run import _publish

    _sidecar(hub)
    frozen = run_actions.mg_freeze(hub)
    if change == "no_digest":
        del frozen["settings_digest"]
    elif change == "short_digest":
        frozen["settings_digest"] = frozen["settings_digest"][:12]
    elif change == "version":
        frozen["version"] = 2
    elif change == "solves":
        frozen["solves"] = 0
    elif change == "kind":
        frozen["kind"] = "zz"
    elif change == "dc_record":
        _publish(hub, "sagc_dc")
        frozen = run_actions.freeze(hub)
    if change not in ("dc_record", "card"):
        frozen["digest"] = run_actions.digest(run_actions._mg_core(frozen))
    path = _task_file(hub, frozen)
    task = _read(path)
    task["steps"][0]["card"] = {"island_settings": "x", "solves": 1}
    if change != "card":
        task["steps"][0].pop("card")
        if (
            run_actions.record_kind(frozen) == run_actions.MG_KIND
            and "settings_digest" in frozen
        ):
            task["steps"][0]["card"] = {
                "island_settings": str(frozen["settings_digest"])[:12],
                "solves": frozen.get("solves"),
            }
    path.write_text(json.dumps(task))
    _stub(monkeypatch, during=lambda: pytest.fail("never run"))
    assert _start(path, _ctx(hub)) == "mg_args"
    assert _read(path)["steps"][0]["status"] == "failed"


def test_an_mg_record_on_a_data_centre_step_is_refused(hub, monkeypatch):
    _sidecar(hub)
    mg = run_actions.mg_freeze(hub)
    path = _task_file(hub, mg, tool=run_actions.READINESS)
    assert _start(path, _ctx(hub)) == "dc_args"


def test_an_unfrozen_step_stops_with_its_own_reason(hub, monkeypatch):
    path = _task_file(hub, None)
    task = _read(path)
    task["steps"][0]["unfrozen"] = run_actions.stage_frozen(
        {"project_id": str(hub.id), "steps": []}, run_actions.MG_KIND
    )["unfrozen"]
    path.write_text(json.dumps(task))
    assert _start(path, _ctx(hub)) == "mg_no_island"


def test_settings_changed_between_freeze_and_dispatch_refuse(hub, monkeypatch):
    """C2: the import reads the sidecar again; a change since the freeze
    refuses, and the reservation settles at 0."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 3)
    _change_sidecar(hub)
    _stub(monkeypatch, during=lambda: pytest.fail("never run"))
    assert _start(path, _ctx(hub)) == "mg_island_changed"
    assert _row(path)["solves"] == 0


def test_a_sidecar_gone_at_dispatch_stops_by_name(hub, monkeypatch):
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    (_project_dir(hub) / IC.SIDECAR_NAME).unlink()
    _stub(monkeypatch, during=lambda: pytest.fail("never run"))
    assert _start(path, _ctx(hub)) == "mg_no_island"


def test_a_busy_project_stops_the_check(hub, monkeypatch):
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    ctx = _ctx(hub)
    monkeypatch.setattr("harness.runner.LOCK_TIMEOUT_SECONDS", 0.05)
    held = threading.Event()
    done = threading.Event()

    def hold():
        with ctx.mutation_lock:
            held.set()
            done.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        _stub(monkeypatch, during=lambda: pytest.fail("never run"))
        assert _start(path, ctx) == "mg_busy"
    finally:
        done.set()
        t.join()


# ── dispatch: the run (B1, B2, M4-M8, C1, C3) ────────────────────────────────


def test_the_check_completes_with_its_record_and_spends_one_solve(hub, monkeypatch):
    _sidecar(hub)
    frozen = run_actions.mg_freeze(hub)
    path = _task_file(hub, frozen)
    _granted(path, 3)
    n = pypsa.Network()
    seen = _stub(monkeypatch)
    assert _start(path, _ctx(hub, n)) == MG
    step = _read(path)["steps"][0]
    assert step["status"] == "completed"
    result = step["result"]
    assert run_actions.mg_shape_ok(result)
    assert result["state"] == "survives"
    assert result["settings_digest"] == frozen["settings_digest"]
    assert result["network_revision"] == 7 and result["solves"] == 1
    assert seen[0] is not n  # M4: a copy, never the live network
    row = _row(path)
    assert row["reserved"] == 1 and row["solves"] == 1 and row["tool"] == MG
    assert not run_actions.release(
        path, step["attempt_id"], 1, event=run_actions.MG_EVENT
    )


def test_the_check_solves_on_a_context_of_its_own(hub, monkeypatch):
    from services.pypsa_service import PyPSAService

    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    bound = []

    def fake(n, island, *, solve=None):
        bound.append(PyPSAService._solving_ctx.ctx)
        return _out()

    monkeypatch.setattr(S, "survival", fake)
    ctx = _ctx(hub)
    assert _start(path, ctx) == MG
    assert (
        bound[0] is not None
        and bound[0] is not ctx
        and bound[0].network is not ctx.network
    )
    assert getattr(PyPSAService._solving_ctx, "ctx", None) is None


def test_no_solve_left_stops_before_any_solve(hub, monkeypatch):
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 0)
    _stub(monkeypatch, during=lambda: pytest.fail("never run"))
    assert _start(path, _ctx(hub)) == "mg_no_solve"
    assert not [
        a
        for a in run_envelope._load(path)["audit"]
        if a["event"] == run_actions.MG_EVENT
    ]


@pytest.mark.parametrize("envelope", ["none", "proposed"])
def test_no_granted_budget_stops_before_any_solve(hub, monkeypatch, envelope):
    """B1: the check's one solve comes only from a granted budget. With no
    envelope, or one not granted, it stops by name before it reads the
    network or solves, and the ledger keeps no row."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    if envelope == "proposed":
        run_envelope._save(
            path,
            run_envelope.propose(path, _read(path), run_profiles.resolve("eh_site@1")),
        )
        assert run_envelope._load(path)["state"] != "granted"
    monkeypatch.setattr(S, "survival", lambda *a, **k: pytest.fail("never run"))
    monkeypatch.setattr(S, "_solve", lambda *a, **k: pytest.fail("never solved"))
    monkeypatch.setattr(
        "services.study.findings.copy_network",
        lambda n: pytest.fail("never read"),
    )
    assert _start(path, _ctx(hub), grant=False) == "mg_no_grant"
    step = _read(path)["steps"][0]
    assert step["status"] == "failed" and step["error"].startswith("mg_no_grant")
    assert "result" not in step
    after = run_envelope._load(path)
    if envelope == "none":
        assert after is None
    else:
        assert after["state"] != "granted"
        assert not [a for a in after["audit"] if a["event"] == run_actions.MG_EVENT]


def test_a_second_solve_raises_and_the_reservation_settles_what_was_spent(
    hub, monkeypatch
):
    """C1, M7: one dispatch makes one solver call; the second raises, the
    step stops, and the row settles once with the calls made."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 3)
    _stub(monkeypatch, solves=2)
    assert _start(path, _ctx(hub)) == "mg_check_failed"
    assert (
        _row(path)["solves"] == 2
    )  # the refused second call counted: never under-reported


def test_a_check_that_raises_before_its_solve_settles_at_zero(hub, monkeypatch):
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 3)

    def boom(n, island, *, solve=None):
        raise KeyError("efficiency2")

    monkeypatch.setattr(S, "survival", boom)
    assert _start(path, _ctx(hub)) == "mg_check_failed"
    assert _row(path)["solves"] == 0
    assert _read(path)["run"]["status"] == "needs_user"


def test_the_solve_is_held_to_the_time_limit(monkeypatch):
    got = {}
    monkeypatch.setattr(
        S, "_solve", lambda n, ef, **kw: got.update(kw) or ("ok", "optimal")
    )
    calls = []
    solve = run_actions._mg_solve(calls)
    assert solve(None, None, linearized_unit_commitment=True) == ("ok", "optimal")
    assert got == {
        "solver_options": {"time_limit": run_actions.MG_TIME_LIMIT_S},
        "linearized_unit_commitment": True,
    }
    assert run_actions.MG_TIME_LIMIT_S < 90
    with pytest.raises(RuntimeError):
        solve(None, None)


@pytest.mark.parametrize("edit", ["revision", "settings", "both"])
def test_an_edit_during_the_check_makes_its_result_no_evidence(hub, monkeypatch, edit):
    """M6, C6 (#1257 F1a): the counter and the digest are each compared on
    their own after the solve."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    ctx = _ctx(hub)

    def during():
        if edit in ("revision", "both"):
            ctx.network_revision += 1
        if edit in ("settings", "both"):
            _change_sidecar(hub)

    _stub(monkeypatch, during=during)
    assert _start(path, ctx) == MG
    result = _read(path)["steps"][0]["result"]
    assert (
        result["state"] == run_actions.INPUTS_CHANGED
        and result["checked"] == "survives"
    )
    words = {
        "revision": "the network was edited",
        "settings": "the island settings changed",
    }
    for key, w in words.items():
        assert (w in result["reason"]) == (edit in (key, "both"))


class _EditOnRelease:
    """A context's mutation lock whose first release is followed at once by
    a network edit (the counter bumped, a bus added), as an edit waiting on
    the lock would land."""

    def __init__(self, edit):
        self._lock = threading.RLock()
        self._edit = edit
        self.fired = False

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        self._lock.release()
        if not self.fired:
            self.fired = True
            with self._lock:
                self._edit()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def test_the_solved_copy_is_the_network_at_the_recorded_revision(hub, monkeypatch):
    """C2: the copy the check solves is taken under the lock with the
    revision it records. An edit landing the moment the lock is released is
    not in the solved network, and the result reads not current."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    n = pypsa.Network()
    n.add("Bus", "a")
    ctx = _ctx(hub, n)

    def edit():
        ctx.network_revision += 1
        ctx.network.add("Bus", "edited")

    ctx.mutation_lock = _EditOnRelease(edit)
    seen = _stub(monkeypatch)
    assert _start(path, ctx) == MG
    assert ctx.mutation_lock.fired and "edited" in ctx.network.buses.index
    assert list(seen[0].buses.index) == ["a"]  # the network at revision 7
    result = _read(path)["steps"][0]["result"]
    assert result["network_revision"] == 7
    assert result["state"] == run_actions.INPUTS_CHANGED
    assert "the network was edited" in result["reason"]
    live = run_actions.mg_outcome(
        _read(path), ctx.network_revision, result["settings_digest"]
    )
    assert live["no_evidence"] == run_actions.INPUTS_CHANGED


@pytest.mark.parametrize("loss", ["lease", "attempt", "status"])
def test_a_run_taken_over_during_the_check_keeps_nothing(hub, monkeypatch, loss):
    """C3 (#1123): the check is synchronous and registers no job (no
    activity binding), so only the publish guard (`_held`) stands between
    the result and the step."""
    from harness import runner

    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 3)

    def during():
        def change(t):
            s = t["steps"][0]
            if loss == "lease":
                t["run"]["runner"]["lease_owner"] = "b"
            elif loss == "attempt":
                s["attempt_id"] = "other"
            else:
                s["status"] = "uncertain"

        runner._update(path, change)

    from harness import job_adapters

    bindings = dict(job_adapters._ACTIVITY_BINDINGS)
    _stub(monkeypatch, during=during)
    assert _start(path, _ctx(hub)) == "taken_over"
    assert dict(job_adapters._ACTIVITY_BINDINGS) == bindings
    step = _read(path)["steps"][0]
    assert "result" not in step and step["status"] != "completed"
    assert _row(path)["solves"] == 1


@pytest.mark.parametrize("loss", ["lease", "attempt"])
def test_a_check_that_fails_after_a_takeover_records_nothing(hub, monkeypatch, loss):
    """C3: the stop of a check that broke is written only while the run is
    still this attempt's, like its result."""
    from harness import runner

    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 3)

    def boom(n, island, *, solve=None):
        def change(t):
            if loss == "lease":
                t["run"]["runner"]["lease_owner"] = "b"
            else:
                t["steps"][0]["attempt_id"] = "other"

        runner._update(path, change)
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(S, "survival", boom)
    _start(path, _ctx(hub))
    task = _read(path)
    assert task["steps"][0]["status"] == "running" and "error" not in task["steps"][0]
    assert task["run"]["status"] == "running"
    assert _row(path)["solves"] == 0


def test_the_database_session_is_closed_while_the_check_solves(hub, monkeypatch):
    """M5: the project is resolved in a session that ends before the solve."""
    from services import chat_tools

    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    real, open_ = chat_tools._acting, []

    @contextlib.contextmanager
    def acting():
        with real() as pair:
            open_.append(True)
            try:
                yield pair
            finally:
                open_.pop()

    monkeypatch.setattr(chat_tools, "_acting", acting)
    seen = []

    def fake(n, island, *, solve=None):
        seen.append(list(open_))
        return _out()

    monkeypatch.setattr(S, "survival", fake)
    assert _start(path, _ctx(hub)) == MG
    assert seen == [[]]


def test_a_slowed_solve_holds_neither_lock(hub, monkeypatch):
    """M5: while the check solves, the context's mutation lock and the task
    lock are free: an edit and another run's tick go ahead."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    ctx = _ctx(hub)
    solving, release = threading.Event(), threading.Event()
    free = {}

    def slow(n, island, *, solve=None):
        solving.set()
        release.wait(10)
        return _out()

    monkeypatch.setattr(S, "survival", slow)

    def other():
        solving.wait(10)
        free["mutation"] = ctx.mutation_lock.acquire(timeout=1)
        if free["mutation"]:
            ctx.mutation_lock.release()
        free["task"] = assistant_tasks._LOCK.acquire(timeout=1)
        if free["task"]:
            assistant_tasks._LOCK.release()
        release.set()

    t = threading.Thread(target=other)
    t.start()
    started = time.monotonic()
    assert _start(path, ctx) == MG
    t.join()
    assert free == {"mutation": True, "task": True}
    assert time.monotonic() - started < 10


def test_a_restart_mid_check_settles_its_reservation_once(hub, monkeypatch):
    """`settle`'s interrupted path charges the whole bound on the mg row."""
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _granted(path, 3)
    task = _read(path)
    task["steps"][0].update(status="running", attempt_id="gone")
    path.write_text(json.dumps(task))
    envelope = run_envelope._load(path)
    run_envelope._audit(
        envelope, run_actions.MG_EVENT, call_id="gone", tool=MG, reserved=1
    )
    run_envelope._save(path, envelope)
    assert run_actions._interrupted(path, _read(path))
    assert _row(path)["solves"] == 1
    assert _read(path)["steps"][0]["status"] == "uncertain"


# ── a model turn (M3, B3) ────────────────────────────────────────────────────


def test_a_model_turn_is_refused_the_check_even_under_a_grant_listing_it():
    envelope = {"runs": [{"tool": MG}], "edits": [], "gaps": [], "state": "granted"}
    assert (
        run_envelope._refusal(
            None, envelope, {}, MG, {}, "execution", False, None, None, {}
        )
        == "unknown_tool"
    )


# ── the facts (B4-B8, M10-M12, C4, C5) ───────────────────────────────────────


def _env(hub, task=None, ctx=None):
    return run_gates.Env(
        task=task or {"steps": []},
        ctx=ctx or _ctx(hub),
        project=hub,
        project_dir=_project_dir(hub),
        cache={},
        case=None,
    )


def _rule(fact, op="eq", value=None):
    return {
        "id": fact,
        "fact": fact,
        "op": op,
        "value": value,
        "severity": "warn",
        "reads": "x",
    }


def _gate(hub, fact, op="eq", value=None, task=None, ctx=None):
    return run_gates.evaluate([_rule(fact, op, value)], _env(hub, task, ctx))[0]


def _done(hub, result_over=None, drop=None, revision=7):
    _sidecar(hub)
    frozen = run_actions.mg_freeze(hub)
    result = {
        **{k: v for k, v in _out().items() if k in run_actions.MG_RESULT},
        "kind": run_actions.MG_KIND,
        "version": run_actions.MG_VERSION,
        "settings_digest": frozen["settings_digest"],
        "network_revision": revision,
        "skipped": [],
        **(result_over or {}),
    }
    if drop:
        result.pop(drop)
    return {
        "steps": [
            {
                "id": "solve.3",
                "tool": MG,
                "native": run_actions.KIND,
                "status": "completed",
                "frozen": frozen,
                "result": result,
            }
        ]
    }


def test_survival_survives_and_fails_with_its_shortfalls_named(hub):
    task = _done(hub)
    g = _gate(hub, "mg.island.survival", value="survives", task=task)
    assert g["verdict"] == "pass" and g["facts"][0]["value"] == "survives"
    short = [{"row": "n_k_ride_through", "sentence": "short by 2 MW"}]
    task = _done(hub, {"state": "fails", "shortfalls": short, "reason": "x"})
    g = _gate(hub, "mg.island.survival", value="survives", task=task)
    assert g["verdict"] == "fail"
    assert g["facts"][0]["detail"] == ["n_k_ride_through: short by 2 MW"]


@pytest.mark.parametrize(
    "case, state",
    [
        ("not_run", "not_run"),
        ("running", "not_run"),
        ("missing_key", "unreadable"),
        ("wrong_version", "unreadable"),
        ("wrong_kind", "unreadable"),
        ("bool_revision", "unreadable"),
        ("inputs_changed", "inputs_changed"),
        ("edited", "edited"),
        ("island_changed", "island_changed"),
        ("not_solved", "not_solved"),
        ("not_evaluated", "not_evaluated"),
        ("unknown_state", "unreadable"),
        ("no_counter", "edited"),
        ("no_sidecar", "no_island"),
    ],
)
def test_survival_is_no_evidence_unless_current_and_judged(hub, case, state):
    task, ctx = _done(hub), _ctx(hub)
    result = task["steps"][0]["result"]
    if case == "not_run":
        task = {"steps": []}
    elif case == "running":
        task["steps"][0]["status"] = "running"
    elif case == "missing_key":
        result.pop("shortfalls")
    elif case == "wrong_version":
        result["version"] = 2
    elif case == "wrong_kind":
        result["kind"] = "dc_grid_code"
    elif case == "bool_revision":
        result["network_revision"] = True
    elif case == "inputs_changed":
        result.update(
            state="inputs_changed",
            reason="the network was edited while the island check ran",
        )
    elif case == "edited":
        ctx.network_revision = 8
    elif case == "island_changed":
        _change_sidecar(hub)
    elif case in ("not_solved", "not_evaluated"):
        result.update(state=case, reason=f"{case} words")
    elif case == "unknown_state":
        result["state"] = "maybe"
    elif case == "no_counter":
        ctx = SimpleNamespace(network=ctx.network)
    elif case == "no_sidecar":
        (_project_dir(hub) / IC.SIDECAR_NAME).unlink()
    g = _gate(hub, "mg.island.survival", value="survives", task=task, ctx=ctx)
    assert g["verdict"] == "not_evaluated", g
    assert g["facts"][0]["value"] == state
    assert g["reason"]
    if case in ("not_solved", "not_evaluated"):
        assert g["reason"] == f"{case} words"


def test_fuel_autonomy_is_never_evaluated(hub):
    g = _gate(hub, "mg.fuel.autonomy", value=True)
    assert g["verdict"] == "not_evaluated" and g["reason"] == run_gates.MG_NO_FUEL


def _island_network():
    """Two buses joined by a tie link: the island bus `hospital` and the
    grid bus `grid`, with the critical load on the island."""
    n = pypsa.Network()
    n.set_snapshots(pd.RangeIndex(2))
    n.add("Bus", ["hospital", "grid"])
    n.add("Load", "hospital_load", bus="hospital", p_set=1.0)
    n.add("Link", "subsea_tie", bus0="grid", bus1="hospital", p_nom=5.0, p_max_pu=0.0)
    return n


@pytest.mark.parametrize("state", ["open", "closed", "no_tie", "no_island"])
def test_the_tie_reads_the_live_network_and_fails_closed_by_name(
    hub, monkeypatch, state
):
    _sidecar(hub)
    monkeypatch.setattr(
        S,
        "tie_state",
        lambda n, island: {
            "state": state,
            "ties": [],
            "carrying": ["subsea_tie"] if state == "closed" else [],
        },
    )
    g = _gate(hub, "mg.tie.open", value="open")
    if state == "no_island":
        assert g["verdict"] == "not_evaluated" and g["facts"][0]["value"] == "no_island"
    else:
        assert g["facts"][0]["value"] == state
        assert g["verdict"] == ("pass" if state == "open" else "fail")
        assert g["facts"][0]["detail"] == (["subsea_tie"] if state == "closed" else [])


def test_the_tie_and_critical_loads_are_no_evidence_when_unreadable(hub, monkeypatch):
    _sidecar(hub)

    def boom(n, island):
        raise KeyError("bus")

    monkeypatch.setattr(S, "tie_state", boom)
    monkeypatch.setattr(S, "critical_state", boom)
    for fact in ("mg.tie.open", "mg.critical.tagged"):
        g = _gate(hub, fact, value="x")
        assert (
            g["verdict"] == "not_evaluated" and g["facts"][0]["value"] == "unreadable"
        )
        assert "KeyError" in g["reason"]
    monkeypatch.setattr(S, "tie_state", lambda n, i: {"state": "weird"})
    assert _gate(hub, "mg.tie.open", value="open")["facts"][0]["value"] == "unreadable"


def test_the_tie_on_a_real_network(hub):
    _sidecar(hub)
    n = T.BUILDERS[TID]()
    g = _gate(hub, "mg.tie.open", value="open", ctx=_ctx(hub, n))
    assert g["facts"][0]["value"] in ("open", "no_tie", "closed")
    assert (
        g["facts"][0]["value"]
        == S.tie_state(n, IC.validate(T.ISLAND_CONFIG[TID]))["state"]
    )


@pytest.mark.parametrize("state", ["tagged", "none", "mismatch"])
def test_critical_tagging_names_each_load_out_of_step(hub, monkeypatch, state):
    _sidecar(hub)
    monkeypatch.setattr(
        S,
        "critical_state",
        lambda n, island: {
            "state": state,
            "loads": [],
            "network_only": ["a"] if state == "mismatch" else [],
            "sidecar_only": ["b"] if state == "mismatch" else [],
        },
    )
    g = _gate(hub, "mg.critical.tagged", value="tagged")
    assert g["verdict"] == ("pass" if state == "tagged" else "fail")
    if state == "mismatch":
        assert g["facts"][0]["detail"] == [
            "a: on the network only",
            "b: in the island settings only",
        ]


def _eh(monkeypatch, section, edited=False):
    record = {
        "status": "done",
        "report": {"sections": {"dtc": section}},
        "network_revision": 7,
    }
    from harness import run_eh
    from services import study_state

    monkeypatch.setattr(run_eh, "own_record", lambda task, ctx: record)
    monkeypatch.setattr(study_state, "edited_since", lambda rec, revision: edited)


def _dtc(rows, status="ok"):
    return {"status": status, "note": None, "payload": {"contingencies": rows}}


def test_critical_served_sums_each_contingency_with_its_name(hub, monkeypatch):
    _eh(
        monkeypatch,
        _dtc(
            [
                {
                    "contingency": "subsea_tie",
                    "status": "ok",
                    "critical_unserved_mwh": 1.5,
                },
                {
                    "contingency": "tie_2",
                    "status": "optimal",
                    "critical_unserved_mwh": 0.25,
                },
            ]
        ),
    )
    g = _gate(hub, "mg.island.critical_served", op="le", value=0.0)
    assert g["facts"][0]["value"] == 1.75 and g["facts"][0]["unit"] == "MWh"
    assert g["verdict"] == "fail"
    assert g["facts"][0]["detail"] == ["subsea_tie: 1.5 MWh", "tie_2: 0.25 MWh"]


@pytest.mark.parametrize(
    "case, state",
    [
        ("edited", "inputs_changed"),
        ("skipped", "skipped"),
        ("not_established", "not_established"),
        ("no_section", "unreadable"),
        ("no_rows", "unreadable"),
        ("unsolved", "not_solved"),
        ("none_value", "not_solved"),
        ("nan_value", "not_solved"),
        ("bool_value", "not_solved"),
        ("dict_rows", "unreadable"),
    ],
)
def test_critical_served_is_no_evidence_unless_every_contingency_solved(
    hub, monkeypatch, case, state
):
    row = {"contingency": "subsea_tie", "status": "ok", "critical_unserved_mwh": 0.0}
    section = _dtc([row])
    if case == "skipped":
        section = {
            "status": "skipped",
            "note": "dtc_stress was not requested",
            "payload": None,
        }
    elif case == "not_established":
        section = _dtc([], status="not_established")
    elif case == "no_section":
        section = None
    elif case == "no_rows":
        section = _dtc([])
    elif case == "unsolved":
        row["status"] = "infeasible"
    elif case == "none_value":
        row["critical_unserved_mwh"] = None
    elif case == "nan_value":
        row["critical_unserved_mwh"] = math.nan
    elif case == "bool_value":
        row["critical_unserved_mwh"] = False
    elif case == "dict_rows":
        section = _dtc({"subsea_tie": row})
    _eh(monkeypatch, section, edited=case == "edited")
    g = _gate(hub, "mg.island.critical_served", op="le", value=0.0)
    assert (
        g["verdict"] == "not_evaluated"
        and g["facts"][0]["value"] == state
        and g["reason"]
    )


def test_critical_served_reads_energy_weighted_by_the_snapshot_weightings(
    hub, monkeypatch
):
    """C5: the off-grid stress turns each hour's shortfall into MWh with the
    snapshot weightings (`dtc._bus_unserved_mwh`, the bus roll-up the stress
    stores as `critical_unserved_mwh`), and the fact reads that energy.
    With a weighting of 3 h, 1 MW short over two snapshots is 6 MWh; a
    missing weighting would read 2."""
    from services.adequacy import dtc

    n = _island_network()
    n.snapshot_weightings.loc[:, :] = 3.0
    lost = pd.DataFrame({"hospital_load": [1.0, 1.0]}, index=n.snapshots)
    mwh = dtc._bus_unserved_mwh(
        n, {"hospital"}, sink={"last_lost_load": {"lost_load_t": lost}}
    )
    assert mwh == 6.0
    _eh(
        monkeypatch,
        _dtc(
            [
                {
                    "contingency": "subsea_tie",
                    "status": "ok",
                    "critical_unserved_mwh": mwh,
                }
            ]
        ),
    )
    g = _gate(hub, "mg.island.critical_served", op="le", value=0.0)
    assert g["facts"][0]["value"] == 6.0 and g["facts"][0]["detail"] == [
        "subsea_tie: 6 MWh"
    ]


# The frequency fact reads the run's own campus step and its run files.


def _campus_step(hub, monkeypatch, summary, revision=7, files_match=True):
    from services import campus_electrical_service as ce

    run = _project_dir(hub) / ce.SUBDIR / "run"
    run.mkdir(parents=True, exist_ok=True)
    sidecar = _sidecar(hub)
    os.utime(sidecar, (time.time() - 60, time.time() - 60))  # set before the campus run
    if summary is not None:
        (run / ce.cs.FREQ_SUMMARY_JSON).write_text(
            summary if isinstance(summary, str) else json.dumps(summary)
        )
    monkeypatch.setattr(run_actions, "run_files", lambda r: {"x": "1"})
    return {
        "steps": [
            {
                "id": "solve.2",
                "tool": run_actions.CAMPUS,
                "native": run_actions.KIND,
                "status": "completed",
                "result": {
                    "run_files": {"x": "1" if files_match else "2"},
                    "network_revision": revision,
                },
            }
        ]
    }


def _summary(**over):
    base = {k: [] for k in run_actions.MG_FREQUENCY_LISTS}
    return {**base, **over}


def test_frequency_counts_each_failing_hour_once_and_names_it(hub, monkeypatch):
    task = _campus_step(
        hub,
        monkeypatch,
        _summary(
            failing_hours=[[2030, 5], [2030, 7]],
            e1_failing_hours=[[2030, 5]],
            bridge_failing_hours=[[2030, 7], [2040, 1]],
        ),
    )
    g = _gate(hub, "mg.island.frequency", op="le", value=0, task=task)
    assert g["facts"][0]["value"] == 3 and g["facts"][0]["unit"] == "h"
    assert g["facts"][0]["detail"] == [
        "period 2030 hour 5",
        "period 2030 hour 7",
        "period 2040 hour 1",
    ]
    assert g["verdict"] == "fail"
    assert g["facts"][0]["basis"] == run_actions.MG_FREQUENCY_BASIS
    assert "not weighted" in run_actions.MG_FREQUENCY_BASIS


def test_frequency_counts_hours_not_weighted_hours(hub, monkeypatch):
    """C5: the count is of (period, hour) pairs whatever the snapshot
    weightings, as its words say; the live network's 3 h weighting does
    not triple it."""
    task = _campus_step(hub, monkeypatch, _summary(failing_hours=[[2030, 5]]))
    n = _island_network()
    n.snapshot_weightings.loc[:, :] = 3.0
    g = _gate(hub, "mg.island.frequency", op="le", value=0, task=task, ctx=_ctx(hub, n))
    assert g["facts"][0]["value"] == 1


@pytest.mark.parametrize(
    "case, state",
    [
        ("not_run", "not_run"),
        ("rerun", "campus_rerun"),
        ("edited", "edited"),
        ("no_summary", "not_judged"),
        ("bad_json", "unreadable"),
        ("missing_list", "unreadable"),
        ("bad_hour", "unreadable"),
        ("no_counter", "edited"),
        ("island_changed", "island_changed"),
        ("no_island", "island_changed"),
    ],
)
def test_frequency_is_no_evidence_unless_the_run_is_its_own_and_current(
    hub, monkeypatch, case, state
):
    summary = _summary()
    if case == "no_summary":
        summary = None
    elif case == "bad_json":
        summary = "{nope"
    elif case == "missing_list":
        summary.pop("failing_hours")
    elif case == "bad_hour":
        summary["failing_hours"] = [["x", 1]]
    task = _campus_step(
        hub,
        monkeypatch,
        summary,
        files_match=case != "rerun",
        revision=8 if case == "edited" else 7,
    )
    if case == "not_run":
        task = {"steps": []}
    elif case == "island_changed":
        _change_sidecar(hub)  # B6: the settings moved after the judgement
    elif case == "no_island":
        (_project_dir(hub) / IC.SIDECAR_NAME).unlink()
    ctx = SimpleNamespace(network=pypsa.Network()) if case == "no_counter" else None
    g = _gate(hub, "mg.island.frequency", op="le", value=0, task=task, ctx=ctx)
    assert (
        g["verdict"] == "not_evaluated"
        and g["facts"][0]["value"] == state
        and g["reason"]
    )


def test_each_mg_fact_is_registered_with_its_unit():
    want = {
        "mg.island.survival": None,
        "mg.fuel.autonomy": None,
        "mg.tie.open": None,
        "mg.critical.tagged": None,
        "mg.island.critical_served": "MWh",
        "mg.island.frequency": "h",
    }
    assert {k: run_gates.FACTS[k].unit for k in want} == want


# ── live: the real check on the shipped template ────────────────────────────


def test_a_data_centre_record_relabelled_mg_is_refused_at_dispatch(hub, monkeypatch):
    """Mutant 3: only the record-kind term refuses a valid DC record (same
    core digest, its own DC card) that claims the microgrid kind."""
    from tests.test_campus_scan_and_grid_code_since_run import _publish

    _publish(hub, "sagc_dc")
    dc = run_actions.freeze(hub)
    path = _task_file(
        hub, {**dc, "kind": run_actions.MG_KIND}, tool=run_actions.READINESS
    )
    task = _read(path)
    task["steps"][0]["card"] = run_actions._card_for(run_actions.DC_KIND, dc)
    path.write_text(json.dumps(task))
    assert _start(path, _ctx(hub)) == "dc_args"


def test_a_digest_tampered_past_the_card_is_refused(hub, monkeypatch):
    """The card names only the first 12 characters; the record digest
    covers the rest."""
    _sidecar(hub)
    frozen = run_actions.mg_freeze(hub)
    tail = frozen["settings_digest"][12:]
    frozen["settings_digest"] = frozen["settings_digest"][:12] + tail[::-1]
    assert frozen["settings_digest"] != run_actions.mg_freeze(hub)["settings_digest"]
    path = _task_file(hub, frozen)
    _stub(monkeypatch, during=lambda: pytest.fail("never run"))
    assert _start(path, _ctx(hub)) == "mg_args"


def test_a_lock_busy_after_the_solve_makes_the_result_no_evidence(hub, monkeypatch):
    _sidecar(hub)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    real, calls = run_actions._locked, []

    def locked(ctx, read):
        calls.append(1)
        return real(ctx, read) if len(calls) == 1 else None

    monkeypatch.setattr(run_actions, "_locked", locked)
    _stub(monkeypatch)
    assert _start(path, _ctx(hub)) == MG
    result = _read(path)["steps"][0]["result"]
    assert result["state"] == run_actions.INPUTS_CHANGED
    assert "the network was edited" in result["reason"]
    assert "the island settings changed" in result["reason"]


def _solver_config(hub, cfg):
    (_project_dir(hub) / "solver_config.json").write_text(json.dumps(cfg))


_CODE = "def extra_functionality(n, snapshots):\n    pass\n"


@pytest.mark.parametrize(
    "cfg, skipped",
    [
        (None, []),
        ({}, []),
        ({"extra_functionality_code": "  \n", "solver_options": {}}, []),
        (
            {"extra_functionality_code": _CODE, "solver_options": {"threads": 2}},
            ["user_code", "solver_options"],
        ),
        ({"island_constraints": True, "mode": "pf"}, []),
        (
            {
                "island_constraints": True,
                "commercial": {"poc": {}},
                "extra_functionality_code": _CODE,
                "solver_options": {"threads": 2},
            },
            ["island_apply", "poc_writer", "user_code", "solver_options"],
        ),
    ],
)
def test_the_result_names_each_solve_step_the_check_skipped(
    hub, monkeypatch, cfg, skipped
):
    """C1: the steps of the project's own solve the check does not take,
    named only when this project has them on, and shown in the gate record."""
    _sidecar(hub)
    if cfg is not None:
        _solver_config(hub, cfg)
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _stub(monkeypatch)
    assert _start(path, _ctx(hub)) == MG
    result = _read(path)["steps"][0]["result"]
    assert run_actions.mg_shape_ok(result) and result["skipped"] == skipped
    task = _read(path)
    fact = _gate(hub, "mg.island.survival", value="survives", task=task)["facts"][0]
    assert fact["value"] == "survives"
    if skipped:
        for key in skipped:
            assert run_actions.MG_SKIPPED[key] in fact["basis"]
        for key in set(run_actions.MG_SKIPPED) - set(skipped):
            assert run_actions.MG_SKIPPED[key] not in fact["basis"]
    else:
        assert "basis" not in fact


@pytest.mark.parametrize(
    "raw", ["{not json", '"x"', '{"extra_functionality_code": 5}', "[]", "null"]
)
def test_unreadable_solver_settings_are_named_as_skipped(hub, monkeypatch, raw):
    """N2b: settings the check cannot read are named; never raised, never []."""
    _sidecar(hub)
    (_project_dir(hub) / "solver_config.json").write_text(raw)
    assert run_actions.mg_skipped(_project_dir(hub)) == ["solver_config"]
    path = _task_file(hub, run_actions.mg_freeze(hub))
    _stub(monkeypatch)
    assert _start(path, _ctx(hub)) == MG
    assert _read(path)["steps"][0]["result"]["skipped"] == ["solver_config"]


def _done_result():
    out = {k: v for k, v in _out().items() if k in run_actions.MG_RESULT}
    return {
        **out,
        "kind": run_actions.MG_KIND,
        "version": run_actions.MG_VERSION,
        "settings_digest": "0" * 64,
        "network_revision": 7,
        "skipped": [],
    }


def test_the_result_shape_needs_every_key_with_its_type():
    """C4: each key of MG_RESULT, dropped or of the wrong type, is unreadable."""
    good = _done_result()
    assert run_actions.mg_shape_ok(good)
    assert not run_actions.mg_shape_ok(None) and not run_actions.mg_shape_ok([good])
    for key, typ in run_actions.MG_RESULT.items():
        assert not run_actions.mg_shape_ok(
            {k: v for k, v in good.items() if k != key}
        ), key
        wrong = object() if typ is not list else "x"
        assert not run_actions.mg_shape_ok({**good, key: wrong}), key


def test_a_result_that_is_not_a_record_is_unreadable(hub):
    task = _done(hub)
    task["steps"][0]["result"] = "survives"
    g = _gate(hub, "mg.island.survival", value="survives", task=task)
    assert g["facts"][0]["value"] == "unreadable"
    task = _done(hub, {"shortfalls": "x"})
    g = _gate(hub, "mg.island.survival", value="survives", task=task)
    assert g["facts"][0]["value"] == "unreadable"


@pytest.mark.parametrize(
    "fact", ["mg.tie.open", "mg.critical.tagged", "mg.island.survival"]
)
def test_the_island_facts_name_a_missing_sidecar_or_project(hub, fact):
    import dataclasses

    _sidecar(hub)
    (_project_dir(hub) / IC.SIDECAR_NAME).unlink()
    g = _gate(hub, fact, value="x")
    assert g["verdict"] == "not_evaluated" and g["facts"][0]["value"] == "no_island", g
    env = dataclasses.replace(_env(hub), project=None)
    g = run_gates.evaluate([_rule(fact, "eq", "x")], env)[0]
    assert g["verdict"] == "not_evaluated" and g["facts"][0]["value"] == "not_run", g


def test_an_unknown_critical_state_is_unreadable(hub, monkeypatch):
    _sidecar(hub)
    monkeypatch.setattr(S, "critical_state", lambda n, island: {"state": "weird"})
    g = _gate(hub, "mg.critical.tagged", value="tagged")
    assert g["verdict"] == "not_evaluated" and g["facts"][0]["value"] == "unreadable"


@pytest.mark.live_solve
def test_the_check_on_the_microgrid_templates_own_solve(hub, monkeypatch):
    from tests.test_island_survival import _own_solve

    defaults = pypsa.Network().components.links.defaults
    before = set(defaults.index)
    try:
        n = _own_solve(T.BUILDERS[TID](), TID)
        objective = float(n.objective)
        _sidecar(hub)
        path = _task_file(hub, run_actions.mg_freeze(hub))
        _granted(path, 3)
        ctx = _ctx(hub, n)
        assert _start(path, ctx) == MG
        result = _read(path)["steps"][0]["result"]
        assert result["state"] in (S.SURVIVES, S.FAILS), result
        assert result["solves"] == 1 and _row(path)["solves"] == 1
        assert float(n.objective) == objective  # the live network is never solved
        g = _gate(
            hub, "mg.island.survival", value="survives", task=_read(path), ctx=ctx
        )
        assert g["facts"][0]["value"] == result["state"]
    finally:
        defaults.drop(
            index=[a for a in defaults.index if a not in before], inplace=True
        )


def test_a_sidecar_unit_on_the_mainland_is_no_evidence_by_name(hub):
    """#46 (GK A N5, R2): never a pass and never no_tie."""
    _sidecar(hub)
    n = T.BUILDERS[TID]()
    n.generators.at["diesel_new", "bus"] = "mainland"
    g = _gate(hub, "mg.tie.open", value="open", ctx=_ctx(hub, n))
    assert g["verdict"] == "not_evaluated"
    assert g["facts"][0]["value"] == "unit_on_mainland"
    assert "diesel_new" in g["reason"] and "mainland_supply" in g["reason"]


def test_a_grid_import_link_inside_the_island_is_no_evidence_by_name(hub):
    """#46 R4 through the gate: the mainland is fed only through the Link."""
    _sidecar(hub)
    n = T.BUILDERS[TID]()
    n.remove("Generator", "mainland_supply")
    n.generators.at["diesel_new", "bus"] = "mainland"
    g = _gate(hub, "mg.tie.open", value="open", ctx=_ctx(hub, n))
    assert g["verdict"] == "not_evaluated"
    assert g["facts"][0]["value"] == "unit_on_mainland"
    for word in ("diesel_new", "subsea_tie", "Move the unit off that bus"):
        assert word in g["reason"], word


# SH 40: island hours whose solved tie is open are named, never counted.


def test_frequency_names_tie_open_hours_without_counting_them(hub, monkeypatch):
    task = _campus_step(
        hub,
        monkeypatch,
        _summary(failing_hours=[[2030, 5]], tie_open_hours=[[2030, 9], [2030, 2]]),
    )
    out = run_actions.mg_frequency(hub, task, 7)
    assert out == {"hours": [(2030, 5)], "tie_open_hours": [(2030, 2), (2030, 9)], "note": None}
    g = _gate(hub, "mg.island.frequency", op="le", value=0, task=task)
    assert g["facts"][0]["value"] == 1
    assert g["facts"][0]["detail"] == [
        "period 2030 hour 5",
        "period 2030 hour 2: E1 and bridge not applicable (tie open)",
        "period 2030 hour 9: E1 and bridge not applicable (tie open)",
    ]


def test_frequency_with_every_island_hour_tie_open_says_not_evaluated(hub, monkeypatch):
    note = "not evaluated (tie open on every island hour)"
    task = _campus_step(
        hub,
        monkeypatch,
        _summary(tie_open_hours=[[2030, 3]], e1_bridge_note=note),
    )
    g = _gate(hub, "mg.island.frequency", op="le", value=0, task=task)
    assert g["facts"][0]["value"] == 0
    assert g["facts"][0]["detail"] == [
        "period 2030 hour 3: E1 and bridge not applicable (tie open)",
        f"E1 and bridge: {note}",
    ]


def test_frequency_summary_from_before_the_tie_state_reads_no_open_hours(hub, monkeypatch):
    task = _campus_step(hub, monkeypatch, _summary(failing_hours=[[2030, 5]]))
    assert run_actions.mg_frequency(hub, task, 7) == {
        "hours": [(2030, 5)], "tie_open_hours": [], "note": None}


@pytest.mark.parametrize("bad", [{"tie_open_hours": [["x", 1]]}, {"tie_open_hours": 5},
                                 {"e1_bridge_note": 3}])
def test_frequency_with_a_malformed_tie_open_entry_is_unreadable(hub, monkeypatch, bad):
    task = _campus_step(hub, monkeypatch, _summary(**bad))
    g = _gate(hub, "mg.island.frequency", op="le", value=0, task=task)
    assert g["verdict"] == "not_evaluated" and g["facts"][0]["value"] == "unreadable"
