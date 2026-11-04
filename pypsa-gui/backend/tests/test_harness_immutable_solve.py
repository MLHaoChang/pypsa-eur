"""Study harness issue 05, HH-10 (GK B, 3 Nov ruling; C6): an envelope-admitted
queued solve solves the exact input the gate checked.

At enqueue the run's input is captured (network, solver config, the template
sidecars byte for byte, each with its sha256); the job solves that capture on
a private context. A user edit or a sidecar write landing while the job waits
can neither change the solve nor be mistaken for its result.

Real project, real runner, real queue and a real HiGHS solve of the baseline
(one generator at marginal cost 10, one 5 MW load, two hours: objective 100)."""
# ruff: noqa: F811 -- imported pytest fixtures
import io
import json
import os
import pathlib
import shutil
import sys
import threading
import time
import zipfile

import pytest
from harness import runner as harness_runner
from services import solve_capture, solve_queue
from services.pypsa_service import PyPSAService
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline, dispatch, edit, helpers_state, session, wait_until,
)
from tests.test_harness_envelope import envelope_of, granted, profile, refusals, stopped, until  # noqa: F401
from tests.test_harness_envelope_atomic import eh, writer  # noqa: F401
from tests.test_harness_run_skeleton import site  # noqa: F401
from tests.test_harness_runner import detached, frames_of, settle, task_path  # noqa: F401

CAPTURED_OBJECTIVE = 2 * 5.0 * 10.0  # the inputs the gate checked
EDITED_OBJECTIVE = 2 * 5.0 * 50.0  # the user's edit made while the job waits


def ctx():
    return PyPSAService.get_active_context()


@pytest.fixture
def held_queue():
    solve_queue.solve_queue.pause()
    yield solve_queue.solve_queue
    solve_queue.solve_queue.resume()


def _enqueued(eh, site):
    project_id = str(ctx().project_uuid)
    ctx().network.storage_units.at["S1", "capital_cost"] = 1.0  # eh's S1 is extendable: priced, so it solves
    task, path = eh([{"id": "inputs.2", "tool": "solve_queue_enqueue", "args": {"project_id": project_id}}])
    until(harness_runner.Runner("a"), path, task,
          lambda r: r["steps"][1]["status"] not in ("pending", "running"))
    jobs = [j for j in solve_queue.solve_queue.list_jobs() if j["status"] == "queued"]
    assert len(jobs) == 1, (jobs, json.loads(path.read_text())["steps"][1], json.loads(path.read_text())["run"].get("reason"))
    return __import__("uuid").UUID(jobs[0]["id"]), path


