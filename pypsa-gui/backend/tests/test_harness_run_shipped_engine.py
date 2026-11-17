# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
# ruff: noqa: F811 -- the `baseline` fixture is imported by name
"""Study harness 14, S-B2b (GK B (58) D1-D7, GK B (59) G1-G8): the shipped
cases' engine on a real `eh_ipp_hybrid` project created through the route.
The solve queue is a spy (no LP): what is under test is the fork, the write,
the reservation and the judging."""

from __future__ import annotations

import contextvars
import inspect
import uuid
import json
import threading
import time
from pathlib import Path

import pytest

from harness import run_explore, run_shipped, run_shipped_engine as E, shipped_fork
from services import (
    chat_tools,
    project_registry,
    run_actions,
    run_envelope,
    workflow_tools,
)
from services.pypsa_service import PyPSAService
from tests.test_run_profile_ipp_runs import REF, baseline  # noqa: F401

INSTANCE = "i-shipped"
PICK = ["low_price", "cheap_battery"]  # two LP cases; every finance case rides along (P3)
LP_IDS = ["low_price", "cheap_battery"]
TPL = Path(__file__).resolve().parents[1] / "project_templates" / "eh_ipp_hybrid"


def _envelope(path, task, solves):
    run_envelope.path_for(path).write_text(
        json.dumps(
            {
                "id": "e1",
                "task_id": task["task_id"],
                "project_id": task["project_id"],
                "owner_id": None,
                "version": run_envelope.VERSION,
                "granted_at": "2026-11-14T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "fingerprint": None,
                "chain": [],
                "profile": REF,
                "edits": [],
                "runs": [],
                "budget": {"solves": solves, "rounds": 2, "wall_time_h": 8.0},
                "research": {},
                "notify": {},
                "state": "granted",
                "audit": [],
            }
        )
    )


def _task(tmp_path, project_id, *, picked=PICK, solves=5):
    """A run in its explore stage holding one `shipped_cases` step, frozen."""
    (tmp_path / "assistant_tasks").mkdir(exist_ok=True)
    path = tmp_path / "assistant_tasks" / "t-shipped.json"
    step = {
        "id": "explore.1",
        "tool": run_actions.SHIPPED,
        "args": dict(run_actions.MARKERS[run_actions.SHIPPED_KIND]),
        "native": run_actions.KIND,
        "status": "pending",
    }
    task = {
        "task_id": "a1b2c3d4-shipped",
        "project_id": project_id,
        "status": "running",
        "steps": [step],
        "run": {
            "profile": REF,
            "status": "running",
            "stage": "explore",
            "stages": {"explore": {"status": "active"}},
            "explore": {"shipped_pick": picked},
            "runner": {"lease_owner": INSTANCE, "lease_until": time.time() + 600},
            "cases": [],
        },
    }
    run_actions.fill(
        task["steps"],
        run_actions.stage_frozen(task, run_actions.SHIPPED_KIND),
        run_actions.SHIPPED_KIND,
    )
    path.write_text(json.dumps(task))
    _envelope(path, task, solves)
    return path


def _read(path):
    return json.loads(Path(path).read_text())


def _cases(path):
    return {c["name"]: c for c in _read(path)["run"]["cases"]}


def _audit(path):
    return [
        a
        for a in json.loads(run_envelope.path_for(path).read_text())["audit"]
        if a["event"] == E.EVENT
    ]


class _Ctx:
    def __init__(self, project_id):
        self.project_uuid = project_id


@pytest.fixture
def queue(monkeypatch):
    """The solve queue as a spy: each enqueue's project id, a fake job id."""
    calls = []

    def enqueue(project_id, rerun_current=False):
        calls.append(project_id)
        return {"id": f"job-{len(calls)}", "status": "queued"}

    monkeypatch.setattr(chat_tools, "solve_queue_enqueue", enqueue)
    return calls


def _start(path, project_id, approved="card"):
    task = _read(path)
    digest = task["steps"][0]["frozen"]["digest"] if approved == "card" else approved
    return run_actions.start(
        path, _Ctx(project_id), task["steps"][0], instance=INSTANCE, approved=digest
    )


def _children(base_id):
    from db.models import Project

    with chat_tools._acting() as (db, _):
        rows = (
            db.query(Project)
            .filter(Project.parent_project_id == uuid.UUID(base_id))
            .all()
        )
        return {str(r.id): r.name for r in rows}


def _fork(path, name):
    with chat_tools._acting() as (db, user):
        row = project_registry.resolve_project(
            db, user, _cases(path)[name]["project_id"]
        )
        ctx = workflow_tools._load_context(row)
        record = json.loads(
            (
                project_registry.project_dir(row) / workflow_tools.SENSITIVITY_CASE_FILE
            ).read_text()
        )
    return row, ctx, record


