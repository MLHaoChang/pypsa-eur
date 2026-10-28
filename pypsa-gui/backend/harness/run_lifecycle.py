"""LC-19: re-run and failure rules for a solve started from chat.

**Re-run (item 2).** A re-run of unchanged inputs is asked for, never assumed.

A solve tool aimed at the open project whose results already describe its
current inputs (`calculations.solve_matches_inputs`, the guard every result
read uses) is refused BEFORE its card, for the model only, with
`results_current`: the model says so and asks. The user's yes comes back as
the same call with `rerun_current: true`, which then shows the ordinary run
card. Nothing here starts, approves or retries a job.

Only the open project is checked: its live network is the one the fingerprint
describes. A queued project solves its saved file, so the open project counts
only while its saved inputs are the solved ones; any other project is left to
its card.

**Failure (item 3).** A failed solve's result names its triage: the job's log,
then `diagnose_network` and `validate_network`; the cause in one sentence with
its evidence; the fix as an ordinary edit card. Nothing re-runs by itself.
"""
from __future__ import annotations

from typing import Any

# The tools that solve the optimisation again, and how each names its project.
RERUN_TOOLS = frozenset(["run_simulation", "solve_queue_enqueue", "solve_queue_requeue"])
# The load flow re-runs on the open project only; its results are current while
# the project flow's `freshness_by_kind.loadflow` reads fresh.
LOADFLOW_TOOL = "run_ac_pf_stage"

FLAG = "rerun_current"


def _targets_open_project(tool_name: str, args: dict[str, Any], ctx) -> bool:
    if tool_name == "run_simulation":
        return True
    if tool_name == "solve_queue_enqueue":
        return str(args.get("project_id") or "") in {str(ctx.project_uuid), str(ctx.loaded_project)}
    import uuid

    from services.solve_queue import solve_queue
    try:
        job = solve_queue.get_job(uuid.UUID(str(args.get("job_id") or "")))
    except ValueError:
        return False
    return job is not None and job.get("project_key") == ctx.registry_key


def _saved_matches_solve(ctx) -> bool:
    """A queued job solves the SAVED file: it re-solves the current results only
    when the saved inputs are the ones the open project's last solve used."""
    from services import chat_tools, project_registry
    from services.workflow_tools import _load_context, _network_input_fingerprint
    with chat_tools._acting() as (db, user):
        project = project_registry.resolve_project(db, user, str(ctx.project_uuid))
    saved = _load_context(project)
    return _network_input_fingerprint(saved) == ctx.network.meta.get("assistant_solve_input_fingerprint")


def _latest_completed_run() -> dict | None:
    from services import run_evidence
    try:
        items = run_evidence.list_project_runs(limit=1, status="completed").get("items") or []
    except Exception:  # noqa: BLE001 — the run's name is a courtesy, never the check
        return None
    return {k: items[0].get(k) for k in ("run_id", "finished_at")} if items else None


def check_rerun(tool_name: str, args: dict[str, Any]) -> dict | None:
    """The refusal detail for a re-solve of current results, or None."""
    if args.get(FLAG) is True:
        return None
    if tool_name == LOADFLOW_TOOL:
        return _check_loadflow_rerun()
    if tool_name not in RERUN_TOOLS:
        return None
    from harness.calculations import solve_matches_inputs
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid is None or not _targets_open_project(tool_name, args, ctx):
        return None
    with ctx.solver_state_lock:
        current = solve_matches_inputs(ctx)
    if not current or (tool_name != "run_simulation" and not _saved_matches_solve(ctx)):
        return None
    run = _latest_completed_run()
    named = f" (run {run['run_id']}, finished {run['finished_at']})" if run else ""
    return {
        "error_kind": "results_current",
        "message": (f"The results are current: the inputs have not changed since the last solve{named}. "
                    "Solving again gives the same answer. Tell the user and ask whether to solve again "
                    f"anyway; only on their yes call {tool_name} again with {FLAG}: true."),
        "latest_run": run,
    }


def loadflow_current(ctx) -> bool:
    """The load flow's results still describe the open network: the same word
    the project flow's results row shows (`freshness_by_kind.loadflow`)."""
    from services.dispatch_status import dispatch_status
    from services.flow_service import _loadflow_freshness
    with ctx.solver_state_lock:
        return _loadflow_freshness(dispatch_status(ctx.network), running=False) == "fresh"


def _check_loadflow_rerun() -> dict | None:
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    if ctx.project_uuid is None or not loadflow_current(ctx):
        return None
    return {
        "error_kind": "results_current",
        "message": ("The load flow results are current: the network and the dispatch it read have not "
                    "changed since it ran. Running it again gives the same answer. Tell the user and ask "
                    f"whether to run it again anyway; only on their yes call {LOADFLOW_TOOL} again with {FLAG}: true."),
        "latest_run": None,
    }


def gate(tool_name: str, args: dict[str, Any]) -> tuple[dict | None, dict[str, Any]]:
    """The refusal for a re-solve of current results, and the args the card shows.
    The flag stays only while the results really are current: the card words it
    as "the results already match", so a flag on changed inputs is dropped."""
    if args.get(FLAG) is not True:
        return check_rerun(tool_name, args), args
    plain = {k: v for k, v in args.items() if k != FLAG}
    if tool_name != LOADFLOW_TOOL and tool_name not in RERUN_TOOLS:
        return None, args
    return None, (args if check_rerun(tool_name, plain) else plain)


# A status that ends a solve without results and was not the user's own stop.
FAILED = frozenset(["failed", "interrupted"])


def triage(log_tool: str, log_args: dict[str, Any] | None = None) -> dict:
    """What a failed solve's reader does next: reads first, then words, never a run."""
    return {
        "read": [{"tool": log_tool, "args": log_args or {}},
                 {"tool": "diagnose_network", "args": {}}, {"tool": "validate_network", "args": {}}],
        "then": ("State the cause in one sentence with the evidence that shows it (the log line, "
                 "the island or the validation code). Offer the fix as an ordinary edit card. "
                 "Never run it again on your own: a new run needs the user's request and its own card."),
    }


def with_triage(result: dict, job_id: str) -> dict:
    """`wait_for_job`'s result, plus the triage when the solve failed."""
    job = result.get("job") if isinstance(result, dict) else None
    if not isinstance(job, dict) or job.get("status") not in FAILED:
        return result
    from harness import job_adapters
    if job_adapters.is_foreground_job_id(job_id):
        return {**result, "triage": triage("get_simulation_log_history")}
    return {**result, "triage": triage("solve_queue_job_log", {"job_id": job_id})}


def continuation_note(outcome: str) -> str:
    """The failed continuation's one-line rule; it also offers the turn these tools."""
    if outcome != "failed":
        return ""
    return ("; triage: read the job log, diagnose_network, validate_network; name the cause "
            "with its evidence; offer the fix as an edit card; do not run it again")