def _terminal(job_id, timeout=120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = solve_queue.solve_queue.get_job(job_id)
        if job and job["status"] in ("completed", "failed", "aborted"):
            q = solve_queue.solve_queue.get_log_queue(job_id)
            job["log"] = list(q._history) if q is not None else []
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not settle")


def test_an_edit_and_a_sidecar_write_while_the_job_waits_do_not_reach_the_solve(eh, site, held_queue):
    _, root = site
    job_id, _ = _enqueued(eh, site)
    template_before = (root / "eh_template.json").read_bytes()
    # The barrier: between the gate's pass (the enqueue) and the job's pickup.
    writer(lambda n: n.generators.__setitem__("marginal_cost", 50.0)).join()
    (root / "eh_template.json").write_text(json.dumps({"id": "eh_site", "site_type": "edited"}))
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "completed", (job["error"], job["condition"], [l for l in job["log"] if "ERROR" in str(l)][:3])
    # The solve used the capture, and the evidence names it.
    assert job["objective"] == pytest.approx(CAPTURED_OBJECTIVE)
    capture = job["capture"]
    assert len(capture["identity"]) == 64
    assert capture["files"]["eh_template.json"] == __import__("hashlib").sha256(template_before).hexdigest()
    # The user's edit is neither solved nor overwritten, nor taken for the result.
    assert capture["applied"] is False and capture["reason"] == "inputs_changed"
    assert float(ctx().network.generators.at["G1", "marginal_cost"]) == 50.0
    assert json.loads((root / "eh_template.json").read_text())["site_type"] == "edited"


def test_with_no_edit_while_the_job_waits_the_captured_result_is_applied(eh, site, held_queue):
    job_id, _ = _enqueued(eh, site)
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "completed", (job["error"], job["condition"], [l for l in job["log"] if "ERROR" in str(l)][:3])
    assert job["objective"] == pytest.approx(CAPTURED_OBJECTIVE)
    assert job["capture"]["applied"] is True
    assert EDITED_OBJECTIVE != CAPTURED_OBJECTIVE


def test_the_applied_write_back_keeps_the_projects_settings_and_study_ownership(eh, site, held_queue):
    # The write-back saves the project's own directory, not the capture's: the
    # per-project settings table and the study-fork owner keys survive it.
    _, root = site
    meta_path = pathlib.Path(root) / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.update({"settings": {"kept": True}, "owner_study_id": "study-1"})
    meta_path.write_text(json.dumps(meta))
    job_id, _ = _enqueued(eh, site)
    held_queue.resume()
    job = _terminal(job_id)
    assert job["capture"]["applied"] is True, job["capture"]
    after = json.loads(meta_path.read_text())
    assert after.get("settings") == {"kept": True}, after
    assert after.get("owner_study_id") == "study-1", after


def test_a_sidecar_write_between_the_compare_and_the_save_is_not_applied(eh, site, held_queue, monkeypatch):
    # Sidecar writers hold no context lock: one landing after the compare
    # passed leaves the result not the project's (GK B, 4 Nov).
    from routers import projects
    _, root = site
    real = projects._save_context

    def racing(*args, **kwargs):
        (pathlib.Path(root) / "eh_template.json").write_text(json.dumps({"id": "eh_site", "site_type": "raced"}))
        return real(*args, **kwargs)
    job_id, _ = _enqueued(eh, site)
    monkeypatch.setattr(projects, "_save_context", racing)
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "completed", job
    assert job["capture"]["applied"] is False and job["capture"]["reason"] == "inputs_changed", job["capture"]
    assert json.loads((pathlib.Path(root) / "eh_template.json").read_text())["site_type"] == "raced"


# ── C6a: the capture is the one the job recorded ────────────────────────────

def _settled_after(eh, site, held_queue, change):
    job_id, _ = _enqueued(eh, site)
    record = solve_queue.solve_queue._jobs[job_id].capture
    change(pathlib.Path(record["dir"]))
    held_queue.resume()
    return _terminal(job_id)


@pytest.mark.parametrize("change", [
    lambda d: (d / "network.nc").write_bytes((d / "network.nc").read_bytes() + b"\0"),  # a file
    lambda d: (d / "manifest.json").write_text(json.dumps({**json.loads((d / "manifest.json").read_text()),
                                                           "network_revision": 999})),  # the manifest
    lambda d: shutil.rmtree(d),  # the directory
    lambda d: (d / solve_capture.sidecars()[0]).write_text("{}"),  # a sidecar the capture did not hold
], ids=["file", "manifest", "missing", "planted"])
def test_a_capture_that_is_not_the_jobs_fails_with_nothing_solved(eh, site, held_queue, change):
    job = _settled_after(eh, site, held_queue, change)
    assert job["status"] == "failed" and job["condition"] == solve_capture.MISMATCH, job
    assert job["objective"] is None


def test_another_pickup_failure_is_a_queue_error_not_a_mismatch(eh, site, held_queue, monkeypatch):
    # The catch names CaptureMismatch alone: a failure of its base class
    # (RuntimeError) is no proof the capture differs, so it stays queue_error.
    def broken(_record):
        raise RuntimeError("disk gone")
    job_id, _ = _enqueued(eh, site)
    monkeypatch.setattr(solve_capture, "verify", broken)
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "failed" and job["condition"] == "queue_error", job
    assert job["objective"] is None


def test_a_run_solve_naming_another_project_is_refused_with_nothing_captured(eh, site, held_queue, monkeypatch):
    _, root = site
    real = solve_capture.request

    def other_project():
        request = real()
        if request is None:
            return None
        return {**request, "ctx": type("Other", (), {"project_uuid": __import__("uuid").uuid4()})()}
    monkeypatch.setattr(solve_capture, "request", other_project)
    project_id = str(ctx().project_uuid)
    ctx().network.storage_units.at["S1", "capital_cost"] = 1.0
    task, path = eh([{"id": "inputs.2", "tool": "solve_queue_enqueue", "args": {"project_id": project_id}}])
    record = until(harness_runner.Runner("a"), path, task,
                   lambda r: r["steps"][1]["status"] not in ("pending", "running"))
    assert record["steps"][1]["status"] != "completed", record["steps"][1]
    assert not [j for j in solve_queue.solve_queue.list_jobs() if j["status"] == "queued"]
    captures = pathlib.Path(root) / solve_capture.CAPTURES
    assert not captures.is_dir() or not any(captures.iterdir())


# ── C6b, C6c: the private context is the capture, and only the capture ──────

def test_the_queued_solve_opens_no_file_of_the_project(eh, site, held_queue, monkeypatch):
    """Every file the solve reads comes from the capture: an audit hook on
    `open`, on the dispatcher from pickup to the write-back, sees nothing
    under the project but the capture."""
    _, root = site
    project, opened, solving = pathlib.Path(root).resolve(), [], []
    queue_type = type(solve_queue.solve_queue)
    run, apply = queue_type._run_solve_job, queue_type._apply_capture

    def watched(self, job):  # from pickup (hydrate included) to the write-back
        solving.append(True)
        try:
            return run(self, job)
        finally:
            solving.clear()

    def applying(self, *args):
        solving.clear()  # the write-back compares against the project: its own reads
        return apply(self, *args)
    monkeypatch.setattr(queue_type, "_run_solve_job", watched)
    monkeypatch.setattr(queue_type, "_apply_capture", applying)

    def hook(event, args):
        if event == "open" and solving and isinstance(args[0], (str, os.PathLike)) \
                and threading.current_thread().name.startswith("solve-queue-dispatcher"):
            opened.append(pathlib.Path(os.fsdecode(args[0])))
    sys.addaudithook(hook)
    job_id, _ = _enqueued(eh, site)
    held_queue.resume()
    job = _terminal(job_id)
    solving.clear()
    assert job["status"] == "completed"
    inside = [p for p in opened if p.is_absolute() and p.resolve().is_relative_to(project)]
    captures = project / solve_capture.CAPTURES
    assert inside and all(p.resolve().is_relative_to(captures) for p in inside), inside


def test_the_island_reader_reads_the_captures_sidecar(eh, site, monkeypatch):
    """`load_project_island` reads `<storage_dir>/island_config.json`: on the
    private context that is the capture's copy (C6b)."""
    from routers.projects import _hydrate_context_from_disk
    from services.adequacy import island_config
    from services.solver import island
    _, root = site
    (pathlib.Path(root) / island_config.SIDECAR_NAME).write_text("{}")
    with ctx().mutation_lock, ctx().solver_state_lock:
        record = solve_capture.capture(ctx(), root, None)
    assert island_config.SIDECAR_NAME in record["files"]
    read = []
    monkeypatch.setattr(island_config, "load_island_config", lambda path: read.append(pathlib.Path(path)))
    private = PyPSAService.build_context()
    _hydrate_context_from_disk(private, pathlib.Path(record["dir"]), "Tool Baseline")
    private.storage_dir = record["dir"]
    with PyPSAService.solving_context(private):
        island.load_project_island()
    assert read == [pathlib.Path(record["dir"]) / island_config.SIDECAR_NAME]


def test_the_hydrated_capture_has_the_projects_exact_fingerprint(eh, site):
    """Custom LP columns survive the .nc round trip (C6c)."""
    from routers.projects import _hydrate_context_from_disk
    from services import run_envelope
    _, root = site
    ctx().network.generators["eh_custom_lp"] = 0.25
    with ctx().mutation_lock, ctx().solver_state_lock:
        before = run_envelope.identity(ctx())
        record = solve_capture.capture(ctx(), root, None)
    private = PyPSAService.build_context()
    _hydrate_context_from_disk(private, pathlib.Path(record["dir"]), "Tool Baseline")
    after = run_envelope.identity(private)
    assert record["exact"] == before["exact"] == after["exact"]
    assert record["network_revision"] == before["network_revision"] == after["network_revision"]


# ── C6f: a result not applied is never the project's ────────────────────────

def test_a_capture_not_applied_is_no_evidence_for_the_solve_rules(monkeypatch):
    from services import run_gates
    job = {"status": "completed", "condition": "optimal", "capture": {"applied": False, "reason": "inputs_changed"}}
    monkeypatch.setattr(run_gates, "_solve_job", lambda env: job)
    rules = [run_gates.check_rule({"id": r, "fact": f, "op": "eq", "value": v, "severity": "block", "basis": "05"})
             for r, f, v in (("a", "solve.state", "completed"), ("b", "solve.status", "optimal"))]
    results = run_gates.evaluate(rules, run_gates.Env(task={}, ctx=None, project=None, project_dir=None))
    assert [r["verdict"] for r in results] == ["not_evaluated", "not_evaluated"]
    assert all(r["reason"].startswith("inputs_changed") for r in results)
    job["capture"]["applied"] = True
    results = run_gates.evaluate(rules, run_gates.Env(task={}, ctx=None, project=None, project_dir=None))
    assert [r["verdict"] for r in results] == ["pass", "pass"]
    # Only `applied is True` counts: a captured job with the field missing
    # (or not a bool) is no evidence.
    for capture in ({"identity": "x"}, {"applied": "yes"}):
        job["capture"] = capture
        results = run_gates.evaluate(rules, run_gates.Env(task={}, ctx=None, project=None, project_dir=None))
        assert [r["verdict"] for r in results] == ["not_evaluated", "not_evaluated"], capture


def test_the_run_evidence_names_the_capture_and_whether_it_was_applied(eh, site, held_queue):
    from services import run_evidence
    _, root = site
    job_id, _ = _enqueued(eh, site)
    writer(lambda n: n.generators.__setitem__("marginal_cost", 50.0)).join()
    held_queue.resume()
    job = _terminal(job_id)
    run = run_evidence.queue_run_id(str(job_id))
    summary = json.loads((pathlib.Path(root) / "run_evidence" / run / "sections" / "summary.json").read_text())
    assert summary["capture"]["identity"] == job["capture"]["identity"]
    assert summary["capture"]["applied"] is False and summary["capture"]["reason"] == "inputs_changed"


# ── Enqueue: never merged into another job ──────────────────────────────────

def test_a_run_solve_is_refused_while_another_solve_of_the_project_is_active(eh, site, held_queue):
    _, root = site
    solve_queue.solve_queue.enqueue_unique(ctx().loaded_project, project_key=ctx().registry_key,
                                           storage_dir=str(root))
    project_id = str(ctx().project_uuid)
    ctx().network.storage_units.at["S1", "capital_cost"] = 1.0
    task, path = eh([{"id": "inputs.2", "tool": "solve_queue_enqueue", "args": {"project_id": project_id}}])
    record = until(harness_runner.Runner("a"), path, task,
                   lambda r: r["steps"][1]["status"] not in ("pending", "running"))
    assert record["steps"][1]["status"] != "completed", record["steps"][1]
    # Refused, never merged: the earlier job is the only one, and solves its own input.
    assert [j.get("capture") for j in solve_queue.solve_queue.list_jobs() if j["status"] == "queued"] == [None]
    captures = pathlib.Path(root) / solve_capture.CAPTURES
    assert not captures.is_dir() or not any(captures.iterdir())


def test_a_capture_whose_enqueue_failed_is_removed_at_once(eh, site, held_queue, monkeypatch):
    _, root = site

    def failing(*_a, **_k):
        raise RuntimeError("queue store down")
    monkeypatch.setattr(solve_queue.solve_queue, "enqueue_unique", failing)
    project_id = str(ctx().project_uuid)
    ctx().network.storage_units.at["S1", "capital_cost"] = 1.0
    task, path = eh([{"id": "inputs.2", "tool": "solve_queue_enqueue", "args": {"project_id": project_id}}])
    record = until(harness_runner.Runner("a"), path, task,
                   lambda r: r["steps"][1]["status"] not in ("pending", "running"))
    assert record["steps"][1]["status"] != "completed", record["steps"][1]
    captures = pathlib.Path(root) / solve_capture.CAPTURES
    assert not captures.is_dir() or not any(captures.iterdir())


# ── C6g: retention and exclusions ───────────────────────────────────────────

def test_the_capture_is_removed_once_the_job_settled(eh, site, held_queue):
    job_id, _ = _enqueued(eh, site)
    directory = pathlib.Path(solve_queue.solve_queue._jobs[job_id].capture["dir"])
    assert (directory / solve_capture.MANIFEST).is_file()
    held_queue.resume()
    job = _terminal(job_id)
    assert not directory.exists() and job["capture"]["identity"]


def test_the_boot_sweep_keeps_live_and_young_captures_only(tmp_path):
    base = tmp_path / "org" / "Project" / solve_capture.CAPTURES
    for name in ("live", "orphan", ".young.tmp"):
        (base / name).mkdir(parents=True)
    old = time.time() - 3600
    os.utime(base / "live", (old, old))
    os.utime(base / "orphan", (old, old))
    assert solve_capture.sweep({"live"}, min_age_s=90.0, roots=[tmp_path]) == 1
    assert sorted(p.name for p in base.iterdir()) == [".young.tmp", "live"]


def test_the_sweep_waits_out_the_runners_lease():
    assert solve_capture.SWEEP_MIN_AGE_S == harness_runner.LEASE_SECONDS


def test_a_capture_is_in_no_bundle_and_no_fingerprint(eh, site):
    from routers.projects import _project_bundle_bytes
    from services import run_gates
    _, root = site
    with ctx().mutation_lock, ctx().solver_state_lock:
        before = run_gates.fingerprint(ctx())
        solve_capture.capture(ctx(), root, None)
        assert run_gates.fingerprint(ctx()) == before
    names = zipfile.ZipFile(io.BytesIO(_project_bundle_bytes("Tool Baseline", pathlib.Path(root)))).namelist()
    assert names and not [n for n in names if ".harness" in n]


def test_a_capture_leaves_every_project_and_template_file_byte_identical(eh, site):
    """C6g: capturing writes under `.harness/captures` only; every other file of
    the project, the template sidecars included, stays byte for byte."""
    _, root = site
    root = pathlib.Path(root)

    def files():
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and ".harness" not in p.relative_to(root).parts}
    before = files()
    assert "eh_template.json" in before
    with ctx().mutation_lock, ctx().solver_state_lock:
        record = solve_capture.capture(ctx(), root, None)
    assert pathlib.Path(record["dir"]).is_relative_to(root / solve_capture.CAPTURES)
    assert files() == before