# ── the runner's entry (G7, #875) ────────────────────────────────────────────


def test_the_runner_entry_takes_only_the_case_id_and_the_approved_digest():
    assert list(inspect.signature(E.run_case).parameters) == [
        "path",
        "case_id",
        "digest",
    ]
    for helper in (shipped_fork.create, shipped_fork.save, shipped_fork.held):
        assert not {"patch_cfg", "edit_network", "changes", "cases"} & set(
            inspect.signature(helper).parameters
        )


def test_the_sweep_takes_no_fork_callables_through_its_args(baseline):
    _, pid, _ = baseline
    case = {
        "new_name": "Sweep extra",
        "changes": [
            {
                "component_class": "StorageUnit",
                "names": ["bess"],
                "attribute": "p_nom_max",
                "value": 9.0,
            }
        ],
        "patch_cfg": "x",
    }
    with pytest.raises(Exception) as err:
        workflow_tools.run_sensitivity_sweep(pid, [case])
    assert err.value.detail["error_kind"] == "invalid_sensitivity_cases"


# ── the card's digest (seam A) ───────────────────────────────────────────────


@pytest.mark.parametrize("approved", [None, "0" * 64])
def test_no_card_or_a_wrong_one_refuses_before_any_fork(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
    approved,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    monkeypatch.setattr(shipped_fork, "create", lambda *a, **k: pytest.fail("forked"))
    assert _start(path, pid, approved) == "shipped_card"
    step = _read(path)["steps"][0]
    assert step["status"] == "failed" and step["error"].startswith("shipped_card")
    assert "shipped cases" in step["error"]
    assert _read(path)["run"]["cases"] == [] and queue == [] and _audit(path) == []


# ── admission, forks and reservations (C3, D6, G5) ───────────────────────────


def test_every_case_is_admitted_with_its_status_and_only_lp_cases_fork(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    applied = []
    real_apply = run_shipped.apply

    def apply(cfg, changes, resolve, *, check_old=True):
        if check_old:  # the fork's write, not admission's dry run
            applied.append(changes)
        return real_apply(cfg, changes, resolve, check_old=check_old)

    monkeypatch.setattr(run_shipped, "apply", apply)
    assert _start(path, pid) == run_actions.SHIPPED
    cases = _cases(path)
    assert len(cases) == 8  # the true total: every case of the file
    assert {n for n, c in cases.items() if c["status"] == "running"} == set(LP_IDS)
    for name in ("ppa_price_minus_10", "merchant_heavy", "longer_ppa"):
        # P3: a finance case costs no solve, so it is never picked away.
        assert cases[name]["status"] == "not_evaluated"
        assert "Investment case per case not run" in cases[name]["reason"]
    assert (
        cases["cannibalised"]["status"] == "not_run"
        and cases["cannibalised"]["reason"] == E.NOT_PICKED
    )
    for name in ("tight_connection", "faster_degradation"):
        assert cases[name]["status"] == "not_run" and cases[name]["reason"]
    # G5: only the LP cases forked, wrote and queued; the others reserved nothing.
    forks = _children(pid)
    assert sorted(forks.values()) == sorted(
        run_explore.case_project_name(_read(path), {"name": n}) for n in LP_IDS
    )
    assert queue == [cases[n]["project_id"] for n in LP_IDS]
    assert len(applied) == 2
    for name, case in cases.items():
        want = 1 if name in LP_IDS else 0
        assert case["solves"]["reserved"] == want
        assert ("project_id" in case) == (name in LP_IDS)
    rows = _audit(path)
    assert [r["reserved"] for r in rows] == [1, 1]
    assert {r["call_id"] for r in rows} == {
        cases[n]["solves"]["call_id"] for n in LP_IDS
    }
    assert _read(path)["steps"][0]["status"] == "completed"


def test_a_budget_short_of_a_case_runs_it_not_and_says_so(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, solves=1)
    _start(path, pid)
    cases = _cases(path)
    assert cases["low_price"]["status"] == "running"
    assert cases["cheap_battery"]["status"] == "not_run"
    assert "no solve left" in cases["cheap_battery"]["reason"]
    assert len(queue) == 1 and len(_audit(path)) == 1


def test_the_battery_goes_through_the_derive_and_the_price_refs_are_swapped(
    baseline,
    tmp_path,
    queue,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    _start(path, pid)
    _, ctx, record = _fork(path, "cheap_battery")
    bess = ctx.network.storage_units.loc["bess"]
    assert bess["inv_power_overnight"] == pytest.approx(149749.53)
    assert bess["inv_energy_overnight"] == pytest.approx(132902.7)
    base_cc = record["shipped"]["base"]["capital_cost"]["bess"]
    assert bess["capital_cost"] < base_cc  # derived from the cheaper parts
    _, ctx, _ = _fork(path, "low_price")
    commercial = E._cfg(ctx)["commercial"]
    assert commercial["export_price_ref"]["id"] == "ipp_da_low"
    assert commercial["import_price_ref"]["id"] == "ipp_da_low"
    assert all(
        c["reference_price"]["id"] == "ipp_da_low" for c in commercial["contracts"]
    )


# ── D4: the approved cases are current before the fork and before the solve ─


def _edit_cases(root):
    path = Path(root) / "cases.json"
    data = json.loads(path.read_text())
    data["note"] = "edited after approval"
    path.write_text(json.dumps(data))


def test_an_edit_after_approval_refuses_before_any_fork(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
):
    _, pid, root = baseline
    path = _task(tmp_path, pid)
    _edit_cases(root)
    _start(path, pid)
    cases = _cases(path)
    for name in LP_IDS:
        assert cases[name]["status"] == "failed"
        assert "shipped_cases_changed" in cases[name]["reason"]
        assert "project_id" not in cases[name]
        assert cases[name]["solves"]["outcome"] == "released"
    assert _children(pid) == {} and queue == []
    assert [r.get("solves") for r in _audit(path)] == [0, 0]


def test_a_library_ref_moved_after_approval_refuses(baseline, tmp_path, queue):
    ctx, pid, root = baseline
    path = _task(tmp_path, pid)
    cfg_path = Path(root) / "solver_config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["commercial"]["export_price_ref"]["version"] = 99
    cfg_path.write_text(json.dumps(cfg))
    _start(path, pid)
    assert all("shipped_cases_changed" in _cases(path)[n]["reason"] for n in LP_IDS)
    assert queue == []


def test_an_edit_while_the_fork_is_written_refuses_at_the_second_check(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
):
    _, pid, root = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    real_save = shipped_fork.save

    def save(*a, **k):
        real_save(*a, **k)
        _edit_cases(root)

    monkeypatch.setattr(shipped_fork, "save", save)
    _start(path, pid)
    case = _cases(path)["low_price"]
    assert case["status"] == "failed" and "shipped_cases_changed" in case["reason"]
    assert case["reason"].startswith("require_current_again")
    assert case["project_id"] in _children(pid)  # D7: the fork is named
    assert case["solves"]["outcome"] == "released" and queue == []


# ── G6: a raise at each step after the fork ──────────────────────────────────


def _boom(*a, **k):
    raise RuntimeError("boom")


def _boom_on_write(real):
    def apply(cfg, changes, resolve, *, check_old=True):
        if check_old:
            raise RuntimeError("boom")
        return real(cfg, changes, resolve, check_old=check_old)

    return apply


@pytest.mark.parametrize(
    "stage,target,counted",
    [
        ("patch", (run_shipped, "apply"), False),
        ("edit", (E, "_battery"), False),
        ("save", (shipped_fork, "save"), False),
        ("require_current_again", None, False),
        ("enqueue", (chat_tools, "solve_queue_enqueue"), True),
    ],
)
def test_a_raise_after_the_fork_names_the_fork_and_settles_its_reservation(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
    stage,
    target,
    counted,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["cheap_battery"])
    if target is None:
        calls = {"n": 0}
        real = E._require

        def second(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return real(*a, **k)

        monkeypatch.setattr(E, "_require", second)
    elif stage == "patch":
        monkeypatch.setattr(run_shipped, "apply", _boom_on_write(run_shipped.apply))
    else:
        monkeypatch.setattr(*target, _boom)
    _start(path, pid)
    case = _cases(path)["cheap_battery"]
    assert case["status"] == "failed" and case["reason"].startswith(stage)
    forks = _children(pid)
    assert list(forks) == [case["project_id"]]  # no orphan: the one fork is the case's
    assert case["solves"]["outcome"] == ("counted" if counted else "released")
    (row,) = _audit(path)
    assert row["solves"] == (1 if counted else 0)


# ── G7: what run_case refuses ────────────────────────────────────────────────


def test_a_case_not_admitted_or_under_another_digest_refuses(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    task = _read(path)
    task["steps"][0].update(status="running", attempt_id="a1")
    path.write_text(json.dumps(task))
    assert (
        E._admit(path, "explore.1", task["steps"][0]["frozen"], ("a1", INSTANCE))
        == LP_IDS
    )
    digest = _read(path)["steps"][0]["frozen"]["cases_digest"]
    with pytest.raises(run_shipped.ShippedError) as err:
        E.run_case(path, "tight_connection", digest)
    assert err.value.kind == "shipped_case_not_runnable"
    with pytest.raises(run_shipped.ShippedError) as err:
        E.run_case(path, "low_price", "f" * 64)
    assert err.value.kind == "shipped_cases_changed"
    assert _children(pid) == {} and queue == []


def test_an_edit_after_admission_refuses_under_the_new_digest_too(
    baseline, tmp_path, queue, monkeypatch
):
    """GK B (59) F3: a caller holding the NEW current digest after cases.json
    changed is refused by the admitted case's own digest, before any fork."""
    _, pid, root = baseline
    path = _task(tmp_path, pid)
    task = _read(path)
    task["steps"][0].update(status="running", attempt_id="a1")
    path.write_text(json.dumps(task))
    E._admit(path, "explore.1", task["steps"][0]["frozen"], ("a1", INSTANCE))
    _edit_cases(root)
    task = _read(path)
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, pid)
        _, sources = E._sources(project, E._template(run_explore._profile(task)))
        current = run_shipped.load(
            project_registry.project_dir(project),
            E._template(run_explore._profile(task)),
            **sources,
        )["digest"]
    assert current != _cases(path)["low_price"]["digest"]
    monkeypatch.setattr(shipped_fork, "create", lambda *a, **k: pytest.fail("forked"))
    with pytest.raises(run_shipped.ShippedError) as err:
        E.run_case(path, "low_price", current)
    assert err.value.kind == "shipped_cases_changed"
    assert _children(pid) == {} and queue == []


def test_a_fork_name_already_taken_refuses_and_overwrites_nothing(
    baseline,
    client,
    tmp_path,
    queue,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    name = run_explore.case_project_name(_read(path), {"name": "low_price"})
    assert client.post(f"/api/projects/{name}").status_code == 200
    _start(path, pid)
    case = _cases(path)["low_price"]
    assert case["status"] == "failed" and "shipped_fork_exists" in case["reason"]
    assert "project_id" not in case and queue == []


# ── D3 / G1: the read-modify-write holds the child's writer lock ─────────────


def test_a_second_writer_inside_the_read_waits_for_the_write(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
):
    from routers import projects

    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    real_apply = run_shipped.apply
    seen = {}

    def apply(cfg, changes, resolve, *, check_old=True):
        if not check_old:
            return real_apply(cfg, changes, resolve, check_old=False)
        case = _cases(path)["low_price"]
        with chat_tools._acting() as (db, user):
            child = project_registry.resolve_project(db, user, case["project_id"])
        resident = PyPSAService.get_context(project_registry.registry_key(child))
        writer = threading.Thread(
            target=projects._save_context,
            args=(resident, child.name),
            kwargs={"storage_dir": Path(resident.storage_dir)},
        )
        writer.start()
        writer.join(0.5)
        seen["blocked"] = writer.is_alive()  # the real save waits on the lock we hold
        seen["writer"] = writer
        return real_apply(cfg, changes, resolve, check_old=True)

    monkeypatch.setattr(run_shipped, "apply", apply)
    _start(path, pid)
    seen["writer"].join(30)
    assert seen["blocked"] is True
    assert _cases(path)["low_price"]["status"] == "running"


def test_a_solver_config_put_past_the_gate_waits_for_the_write(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
):
    """GK B (59) F4: `PUT /api/simulation/solver_config` holds the solver-state
    lock, not `mutation_lock`. A request already past the harness gate, on the
    child, waits for the case's write and then merges over it: the case's
    change and the request's both survive."""
    from models.schemas import SolverConfigSchema
    from routers import simulation

    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    real_apply = run_shipped.apply
    seen = {}

    def put(resident):
        with chat_tools._acting() as (db, user), workflow_tools._isolated(resident):
            simulation.update_solver_config(
                SolverConfigSchema(discount_rate=0.0123), db=db, user=user
            )

    def apply(cfg, changes, resolve, *, check_old=True):
        if not check_old:
            return real_apply(cfg, changes, resolve, check_old=False)
        case = _cases(path)["low_price"]
        with chat_tools._acting() as (db, user):
            child = project_registry.resolve_project(db, user, case["project_id"])
        resident = PyPSAService.get_context(project_registry.registry_key(child))
        writer = threading.Thread(
            target=contextvars.copy_context().run, args=(put, resident)
        )
        writer.start()
        writer.join(0.5)
        seen.update(blocked=writer.is_alive(), writer=writer, ctx=resident)
        return real_apply(cfg, changes, resolve, check_old=True)

    monkeypatch.setattr(run_shipped, "apply", apply)
    _start(path, pid)
    seen["writer"].join(30)
    assert seen["blocked"] is True
    assert _cases(path)["low_price"]["status"] == "running"
    cfg = seen["ctx"].solver_state["solver_config"]
    assert cfg.discount_rate == 0.0123
    assert cfg.commercial["export_price_ref"]["id"] != "ipp_da_base"


# ── D1 / G2: the series a case names ─────────────────────────────────────────


def _org(pid):
    with chat_tools._acting() as (db, user):
        return project_registry.resolve_project(db, user, pid).org_id


def _versions(org, name):
    from db.models import LibraryItem

    with chat_tools._acting() as (db, _):
        return sorted(
            r.version
            for r in db.query(LibraryItem).filter(
                LibraryItem.org_id == org, LibraryItem.name == name
            )
        )


def _drop(org, name):
    from db.models import LibraryItem

    with chat_tools._acting() as (db, _):
        db.query(LibraryItem).filter(
            LibraryItem.org_id == org, LibraryItem.name == name
        ).delete()
        db.commit()


def test_a_series_the_library_lacks_is_minted_once_from_the_verified_file(baseline):
    _, pid, _ = baseline
    org = _org(pid)
    _drop(org, "ipp_da_low")
    refs = []

    def mint():
        with chat_tools._acting() as (db, _):
            refs.append(E.resolver(db, org, TPL)("ipp_da_low"))

    threads = [
        threading.Thread(target=contextvars.copy_context().run, args=(mint,))
        for _ in range(2)
    ]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert _versions(org, "ipp_da_low") == [1]
    assert refs[0] == refs[1] and refs[0]["id"] == "ipp_da_low"


def test_a_library_series_with_another_hash_is_never_reminted(baseline):
    import pandas as pd

    from services.library import series_store

    _, pid, _ = baseline
    org = _org(pid)
    with chat_tools._acting() as (db, _):
        before = series_store.latest_ref(db, org, "ipp_da_low")
        idx = pd.date_range("2030-01-01", periods=3, freq="h", tz="UTC")
        series_store.put_series(
            db,
            org,
            "ipp_da_low",
            pd.Series([1.0, 2.0, 3.0], index=idx),
            {"unit": "EUR/MWh"},
        )
        versions = _versions(org, "ipp_da_low")
        assert versions[-1] == before.version + 1
        assert E.resolver(db, org, TPL)("ipp_da_low") is None
    assert _versions(org, "ipp_da_low") == versions


def test_a_template_series_changed_after_verify_resolves_nothing(baseline, tmp_path):
    import shutil

    _, pid, _ = baseline
    tpl = tmp_path / "tpl"
    shutil.copytree(TPL / "price_series", tpl / "price_series")
    victim = next(p for p in (tpl / "price_series").iterdir() if p.name != "meta.json")
    victim.write_bytes(victim.read_bytes() + b"\n")
    with chat_tools._acting() as (db, _):
        assert E.resolver(db, _org(pid), tpl)("ipp_da_low") is None


# ── judging: kind and witness (GK B (58), G4) ────────────────────────────────


def _fresh_record(ctx, record):
    """The record as a write that left `ctx` as it is now would have made."""
    return {**record, "input_fingerprint": E._fingerprint(ctx)}


def test_the_witness_holds_on_each_lp_fork(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    _start(path, pid)
    for name in LP_IDS:
        _, ctx, record = _fork(path, name)
        assert E.witness(_cases(path)[name], ctx, record) is None


def test_a_record_of_another_digest_or_changed_inputs_is_not_evaluated(
    baseline,
    tmp_path,
    queue,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    _start(path, pid)
    case = _cases(path)["low_price"]
    _, ctx, record = _fork(path, "low_price")
    other = {**record, "shipped": {**record["shipped"], "digest": "f" * 64}}
    assert "approved cases" in E.witness(case, ctx, other)
    assert "changed after" in E.witness(case, ctx, {**record, "input_fingerprint": "x"})
    same = {**record, "baseline_fingerprint": record["input_fingerprint"]}
    assert "are the base's" in E.witness(case, ctx, same)


def test_a_price_ref_left_at_the_base_is_not_evaluated(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    _start(path, pid)
    case = _cases(path)["low_price"]
    _, ctx, record = _fork(path, "low_price")
    cfg = ctx.solver_state["solver_config"]
    cfg.commercial["export_price_ref"]["id"] = "ipp_da_base"
    why = E.witness(case, ctx, _fresh_record(ctx, record))
    assert why.startswith("commercial.export_price_ref.id is ipp_da_base on the fork")


def test_a_raw_battery_write_without_the_derive_is_not_evaluated(
    baseline,
    tmp_path,
    queue,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["cheap_battery"])
    _start(path, pid)
    case = _cases(path)["cheap_battery"]
    _, ctx, record = _fork(path, "cheap_battery")
    # The overnight columns moved, the capital cost did not (a raw write).
    ctx.network.storage_units.at["bess", "capital_cost"] = record["shipped"]["base"][
        "capital_cost"
    ]["bess"]
    why = E.witness(case, ctx, _fresh_record(ctx, record))
    assert "capital cost did not move" in why


def test_a_battery_whose_inputs_are_the_bases_is_not_evaluated(
    baseline, tmp_path, queue
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["cheap_battery"])
    _start(path, pid)
    case = _cases(path)["cheap_battery"]
    _, ctx, record = _fork(path, "cheap_battery")
    # The columns and the capital cost moved; the fingerprint is the base's.
    same = {**record, "baseline_fingerprint": record["input_fingerprint"]}
    assert "are the base's" in E.witness(case, ctx, same)


def test_an_overnight_cost_left_at_the_base_is_not_evaluated(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["cheap_battery"])
    _start(path, pid)
    case = _cases(path)["cheap_battery"]
    _, ctx, record = _fork(path, "cheap_battery")
    ctx.network.storage_units.at["bess", "inv_power_overnight"] = 213927.9
    why = E.witness(case, ctx, _fresh_record(ctx, record))
    assert why.startswith(
        "StorageUnit.bess.inv_power_overnight is 213927.9 on the fork"
    )


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("low_price", "commercial.export_price_ref.id"),
        ("cheap_battery", "StorageUnit.bess.inv_power_overnight"),
    ],
)
def test_a_new_value_equal_to_the_bases_is_not_evaluated(
    baseline, tmp_path, queue, name, path
):
    """GK B (59) F1/F2: a change whose new value is the base's moved nothing,
    even with the fork's other inputs moved."""
    _, pid, _ = baseline
    task = _task(tmp_path, pid, picked=[name])
    _start(task, pid)
    case = _cases(task)[name]
    _, ctx, record = _fork(task, name)
    new = next(c["new"] for c in case["changes"] if c["path"] == path)
    base = record["shipped"]["base"]
    same = {
        **record,
        "shipped": {
            **record["shipped"],
            "base": {**base, "values": {**base["values"], path: new}},
        },
    }
    assert E.witness(case, ctx, same) == f"{path} is the base's {new} on the fork."


def test_a_contract_number_witness_reads_a_field_the_fingerprint_hashes(baseline):
    ctx, pid, _ = baseline
    with chat_tools._acting() as (db, user):
        ctx = workflow_tools._load_context(
            project_registry.resolve_project(db, user, pid)
        )
    before = E._fingerprint(ctx)
    ctx.solver_state["solver_config"].commercial["contracts"][0]["price"] += 1.0
    assert E._fingerprint(ctx) != before
    cid = ctx.solver_state["solver_config"].commercial["contracts"][0]["id"]
    path = f"commercial.contracts.{cid}.price"
    new = ctx.solver_state["solver_config"].commercial["contracts"][0]["price"]
    case = {"digest": "d", "changes": [{"path": path, "old": new - 1.0, "new": new}]}
    record = {
        "input_fingerprint": E._fingerprint(ctx),
        "baseline_fingerprint": before,
        "shipped": {
            "digest": "d",
            "base": {"values": {path: new - 1.0}, "capital_cost": {}},
        },
    }
    assert E.witness(case, ctx, record) is None
    ctx.solver_state["solver_config"].commercial["contracts"][0]["price"] = new + 5.0
    record["input_fingerprint"] = E._fingerprint(ctx)
    assert "on the fork, not" in E.witness(case, ctx, record)


def test_a_contract_that_is_not_dispatch_bound_classifies_finance(baseline):
    ctx, pid, _ = baseline
    with chat_tools._acting() as (db, user):
        ctx = workflow_tools._load_context(
            project_registry.resolve_project(db, user, pid)
        )
    cfg = E._cfg(ctx)
    cid = cfg["commercial"]["contracts"][0]["id"]
    assert run_shipped._dispatch_contract_ids(cfg) == set()
    assert (
        run_shipped.classify({"path": f"commercial.contracts.{cid}.price"}, cfg)
        == run_shipped.FINANCE
    )


def test_advance_judges_each_ended_case_and_settles_its_solve(
    baseline,
    tmp_path,
    queue,
    monkeypatch,
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    _start(path, pid)
    jobs = {"job-1": "completed", "job-2": "failed"}
    monkeypatch.setattr(E, "_job", lambda case: {"status": jobs[case["job"]["job_id"]]})
    monkeypatch.setattr(
        run_explore,
        "case_gate",
        lambda task, case, profile: {"passed": True, "rules": []},
    )
    assert run_explore.advance(path, boot=INSTANCE, clock=time.time)
    cases = _cases(path)
    assert cases["low_price"]["status"] == "passed"
    assert cases["cheap_battery"]["status"] == "failed"
    assert "ended failed" in cases["cheap_battery"]["reason"]
    assert [r["solves"] for r in _audit(path)] == [1, 1]
    assert run_explore.unsettled(_read(path)["run"]) == 0


def test_a_case_without_its_witness_is_never_passed(
    baseline, tmp_path, queue, monkeypatch
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    _start(path, pid)
    monkeypatch.setattr(E, "_job", lambda case: {"status": "completed"})
    monkeypatch.setattr(E, "witness", lambda case, ctx, record: "no witness")
    monkeypatch.setattr(
        run_explore, "case_gate", lambda *a: pytest.fail("gated without a witness")
    )
    run_explore.advance(path, boot=INSTANCE, clock=time.time)
    case = _cases(path)["low_price"]
    assert case["status"] == "not_evaluated" and case["reason"] == "no witness"


@pytest.mark.parametrize("which", ["no_hit", "not_running", "other_engine"])
def test_advance_touches_only_running_shipped_cases_it_has_a_verdict_for(
    baseline, tmp_path, queue, monkeypatch, which
):
    """`judged` reads outside the run's lock, so `advance` re-checks each
    case's engine and status under it before it writes a verdict."""
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    _start(path, pid)
    verdict = ("passed", "", None, 1)
    if which == "no_hit":
        hits = {}
    elif which == "not_running":
        hits = {"ppa_price_minus_10": verdict}
    else:
        data = _read(path)
        data["run"]["cases"].append(
            {"name": "net", "engine": "network", "status": "running", "solves": {}}
        )
        path.write_text(json.dumps(data))
        hits = {"net": verdict}
    before, open_ = _cases(path), run_explore.unsettled(_read(path)["run"])
    monkeypatch.setattr(E, "judged", lambda task, profile: hits)
    run_explore.advance(path, boot=INSTANCE, clock=time.time)
    after = _cases(path)
    for name in hits or ["low_price"]:
        assert after[name]["status"] == before[name]["status"], name
    assert after["low_price"]["status"] == "running"
    assert run_explore.unsettled(_read(path)["run"]) == open_


# ── D2: the validator's refusal reads not_run, shown and counted ─────────────


def test_a_case_the_validator_refuses_is_not_run_with_its_reason(
    baseline, tmp_path, queue
):
    _, pid, root = baseline
    path = Path(root) / "cases.json"
    data = json.loads(path.read_text())
    share = next(c for c in data["cases"] if c["id"] == "merchant_heavy")
    share["changes"][0]["new"] = 1.5  # a share above 1
    path.write_text(json.dumps(data))
    task = _task(tmp_path, pid, picked=["low_price"])
    frozen = _read(task)["steps"][0]["frozen"]
    (case,) = [c for c in frozen["cases"] if c["id"] == "merchant_heavy"]
    assert case["status"] == "not_run" and "does not validate" in case["why"]
    assert frozen["total"] == 8
    _start(task, pid)
    assert _cases(task)["merchant_heavy"]["status"] == "not_run"


def test_a_validator_refusal_at_the_write_is_not_run_and_releases(
    baseline, tmp_path, queue, monkeypatch
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    real = run_shipped.apply

    def apply(cfg, changes, resolve, *, check_old=True):
        if check_old:
            raise run_shipped.ShippedError("shipped_cases_invalid", "share above 1")
        return real(cfg, changes, resolve, check_old=check_old)

    monkeypatch.setattr(run_shipped, "apply", apply)
    _start(path, pid)
    case = _cases(path)["low_price"]
    assert case["status"] == "not_run" and "share above 1" in case["reason"]
    assert case["project_id"] in _children(pid)
    assert case["solves"]["outcome"] == "released" and queue == []


# ── GK B (60): each refusal this engine adds, tripped alone ─────────────────


def _admitted(path):
    task = _read(path)
    task["steps"][0].update(status="running", attempt_id="a1")
    path.write_text(json.dumps(task))
    return E._admit(path, "explore.1", task["steps"][0]["frozen"], ("a1", INSTANCE))


def test_a_dirty_base_refuses_with_its_kind_before_any_fork(
    baseline, tmp_path, queue, monkeypatch
):
    from services import dirty_state

    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["low_price"])
    with chat_tools._acting() as (db, user):
        base = project_registry.resolve_project(db, user, pid)
    resident = PyPSAService.get_context(project_registry.registry_key(base))
    assert resident is not None
    dirty_state.mark_dirty(resident)
    monkeypatch.setattr(shipped_fork, "create", lambda *a, **k: pytest.fail("forked"))
    _start(path, pid)
    case = _cases(path)["low_price"]
    assert case["status"] == "failed"
    assert case["reason"].startswith("require_current: shipped_base_unsaved:")
    assert queue == [] and _children(pid) == {}


def test_a_battery_cost_that_does_not_derive_is_not_run_with_its_kind(
    baseline, tmp_path, queue, monkeypatch
):
    from fastapi import HTTPException

    from services import network_crud

    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["cheap_battery"])

    def refuse(*a, **k):
        raise HTTPException(422, "no derive")

    monkeypatch.setattr(network_crud, "_check_investment_parts", refuse)
    _start(path, pid)
    case = _cases(path)["cheap_battery"]
    assert case["status"] == "not_run"
    assert case["reason"].startswith("edit: shipped_cases_invalid:")
    assert "does not derive" in case["reason"]
    assert case["solves"]["outcome"] == "released" and queue == []


def test_a_battery_value_moved_on_the_fork_is_stale_at_the_edit(
    baseline, tmp_path, queue, monkeypatch
):
    """The fork-side `old` check is the only layer that sees the fork's own
    battery column: the base is current, so both `require_current` calls pass."""
    import contextlib

    _, pid, _ = baseline
    path = _task(tmp_path, pid, picked=["cheap_battery"])
    real = shipped_fork.held

    @contextlib.contextmanager
    def held(child, task_id):
        with real(child, task_id) as ctx:
            ctx.network.storage_units.at["bess", "inv_power_overnight"] += 1.0
            yield ctx

    monkeypatch.setattr(shipped_fork, "held", held)
    _start(path, pid)
    case = _cases(path)["cheap_battery"]
    assert case["status"] == "failed"
    assert case["reason"].startswith("edit: shipped_case_stale:")
    assert case["solves"]["outcome"] == "released" and queue == []


def test_a_series_the_template_does_not_hold_resolves_nothing(baseline):
    from services.library import series_store

    _, pid, _ = baseline
    org = _org(pid)
    with chat_tools._acting() as (db, _):
        assert E.resolver(db, org, TPL)("no_such_series") is None
        assert series_store.latest_ref(db, org, "no_such_series") is None


@pytest.mark.parametrize(
    "profile",
    [None, {}, {"templates": []}, {"templates": ["a", "b"]}],
)
def test_a_profile_without_exactly_one_template_is_not_runnable(profile):
    with pytest.raises(run_shipped.ShippedError) as err:
        E._template(profile)
    assert err.value.kind == "shipped_case_not_runnable"
    assert E._template({"templates": ["eh_ipp_hybrid"]}) == "eh_ipp_hybrid"


def test_cases_are_admitted_once_per_run(baseline, tmp_path, queue):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    assert _admitted(path) == LP_IDS
    rows = len(_read(path)["run"]["cases"])
    task = _read(path)
    assert (
        E._admit(path, "explore.1", task["steps"][0]["frozen"], ("a1", INSTANCE))
        is None
    )
    assert len(_read(path)["run"]["cases"]) == rows
    # the second admission's reservations are released, the first's stand
    assert run_explore.unsettled(_read(path)["run"]) == len(LP_IDS)
    assert [a.get("solves") for a in _audit(path)].count(0) == len(LP_IDS)


def test_an_admission_by_a_runner_that_lost_the_step_admits_nothing(
    baseline, tmp_path, queue
):
    _, pid, _ = baseline
    path = _task(tmp_path, pid)
    task = _read(path)
    task["steps"][0].update(status="running", attempt_id="a1")
    path.write_text(json.dumps(task))
    frozen = task["steps"][0]["frozen"]
    assert E._admit(path, "explore.1", frozen, ("other", INSTANCE)) is None
    assert E.dispatch(path, task["steps"][0], frozen, "other", INSTANCE) == (
        "taken_over"
    )
    assert _read(path)["run"]["cases"] == [] and queue == []
    assert all(a.get("solves") == 0 for a in _audit(path))


# ── D4: a template series edited after approval ──────────────────────────────


def test_a_template_series_edited_after_approval_refuses_before_any_fork(
    baseline, tmp_path, queue, monkeypatch
):
    import shutil

    _, pid, _ = baseline
    tpl = tmp_path / "templates"
    shutil.copytree(TPL / "price_series", tpl / "eh_ipp_hybrid" / "price_series")
    monkeypatch.setattr(E, "_TEMPLATES", tpl)
    path = _task(tmp_path, pid)
    victim = next(
        p
        for p in (tpl / "eh_ipp_hybrid" / "price_series").iterdir()
        if p.name != "meta.json"
    )
    victim.write_bytes(victim.read_bytes() + b"\n")
    _start(path, pid)
    for name in LP_IDS:
        case = _cases(path)[name]
        assert (
            case["status"] == "failed" and "shipped_series_unverified" in case["reason"]
        )
        assert "project_id" not in case
    assert _children(pid) == {} and queue == []
