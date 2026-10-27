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

``verdicts(project_dir, kinds, …)`` answers "would this kind start now?" for
every runnable row. It never waits on the network lock: each verdict is the
server's own start check, reached through the preflight's three gates
(``preflight_validate``: no solver worker, no blocking study, the lock taken
with ``blocking=False``), so a busy network answers ``deferred``, never a
stall. The verdicts are that start code, not copies of it:

* ``optimisation`` and ``study``: the project flow's own verdicts (B1, R1):
  ``validate_for_run`` through the preflight (the check ``run_simulation``
  repeats under the lock at Run start), and ``check_eh_study_start(
  lock_blocking=False)``, the refusal ``start_eh_study`` raises;
* ``loadflow``: ``routers.simulation.ac_pf_refusal``, the refusal
  ``/run_ac_pf`` raises, read in the preflight's lock span. Bundled: in a Run
  that also optimises it is Stage 2 of that solve (``run_ac_pf_after_lopf``),
  so its verdict is the optimisation's with that option on;
* an option (``sclopf``, Stage 2) answers for switching it ON, whatever the
  current config says: its refusals in ``validate_for_run`` fire only then.

Classes. **bundled** rows say with ``runnable_in_run`` whether their bundle can
be part of the one Run (the load flow's dispatch is; the campus study's saved
file and the island study's own re-sizing solve are not, yet). The grid
readiness scorecard is not a run kind and has no row (an advisory read, #435).
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
                  _T + "test_the_binary_check_runs_after_a_plan_and_not_on_an_operational_solve",
                  "tests/test_process_uc_solve_path.py::"
                  "test_a_failing_binary_check_leaves_the_run_ok_and_the_plan_unchanged",
                  "tests/test_preflight_toctou.py::test_preflight_does_not_block_on_a_held_lock")),
    Row("study", "Reliability study (EH hub design)", "supported", True,
        entry="POST /api/results/eh_study → eh_study_runner.start_eh_study",
        inputs="an EH template's study body (archetype, pack overrides, stages); solver_config",
        outputs="_state['eh_study'] report; sibling stores; a run-evidence manifest",
        locks_and_state="check_eh_study_start takes the network lock briefly; the worker solves a "
                        "private copy; clears the sibling stores at start; study mesh",
        implicit_steps="apply_pack and ens_solve are mandatory (validate_stages); ens_solve is a "
                       "full run_simulation, validate_for_run included, on the copy "
                       "(eh_study._stage_ens_solve)",
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
        freshness="dispatch_status must be 'fresh' to start (the topology shape: a parameter "
                  "edit after the solve still reads fresh); its results read stale once the "
                  "edit counter moved past the ac_pf_network_revision the PF recorded (W4b)",
        evidence=(_T + "test_loadflow_needs_a_fresh_dispatch",
                  _T + "test_loadflow_in_a_run_that_optimises_is_the_optimisations_verdict",
                  _T + "test_loadflow_switched_on_is_the_runs_own_verdict",
                  "tests/test_project_flow.py::test_load_flow_freshness_follows_its_inputs",
                  _T + "test_the_bundled_load_flow_answers_for_stage_two_on",
                  _T + "test_verdicts_defer_instead_of_waiting_on_a_held_lock",
                  _T + "test_the_load_flow_overwrites_the_lp_flows")),
    Row("sclopf", "N-1 security (SCLOPF)", "supported", True,
        entry="an option of optimisation (SolverConfig.sclopf → optimize_security_constrained)",
        inputs="lines/transformers in the contingency set; LOPF mode",
        outputs="as optimisation",
        locks_and_state="as optimisation",
        implicit_steps="none beyond optimisation",
        freshness="as optimisation",
        evidence=(_T + "test_sclopf_is_refused_where_the_engine_cannot_run_it",
                  _T + "test_an_option_switched_off_answers_for_switching_it_on"),
        note="no run kind of its own: its verdict is the optimisation's with sclopf on. "
             "Refused with transmission losses, with the rolling strategy and without "
             "branches."),
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
        evidence=(_T + "test_the_dynamics_hand_off_belongs_to_another_project_kind",),
        note="no in-app time-domain simulation; the island cuts are screening"),
    Row("process_detail", "Detailed process (beyond unit commitment)", "unsupported", False,
        entry="none; services/solver/process_uc.py covers eh_process commitment only",
        inputs="-", outputs="-", locks_and_state="-", implicit_steps="-", freshness="-",
        evidence=(_T + "test_process_modelling_is_unit_commitment_only",), note="after GTP"),
    Row("fixed_sizes", "Dispatch on fixed sizes (sizing off)", "supported", True,
        entry="an option of optimisation: SolverConfig.fixed_sizes = capacity_run | nameplate "
              "(services/solver/fixed_sizes.py, F1)",
        inputs="the capacity run's recorded sizes (n.meta['sizing_record']) or the nameplate",
        outputs="as optimisation; *_nom_opt = the fixed sizes; _state['sizing']",
        locks_and_state="as optimisation; freeze_capacities(transient=False) clamps the bounds "
                        "for the solve and undoes them on every exit; sets _fixed_design",
        implicit_steps="as optimisation (the process UC check and the island UC run); the "
                       "connection is pinned at the fixed size",
        freshness="the sizing record (its topology hash and the 1e-4 MW size check) and "
                  "dispatch_status: capacity_run is refused once either moved",
        evidence=(_T + "test_flipping_sizing_off_is_refused_by_preflight",
                  _T + "test_sizing_off_answers_for_its_source",
                  "tests/test_fixed_sizes.py::test_capacity_run_after_a_nameplate_run_is_refused",
                  "tests/test_fixed_sizes.py::test_capacity_run_reproduces_the_plans_sizes_after_a_cost_edit",
                  "tests/test_fixed_sizes.py::test_a_raise_at_the_commercial_binding_leaves_no_freeze_behind"),
        note="no run kind of its own: the optimisation with fixed_sizes set. Combines with "
             "SCLOPF (tests/test_fixed_sizes.py::test_sclopf_on_fixed_sizes_reproduces_the_"
             "secure_plan); refused with the myopic and rolling strategies and in PF mode"),
)}


