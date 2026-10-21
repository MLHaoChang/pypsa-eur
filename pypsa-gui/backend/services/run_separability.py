"""
The separability matrix (workspace-reconciliation §11.1, slice W0).

One row per run kind or study the workspace could offer: is it **supported**
(runs on its own), **bundled** (runs only with something else it cannot be
split from), **unsupported** or **unknown**. Each row records the entry point,
inputs and outputs, locks and shared state, the implicit sub-steps and the
freshness boundary, and names the native tests that prove it
(``tests/test_run_separability.py`` checks every named test exists). The
human-readable copy is ``.scratch/workspace-redesign/backend-separability-assessment.md``.

The gate: no kind enters ``run_request`` and no card sub-option becomes
selectable before its row says ``runnable_in_run``. Unknown and unsupported
rows never do.

``verdict(kind, …)`` answers "would this kind start now?" for every runnable
row. It never waits on the network lock: each verdict is the server's own
check, reached through the preflight's three gates (``preflight_validate``:
no solver worker, no blocking study, the lock taken with ``blocking=False``),
so a busy network answers ``deferred``, never a stall. The verdicts are the
start code itself, not copies of it:

* ``optimisation``: ``preflight_validate`` → ``validate_for_run``, the check
  ``run_simulation`` repeats under the lock at Run start;
* ``study``: ``check_eh_study_start(lock_blocking=False)``, the refusal
  ``start_eh_study`` raises, on the body the Guided cards send
  (``flow_service._study_start_items``);
* ``loadflow``: ``ac_pf_refusal``, the refusal ``/run_ac_pf`` raises, read in
  the preflight's lock span. Bundled: in a Run that also optimises it is
  Stage 2 of that solve (``run_ac_pf_after_lopf``), so its verdict is the
  optimisation's.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Literal

Classification = Literal["supported", "bundled", "unsupported", "unknown"]


@dataclass(frozen=True)
class Row:
    kind: str
    label: str
    classification: Classification
    runnable_in_run: bool
    entry: str
    inputs: str
    outputs: str
    locks_and_state: str
    implicit_steps: str
    freshness: str
    evidence: tuple[str, ...]
    note: str = ""


_T = "tests/test_run_separability.py::"

MATRIX: dict[str, Row] = {r.kind: r for r in (
    Row("optimisation", "Capacity expansion (sizing, dispatch, economics)", "supported", True,
        entry="POST /api/simulation/run → solver_service.run_simulation",
        inputs="the open network and its solver_config; no prior solve",
        outputs="*_t dispatch, *_nom_opt sizes, objective; _state results",
        locks_and_state="the network lock for validate + solve; claims _state under the solver-state "
                        "lock; refuses while a study runs (study mesh)",
        implicit_steps="process_uc.binary_check on a private copy after a linearised plan with a "
                       "committable eh_process unit (never changes the plan, never fails the run; "
                       "skipped when _ic_operational is set); AC power flow Stage 2 when "
                       "run_ac_pf_after_lopf",
        freshness="dispatch_status (topology shape); dirty_state.revision",
        evidence=(_T + "test_the_optimisation_verdict_is_the_preflights",
                  _T + "test_verdicts_defer_instead_of_waiting_on_a_held_lock",
                  _T + "test_the_binary_check_is_skipped_on_an_operational_solve",
                  "tests/test_process_uc_solve_path.py::"
                  "test_a_failing_binary_check_leaves_the_run_ok_and_the_plan_unchanged",
                  "tests/test_preflight_toctou.py::test_preflight_does_not_block_on_a_held_lock")),
    Row("study", "Reliability study (EH hub design)", "supported", True,
        entry="POST /api/results/eh_study → eh_study_runner.start_eh_study",
        inputs="an EH template's study body (archetype, pack overrides, stages); solver_config",
        outputs="_state['eh_study'] report; sibling stores; a run-evidence manifest",
        locks_and_state="check_eh_study_start takes the network lock briefly; the worker solves a "
                        "private copy; clears the sibling stores at start; study mesh",
        implicit_steps="apply_pack and ens_solve are mandatory; ens_solve is a full run_simulation "
                       "(validate_for_run included) on the copy",
        freshness="study_state.edited_since (network revision at start)",
        evidence=(_T + "test_the_study_verdict_is_the_start_refusal",
                  _T + "test_verdicts_defer_instead_of_waiting_on_a_held_lock",
                  _T + "test_a_study_cannot_drop_its_mandatory_stages",
                  "tests/test_eh_study_edited_since.py::"
                  "test_a_finished_study_carries_the_revision_and_reads_unedited")),
    Row("loadflow", "AC power flow", "bundled", True,
        entry="POST /api/simulation/run_ac_pf → ac_pf_service.run_ac_pf_stage; or Stage 2 of /run",
        inputs="a solved network with fresh dispatch (the LP's operating points)",
        outputs="lines_t.p0/p1/q0/q1 and buses_t.v_mag_pu/v_ang overwritten in place; "
                "_state lopf_results / ac_pf_results",
        locks_and_state="the network lock for the whole PF; not abortable",
        implicit_steps="none; it needs a dispatch, so it is bundled with the optimisation",
        freshness="dispatch_status must be 'fresh'; stale once the optimisation reruns",
        evidence=(_T + "test_loadflow_needs_a_fresh_dispatch",
                  _T + "test_loadflow_in_a_run_that_optimises_is_the_optimisations_verdict",
                  _T + "test_verdicts_defer_instead_of_waiting_on_a_held_lock",
                  _T + "test_the_load_flow_overwrites_the_lp_flows")),
    Row("sclopf", "N-1 security (SCLOPF)", "supported", True,
        entry="an option of optimisation (SolverConfig.sclopf → optimize_security_constrained)",
        inputs="lines/transformers in the contingency set; LOPF mode",
        outputs="as optimisation",
        locks_and_state="as optimisation",
        implicit_steps="none beyond optimisation",
        freshness="as optimisation",
        evidence=(_T + "test_sclopf_is_refused_where_the_engine_cannot_run_it",),
        note="no run kind of its own: its verdict is the optimisation's. Refused with "
             "transmission losses, with the rolling strategy and without branches."),
    Row("grid_scorecard", "Grid-readiness scorecard", "unsupported", False,
        entry="an advisory read (#435), shown in section 9",
        inputs="the network", outputs="advisory verdicts",
        locks_and_state="read-only", implicit_steps="none", freshness="read live",
        evidence=(), note="not a run kind; outside run_ready"),
    Row("campus_electrical", "Campus electrical design (PCC, transformers, compensation)",
        "bundled", False,
        entry="POST /api/campus-electrical/{name}/run (solve-queue job) and /milp",
        inputs="a capacity-expansion project's SAVED, solved network.nc; campus_input.yaml",
        outputs="campus_electrical/run/ (sizing, compliance, purchases)",
        locks_and_state="the NetCDF I/O lock and its own run-dir lock; never the network lock; "
                        "reads the saved file, not the open network",
        implicit_steps="gridspine's prepare, rank, island checks, frequency cuts, sizing and "
                       "library purchase run as one job",
        freshness="_stale: the network.nc sha256, the campus file, the library",
        evidence=(_T + "test_campus_run_reads_the_saved_file_without_the_network_lock_current_behaviour",
                  "tests/test_campus_electrical_service.py::test_a_project_never_saved_cannot_be_drafted"),
        note="not in the one Run until a save step and its own verdict exist"),
    Row("control_checks", "Island & ride-through study", "bundled", False,
        entry="POST /api/campus-electrical/{name}/island/study → solver.island.island_study",
        inputs="the SAVED network.nc and island_config.json",
        outputs="the island study sidecar",
        locks_and_state="runs inline in the request; no network lock, no study mesh",
        implicit_steps="its own HiGHS LOPF per scenario that RE-SIZES extendable assets, "
                       "ignoring the project's solver_config and the capacity plan",
        freshness="dropped when island_config.json changes",
        evidence=(_T + "test_the_island_study_resizes_on_its_own_solve_current_behaviour",
                  "tests/test_campus_island_service.py::test_a_study_without_a_sidecar_is_422"),
        note="runnable once #448 §3.1's fixed-size island mode reads the plan's sizes"),
    Row("contingencies", "Contingencies beyond N-1; connection capacity", "unsupported", False,
        entry="routers/gridspine.py (/run, /capacity, /connection)",
        inputs="a planning_dynamics project with a screened run",
        outputs="the gridspine run directory", locks_and_state="files only; per-run-dir locks",
        implicit_steps="AC load flow and an N-1 batch per bisection step",
        freshness="the screened run",
        evidence=(_T + "test_connection_capacity_belongs_to_another_project_kind",),
        note="another project kind; not part of a capacity project's Run"),
    Row("dynamics", "Frequency & time domain", "unsupported", False,
        entry="Planning → dynamics hand-off bundles; PowerFactory read-back",
        inputs="a planning_dynamics project", outputs="an external simulator's results",
        locks_and_state="files only", implicit_steps="none in-app",
        freshness="the read-back",
        evidence=(), note="no in-app time-domain simulation; the island cuts are screening"),
    Row("process_detail", "Detailed process (beyond unit commitment)", "unsupported", False,
        entry="none; services/solver/process_uc.py covers eh_process commitment only",
        inputs="-", outputs="-", locks_and_state="-", implicit_steps="-", freshness="-",
        evidence=(), note="after GTP"),
    Row("fixed_sizes", "Dispatch on fixed sizes (sizing off)", "unsupported", False,
        entry="none yet: slice F1 (a solver-config option over sweep.freeze_capacities)",
        inputs="-", outputs="-", locks_and_state="-", implicit_steps="-", freshness="-",
        evidence=(), note="F1"),
)}


def runnable(kind: str) -> bool:
    row = MATRIX.get(kind)
    return row is not None and row.runnable_in_run


def _deferred(payload: dict) -> dict:
    return {"ready": False, "deferred": True,
            "blocker": {"code": "deferred",
                        "message": payload.get("deferred_reason") or "validation deferred"}}


def _ready() -> dict:
    return {"ready": True, "deferred": False, "blocker": None}


def _blocked(code: str, message: str) -> dict:
    return {"ready": False, "deferred": False, "blocker": {"code": code, "message": message}}


def optimisation_verdict() -> dict:
    from routers.simulation import preflight_validate

    deferred, issues, _ = preflight_validate()
    if deferred is not None:
        return _deferred(deferred)
    error = next((i for i in issues if i.severity == "error"), None)
    return _ready() if error is None else _blocked(error.code, error.message)


def study_verdict(project_dir: pathlib.Path) -> dict:
    from routers.simulation import preflight_validate
    from services import flow_service
    from services.adequacy.eh_study_runner import EhStudyLockBusy

    template = flow_service.read_template(project_dir)
    if template is None:
        return _blocked("no_study_request",
                        "The project has no reliability study to run (no EH template).")
    deferred, _issues, _ = preflight_validate(validate=False)
    if deferred is not None:
        return _deferred(deferred)
    try:
        items = flow_service._study_start_items(flow_service._request_for(template))
    except EhStudyLockBusy:
        return _deferred({"deferred_reason": flow_service._LOCK_BUSY_REASON})
    return _ready() if not items else _blocked(items[0]["code"], items[0]["message"])


def loadflow_verdict(*, with_optimisation: bool) -> dict:
    if with_optimisation:
        return optimisation_verdict()
    from routers.simulation import ac_pf_refusal, preflight_validate

    deferred, _issues, refusal = preflight_validate(lambda n, _cfg: ac_pf_refusal(n),
                                                    validate=False)
    if deferred is not None:
        return _deferred(deferred)
    return _ready() if refusal is None else _blocked("loadflow_needs_dispatch", refusal)


def verdict(kind: str, *, project_dir: pathlib.Path | None = None,
            with_optimisation: bool = False) -> dict:
    """``{ready, deferred, blocker}`` for ``kind`` on the open project. A row
    that is not runnable answers ``ready: false`` with its classification."""
    if kind in ("optimisation", "sclopf"):
        return optimisation_verdict()
    if kind == "study":
        if project_dir is None:
            raise ValueError("the study verdict reads the project's template")
        return study_verdict(project_dir)
    if kind == "loadflow":
        return loadflow_verdict(with_optimisation=with_optimisation)
    row = MATRIX.get(kind)
    cls = row.classification if row is not None else "unknown"
    return _blocked(f"kind_{cls}", f"{row.label if row else kind} is not runnable ({cls})"
                    + (f": {row.note}" if row is not None and row.note else ""))