def _undo(root):
    writer(lambda n: n.generators.__setitem__("marginal_cost", 50.0)).join()
    writer(lambda n: n.generators.__setitem__("marginal_cost", 10.0)).join()


@pytest.mark.parametrize("change", [
    lambda root: (pathlib.Path(root) / "eh_template.json").write_text('{"id": "eh_site", "site_type": "x"}'),
    _undo,  # the same inputs, a later edit counter
], ids=["sidecar_only", "edit_then_undo"])
def test_the_write_back_compares_the_full_identity_not_the_fingerprint_alone(eh, site, held_queue, change):
    """C6e: a sidecar write or an edit and its undo while the job waits
    leaves the fingerprint as it was, and still the result is not applied."""
    _, root = site
    job_id, _ = _enqueued(eh, site)
    change(root)
    held_queue.resume()
    job = _terminal(job_id)
    assert job["status"] == "completed" and job["objective"] == pytest.approx(CAPTURED_OBJECTIVE)
    assert job["capture"]["applied"] is False and job["capture"]["reason"] == "inputs_changed"


def test_a_captured_sidecar_gone_from_the_capture_is_a_mismatch_never_the_projects(eh, site, held_queue):
    """GK B rider 1: the loaders never fall back to the project's file."""
    from services.adequacy import island_config
    _, root = site
    (pathlib.Path(root) / island_config.SIDECAR_NAME).write_text("{}")
    job = _settled_after(eh, site, held_queue, lambda d: (d / island_config.SIDECAR_NAME).unlink())
    assert job["status"] == "failed" and job["condition"] == solve_capture.MISMATCH, job