def runnable(kind: str) -> bool:
    row = MATRIX.get(kind)
    return row is not None and row.runnable_in_run


def _blocked(code: str, message: str) -> dict:
    return {"ready": False, "deferred": False, "blocker": {"code": code, "message": message}}


def loadflow_verdict() -> dict:
    """The standalone load flow: `/run_ac_pf`'s own refusal (``ac_pf_refusal``)
    read inside the preflight's lock span, so a busy network is ``deferred``."""
    from routers.simulation import ac_pf_refusal, preflight_validate

    deferred, _issues, refusal = preflight_validate(ac_pf_refusal, validate=False)
    if deferred is not None:
        return {"ready": False, "deferred": True,
                "blocker": {"code": "deferred",
                            "message": deferred.get("deferred_reason") or "validation deferred"}}
    if refusal is None:
        return {"ready": True, "deferred": False, "blocker": None}
    return _blocked(*refusal)


# The run option each optional kind switches on in the solver config. Its
# refusals in `validate_for_run` fire only with the option on, so its verdict
# asks the validator with it on (gatekeeper, #497).
# Sizing off (F1) is the `fixed_sizes` option set to its size source.
_OPTION_FLAG = {"sclopf": "sclopf", "loadflow": "run_ac_pf_after_lopf",
                "fixed_sizes": "fixed_sizes"}


def _option_verdict(project_dir: pathlib.Path, kind: str, flow_verdict: dict, *,
                    value=True) -> dict:
    """The verdict on switching ``kind``'s option on (to ``value``):
    ``validate_for_run`` with the option set, in the preflight's lock span, its
    first error in flow order (the section the flow files it to). With the
    option already set so it is the optimisation's own (flow) verdict."""
    from dataclasses import replace

    from routers.simulation import preflight_validate
    from services import flow_sections as F
    from services import flow_service
    from services.validation_service import validate_for_run

    flag = _OPTION_FLAG[kind]

    def under(n, cfg):
        if getattr(cfg, flag, False) == value:
            return None
        errors = [i for i in validate_for_run(n, replace(cfg, **{flag: value}))
                  if i.severity == "error"]

        def section(i) -> str:
            by_code = F.section_for_code(i.code)
            if by_code is not None and by_code != F.BY_COMPONENT:
                return by_code
            if not i.component_class:
                return "readiness"
            return flow_service.section_for_component(
                n, cfg, project_dir, i.component_class, i.name or "")["id"]

        ranked = sorted(((F.SECTION_IDS.index(section(i)), k, i) for k, i in enumerate(errors)),
                        key=lambda t: t[:2])
        return [] if not ranked else [(F.SECTION_IDS[ranked[0][0]], ranked[0][2])]

    deferred, _issues, res = preflight_validate(under, validate=False)
    if deferred is not None:
        return {"ready": False, "deferred": True,
                "blocker": {"code": "deferred",
                            "message": deferred.get("deferred_reason") or "validation deferred"}}
    if res is None:                                       # the option is already on
        return flow_verdict
    if not res:
        return {"ready": True, "deferred": False, "blocker": None}
    sec, issue = res[0]
    return {"ready": False, "deferred": False,
            "blocker": {"code": issue.code, "message": issue.message, "section": sec}}