@pytest.mark.parametrize("code,expected", [("", None), ("def extra_functionality(n, s):\n    pass\n",
                                                        ["user_code_reads"])])
def test_user_code_reads_are_named_as_not_captured(eh, site, code, expected):
    """GK B rider 2: the code travels in the captured config; its reads do not."""
    _, root = site
    config = json.dumps({"extra_functionality_code": code})
    with ctx().mutation_lock, ctx().solver_state_lock:
        record = solve_capture.capture(ctx(), root, config)
    assert record.get("not_captured") == expected
    assert json.loads((pathlib.Path(record["dir"]) / "solver_config.json").read_text()) == json.loads(config)


def test_no_project_copy_or_listing_names_the_capture_directory():
    """C6g: bundle export, Save-As and the sidecar listings copy named files
    and directories only; `.harness` is never one of them."""
    from routers import projects
    named = [*projects._BUNDLE_FILES, *projects._BUNDLE_DIRS, *projects._HISTORY_DIRS,
             *getattr(projects, "_BUNDLE_CAMPUS_FILES", ())]
    assert not [n for n in named if str(n).startswith(".harness")]
    assert not [n for n in solve_capture.solve_inputs() if n.startswith(".harness")]


def test_a_failed_captured_solve_reads_as_failed_not_as_not_applied(monkeypatch):
    from services import run_gates
    job = {"status": "failed", "condition": "infeasible", "capture": {"applied": False, "reason": "not_completed"}}
    monkeypatch.setattr(run_gates, "_solve_job", lambda env: job)
    assert run_gates._solve_state(None) == "failed"