def _bundled(optimisation: dict, loadflow: dict) -> dict:
    """The Run with Load flow on (W4b): the optimisation's verdict AND Stage 2's
    own, the first blocker in flow order (the flow's ``run_blocker`` over the two)."""
    from services import flow_sections as F

    blockers = [b for b in (optimisation["blocker"], loadflow["blocker"]) if b is not None]
    if not blockers:
        return optimisation
    first = min(blockers, key=lambda b: F.SECTION_IDS.index(b["section"]))
    return {**optimisation, "ready": False, "blocker": first}


def verdicts(project_dir: pathlib.Path, kinds: tuple[str, ...], *, cache_key: str,
             db=None, org_id=None, size_source: str = "capacity_run") -> dict[str, dict]:
    """``{kind: {ready, deferred, blocker}}`` for the open project at
    ``project_dir``. ``blocker`` carries at least ``message``.

    ``optimisation`` and ``study`` ARE the project flow's verdicts
    (``flow_service.project_flow``, R1): one implementation, so the blocker is
    the flow's first blocking item in flow order. ``sclopf``, and ``loadflow``
    when ``optimisation`` is among ``kinds`` (Stage 2, the
    ``run_ac_pf_after_lopf`` option), answer for switching their option ON:
    the validator with the option set (the flow's verdict once it is set; for
    the load flow, the optimisation's and its own Stage-2 items together).
    ``loadflow`` alone is the standalone route's refusal. ``fixed_sizes``
    (sizing off, F1) answers for setting ``fixed_sizes`` to ``size_source``
    ("capacity_run" or "nameplate"). A row that is not runnable reads
    ``ready: false`` with its classification."""
    from services import flow_service
    from services.solver.fixed_sizes import MODES

    out: dict[str, dict] = {}
    flow: dict | None = None
    for kind in kinds:
        if kind == "fixed_sizes" and size_source not in MODES[1:]:
            out[kind] = _blocked("fixed_sizes_unknown",
                                 f"Sizing source {size_source!r} is not one of "
                                 f"{', '.join(MODES[1:])}.")
            continue
        if kind in ("optimisation", "sclopf", "study", "fixed_sizes") or (
                kind == "loadflow" and "optimisation" in kinds):
            if flow is None:
                flow = flow_service.project_flow(project_dir, cache_key=cache_key,
                                                 db=db, org_id=org_id)
            v = flow["verdicts"]["study" if kind == "study" else "optimisation"]
            if kind == "loadflow" and flow["verdicts"].get("loadflow") is not None:
                v = _bundled(v, flow["verdicts"]["loadflow"])
            out[kind] = (_blocked("no_study_request",
                                  "The project has no reliability study to run (no EH template).")
                         if v is None else {**v, "deferred": flow["deferred"]})
            if kind in _OPTION_FLAG and not out[kind]["deferred"]:
                out[kind] = _option_verdict(
                    project_dir, kind, out[kind],
                    value=size_source if kind == "fixed_sizes" else True)
        elif kind == "loadflow":
            out[kind] = loadflow_verdict()
        else:
            row = MATRIX.get(kind)
            cls = row.classification if row is not None else "unknown"
            out[kind] = _blocked(f"kind_{cls}",
                                 f"{row.label if row else kind} is not runnable ({cls})"
                                 + (f": {row.note}" if row is not None and row.note else ""))
    return out
