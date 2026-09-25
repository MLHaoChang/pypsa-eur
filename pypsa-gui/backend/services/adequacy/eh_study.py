"""
Energy Hub study orchestrator (Phase 1.5).

Runs archetype pack → ENS solve → assemble. Optional stages may be skipped;
required-but-missing stages surface as ``not_established`` / pipeline notes.

Report emission: ``assemble_reference_design_report`` only (spec decision 16).
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import queue
import threading
from typing import Any, Iterable

from models.energy_hub import (
    DEFAULT_EH_BUDGET_SOLVES,
    EH_PIPELINE_STAGES,
    MAX_EH_BUDGET_SOLVES,
    ArchetypePack,
    PipelineStageRecord,
    ReferenceDesignReport,
)
from services.adequacy import archetypes as arch
from services.adequacy import eh_report as report_mod

logger = logging.getLogger("pypsa_gui.eh_study")

DEFAULT_STAGES = EH_PIPELINE_STAGES

# Spec decision 14 / P2: FMEA top-N from the EH study is Link-primary Class-B
# residual risk. AC Line/Transformer N-1 stays on SCLOPF and is omitted from
# the FMEA ranking unless a future product decision merges them.
FMEA_TOP_LINK_PRIMARY_NOTE = (
    "Link-primary residual risk (Class-B Link sweep); "
    "AC Line/Transformer N-1 remains on SCLOPF and is omitted from FMEA ranking"
)

# Re-export for tests / callers.
__all__ = [
    "DEFAULT_STAGES",
    "DEFAULT_EH_BUDGET_SOLVES",
    "MAX_EH_BUDGET_SOLVES",
    "FMEA_TOP_LINK_PRIMARY_NOTE",
    "REQUIRED_STAGES",
    "SIBLING_STORE_KEYS",
    "run_eh_study",
    "validate_stages",
]

# Spec decision 18: stages *after* ens_solve may be skipped — these two may not.
# A report labelled with an archetype whose pack was never applied, or with no
# ENS solve behind it, is not that archetype's reference design.
REQUIRED_STAGES: tuple[str, ...] = ("apply_pack", "ens_solve")

# Per-stage sibling tables served by GET /results/eh_* next to the report.
# Cleared when a new study starts so a table from a previous archetype/run is
# never shown beside a newer report.
SIBLING_STORE_KEYS: tuple[str, ...] = (
    "eh_redundancy_comparison",
    "eh_lever_comparison",
    "eh_dtc_stress",
    "eh_dtc_planning",
)

# Pipeline stage → report section it fills (for unreached-stage notes).
_STAGE_SECTION = {
    "redundancy": "redundancy",
    "levers": "levers",
    "dtc_stress": "dtc",
    "dtc_planning": "dtc",
}


def validate_stages(stages: Iterable[str] | None) -> tuple[str, ...] | None:
    """Refuse unknown stage names and lists that drop a required stage.

    ``None`` means the pack's default pipeline. Raises ``ValueError`` with a
    message naming the offending stage(s) — HTTP maps it to 422.
    """
    if stages is None:
        return None
    requested = tuple(str(s) for s in stages)
    if not requested:
        raise ValueError("stages, when set, must be a non-empty list")
    unknown = [s for s in requested if s not in EH_PIPELINE_STAGES]
    if unknown:
        raise ValueError(
            f"unknown EH pipeline stage(s) {unknown}; expected a subset of "
            f"{list(EH_PIPELINE_STAGES)}")
    missing = [s for s in REQUIRED_STAGES if s not in requested]
    if missing:
        raise ValueError(
            f"stages must include {list(REQUIRED_STAGES)} (only stages after "
            f"ens_solve may be skipped); missing {missing}")
    return requested


def _assumptions_hash(cfg) -> str:
    raw = {
        "voll": getattr(cfg, "voll", None),
        "ens_cap_permyriad": getattr(cfg, "ens_cap_permyriad", None),
        "dsr_buses": list(getattr(cfg, "dsr_buses", None) or []),
        "dsr_price_eur_per_mwh": getattr(cfg, "dsr_price_eur_per_mwh", None),
        "dsr_share_of_load": getattr(cfg, "dsr_share_of_load", None),
    }
    blob = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def run_eh_study(
    network,
    pack: ArchetypePack,
    cfg,
    *,
    lock,
    stop_event: threading.Event,
    log_queue: queue.Queue | None = None,
    stages: Iterable[str] | None = None,
    budget_solves: int = DEFAULT_EH_BUDGET_SOLVES,
    state_update=None,
    store: dict | None = None,
    dtc_config=None,
    dsr_buses: list[str] | None = None,
) -> ReferenceDesignReport:
    """Synchronous EH study driver (HTTP worker: ``eh_study_runner``).

    If ``store`` is provided, the finished report is persisted under
    ``eh_reference_design_report`` for ``GET /results/eh_reference_design``,
    and a redundancy stage also writes ``eh_redundancy_comparison`` for
    ``GET /results/eh_redundancy``. Sibling tables from a previous study are
    cleared at start.

    Isolation: the study runs on a private copy of ``network`` and of ``cfg``.
    The pack's ENS target is authoritative for every stage (the report header
    states it), the caller's ``cfg`` is never mutated, and solve side-results
    are never published through ``state_update`` — the foreground results keep
    describing the user's own solve (same rule as frontier ``_restore_base``).
    ``state_update`` is accepted for runner symmetry only.

    ``dsr_buses`` opts buses into the DSR tier for packs with
    ``dsr_opt_in`` (decision 15) through the double-count preflight; the
    preflight's warnings land on the ``apply_pack`` note and ``notes``.

    ``budget_solves`` is a ceiling: multi-solve stages receive the remaining
    budget and a stage that would start with none left is skipped.

    Stage list vs ``pack.levers.redundancy`` (P3a binding condition):
    - Explicit ``stages=...`` wins: including ``\"redundancy\"`` runs the
      compare even when ``levers.redundancy`` is False.
    - Default pipeline (``stages is None``): ``redundancy`` is dropped unless
      ``pack.levers.redundancy`` is True. MVP-A packs ship False and therefore
      skip redundancy unless the caller opts in via levers or stages.
    """
    from services.adequacy.redundancy import _detach_solver_model
    from services.solver_service import SolverConfig, run_simulation

    requested_explicit = validate_stages(stages)
    if requested_explicit is not None:
        requested = requested_explicit
    else:
        def _keep(s: str) -> bool:
            if s == "redundancy":
                return bool(pack.levers.redundancy)
            if s == "levers":
                return bool(pack.levers.import_cap or pack.levers.storage_duration)
            if s == "dtc_stress":
                return bool(pack.dtc_stress_default)
            if s == "dtc_planning":
                return bool(getattr(pack, "dtc_planning_default", False))
            return True
        requested = tuple(s for s in DEFAULT_STAGES if _keep(s))
    budget_solves = max(1, min(int(budget_solves), MAX_EH_BUDGET_SOLVES))
    log_queue = log_queue or queue.SimpleQueue()

    if store is not None:
        for key in SIBLING_STORE_KEYS + (report_mod.EH_REPORT_STORE_KEY,):
            store.pop(key, None)

    # Private cfg: the pack target wins for every stage, the session config is
    # left exactly as the user set it.
    # Private network: pack overlay + ENS solve never touch the shared one.
    # Copied under the network's lock so an edit in flight cannot tear it;
    # every later read (including the DSR preflight) is of the copy.
    with lock:
        _detach_solver_model(network)
        network = network.copy()
    cfg = copy.copy(cfg) if cfg is not None else SolverConfig()
    patch, pack_notes = arch.solver_config_patch_with_preflight(
        pack, network=network, dsr_buses=dsr_buses)
    for k, v in patch.items():
        try:
            setattr(cfg, k, v)
        except Exception:
            pass

    # Stages this driver can actually execute today.
    IMPLEMENTED = frozenset({
        "apply_pack", "ens_solve", "redundancy", "levers", "dtc_stress", "dtc_planning", "assemble",
    })

    records: list[PipelineStageRecord] = []
    for name in DEFAULT_STAGES:
        if name not in requested:
            note = None
            if name == "mc_certify" and pack.mc_certify_required:
                note = "required by pack but not requested — not_established"
            records.append(PipelineStageRecord(
                stage=name, status="skipped", note=note))  # type: ignore[arg-type]
        elif name not in IMPLEMENTED:
            note = f"{name} not implemented in P1.5 sync driver"
            if name == "mc_certify" and pack.mc_certify_required:
                note = "required by pack but not implemented — not_established"
            elif name == "fmea_top":
                note = FMEA_TOP_LINK_PRIMARY_NOTE + f"; {note}"
            records.append(PipelineStageRecord(
                stage=name, status="skipped", note=note))  # type: ignore[arg-type]
        else:
            records.append(PipelineStageRecord(stage=name, status="pending"))  # type: ignore[arg-type]

    section_payloads: dict[str, tuple] = {}
    solves = 0
    aborted = False
    # Set when a stage the rest depend on (ens_solve) ran and failed — not a
    # user abort; later stages are skipped with this reason.
    failed_reason: str | None = None
    undo = lambda: None  # noqa: E731
    pack_h = arch.pack_hash(pack)
    ens_cap = pack.availability.ens_cap_permyriad
    achieved_ens_permyriad = None
    achieved_shed_hours = None
    cost_at_target = None
    period_basis = None
    adequacy_report: dict[str, Any] | None = None
    tea_obj = None
    gates_obj = None

    def _mark(stage: str, status: str, *, note: str | None = None,
              solves_charged: int = 0) -> None:
        for rec in records:
            if rec.stage == stage:
                rec.status = status  # type: ignore[assignment]
                rec.note = note
                rec.solves_charged = solves_charged
                break

    def _remaining() -> int:
        return max(0, budget_solves - solves)

    def _blocked(stage: str) -> bool:
        """Skip a requested stage when ens_solve failed or budget is spent."""
        if failed_reason is not None:
            reason = f"not run: {failed_reason}"
        elif _remaining() <= 0:
            reason = (f"not run: budget_solves exhausted "
                      f"({solves}/{budget_solves}) before this stage")
        else:
            return False
        _mark(stage, "skipped", note=reason)
        section = _STAGE_SECTION.get(stage)
        if section is not None:
            section_payloads.setdefault(
                section, ("not_established", None, reason))
        return True

    try:
        if "apply_pack" in requested:
            if stop_event.is_set():
                aborted = True
                _mark("apply_pack", "aborted")
            else:
                result = arch.apply_archetype_pack_detailed(network, pack)
                undo = result.undo
                pack_h = result.pack_hash
                _mark("apply_pack", "run",
                      note="; ".join(list(result.warnings) + pack_notes)
                      or None)

        if not aborted and "ens_solve" in requested:
            if stop_event.is_set():
                aborted = True
                _mark("ens_solve", "aborted")
            else:
                sink: dict = {}
                status, condition = run_simulation(
                    cfg, network, lock, stop_event, log_queue,
                    state_update=lambda **kw: sink.update(kw),
                )
                solves += 1
                if status not in ("ok", "optimal") and stop_event.is_set():
                    _mark("ens_solve", "aborted",
                          note=f"{status}:{condition}", solves_charged=1)
                    aborted = True
                elif status not in ("ok", "optimal"):
                    failed_reason = f"ens_solve {status}:{condition}"
                    if "infeasible" in str(condition):
                        failed_reason += (
                            " — the pack's ENS target cannot be met by this "
                            "network under the pack overlay")
                    _mark("ens_solve", "failed",
                          note=failed_reason, solves_charged=1)
                    for sec in ("target", "cost", "sizing", "tea",
                                "multi_energy"):
                        section_payloads[sec] = (
                            "not_established", None, failed_reason)
                else:
                    _mark("ens_solve", "run", solves_charged=1)
                    adequacy_report = sink.get("adequacy_report")
                    if isinstance(adequacy_report, dict):
                        tgt = adequacy_report.get("target") or {}
                        system = tgt.get("system") or {}
                        metrics = adequacy_report.get("metrics") or {}
                        cost = adequacy_report.get("cost") or {}
                        achieved_shed_hours = system.get("achieved_shed_hours")
                        ens_mwh = system.get("achieved_ens_mwh")
                        # Invert ‱ if demand known from cap_mwh.
                        cap_mwh = system.get("cap_mwh")
                        ens_cap = getattr(cfg, "ens_cap_permyriad", ens_cap)
                        if (ens_cap is not None and cap_mwh
                                and float(cap_mwh) > 0 and ens_mwh is not None):
                            # achieved ‱ ≈ ens_mwh / demand * 1e4;
                            # demand = cap_mwh / (ens_cap/1e4)
                            demand = float(cap_mwh) / (float(ens_cap) / 1e4)
                            achieved_ens_permyriad = (
                                float(ens_mwh) / demand * 1e4 if demand else None)
                        section_payloads["target"] = (
                            "ok",
                            {"binding": tgt.get("binding"),
                             "system": system, "metrics": metrics},
                            None,
                        )
                        cost_at_target = cost.get("total_system_cost_eur")
                        period_basis = cost.get("period_basis")
                        section_payloads["cost"] = (
                            "ok",
                            {"total_system_cost_eur": cost_at_target,
                             "period_basis": period_basis,
                             "excludes_shed_cost": True},
                            None,
                        )
                        # P5: sizing from the solved network (installed p_nom).
                        sizing = report_mod.sizing_summary_from_network(network)
                        section_payloads["sizing"] = (
                            "ok", sizing, None)
                        # P5: TEA/LCOE from cost ÷ (demand − ENS).
                        ens_for_tea = float(ens_mwh) if ens_mwh is not None else None
                        served = report_mod.served_energy_mwh_from_network(
                            network, ens_mwh=ens_for_tea)
                        if served is None and isinstance(metrics, dict):
                            dem = metrics.get("demand_mwh")
                            ens = metrics.get("ens_mwh")
                            if dem is not None and ens is not None:
                                served = max(0.0, float(dem) - float(ens))
                        if served is None and ens_cap is not None \
                                and cap_mwh and float(cap_mwh) > 0 \
                                and ens_mwh is not None:
                            demand = float(cap_mwh) / (float(ens_cap) / 1e4)
                            served = max(0.0, demand - float(ens_mwh))
                        tea_block = report_mod.compute_tea(
                            cost_eur=cost_at_target,
                            served_energy_mwh=served)
                        if tea_block.lcoe_eur_per_mwh is not None:
                            section_payloads["tea"] = (
                                "ok",
                                tea_block.model_dump(mode="json"),
                                None,
                            )
                        else:
                            section_payloads["tea"] = (
                                "not_established",
                                tea_block.model_dump(mode="json"),
                                tea_block.notes,
                            )
                        # P6(a): dedicated-bus multi-energy ENS disclosure.
                        from services.adequacy import multi_energy as ME
                        capture = sink.get("last_lost_load")
                        if not isinstance(capture, dict):
                            capture = {}
                        me_status, me_payload, me_note = (
                            ME.multi_energy_section_from_capture(
                                network, capture))
                        section_payloads["multi_energy"] = (
                            me_status, me_payload, me_note)
        elif "ens_solve" not in requested:
            section_payloads.setdefault(
                "target", ("not_established", None, "ens_solve not run"))
            section_payloads.setdefault(
                "cost", ("not_established", None, "ens_solve not run"))
            section_payloads.setdefault(
                "multi_energy",
                ("not_established", None, "ens_solve not run"),
            )

        if (not aborted and "redundancy" in requested
                and not _blocked("redundancy")):
            if stop_event.is_set():
                aborted = True
                _mark("redundancy", "aborted")
            else:
                from services.adequacy import redundancy as red
                try:
                    table = red.compare_redundancy_scenarios(
                        network, cfg,
                        lock=lock,
                        stop_event=stop_event,
                        log_queue=log_queue,
                        scenarios=None,
                        availability=pack.availability,
                        store=store,
                        pack_hash=pack_h,
                        assumptions_hash=_assumptions_hash(cfg),
                        max_solves=_remaining(),
                    )
                    n_attempted = int(table.get("solves_attempted") or 0)
                    sec_status, sec_note = red.redundancy_section_status(table)
                    _mark("redundancy", "run",
                          solves_charged=n_attempted,
                          note=sec_note or f"{n_attempted} solves")
                    section_payloads["redundancy"] = (
                        sec_status, table, sec_note)
                    solves += n_attempted
                except Exception as exc:
                    logger.exception("redundancy compare failed")
                    _mark("redundancy", "aborted", note=str(exc))
                    section_payloads["redundancy"] = (
                        "not_established", None, str(exc))


        if (not aborted and "levers" in requested
                and not _blocked("levers")):
            if stop_event.is_set():
                aborted = True
                _mark("levers", "aborted")
            else:
                from services.adequacy import levers as lev
                try:
                    kinds = []
                    if pack.levers.storage_duration:
                        kinds.append("storage_duration")
                    if pack.levers.import_cap:
                        kinds.append("import_cap")
                    if not kinds:
                        kinds = ["storage_duration"]
                    # Primary kind first; merge options if both enabled.
                    merged = None
                    attempted = 0
                    skipped_kinds: list[str] = []
                    applicable_kinds: list[str] = []
                    budget_exhausted = False
                    for kind in kinds:
                        if budget_solves - solves - attempted <= 0:
                            budget_exhausted = True
                            break
                        try:
                            table = lev.compare_lever_scenarios(
                                network, cfg,
                                lock=lock,
                                stop_event=stop_event,
                                log_queue=log_queue,
                                kind=kind,
                                values=None,
                                availability=pack.availability,
                                store=None,
                                pack_hash=pack_h,
                                assumptions_hash=_assumptions_hash(cfg),
                                max_solves=budget_solves - solves - attempted,
                            )
                        except lev.LeverScenarioError as exc:
                            msg = str(exc)
                            # Soft-skip only asset-absence — config/unknown-kind
                            # errors must fail closed with the original message.
                            asset_absent = (
                                "no StorageUnits" in msg
                                or "no import Links" in msg
                            )
                            if not asset_absent:
                                raise
                            skipped_kinds.append(f"{kind}:{exc}")
                            logger.info(
                                "lever kind %s not applicable: %s", kind, exc)
                            continue
                        applicable_kinds.append(kind)
                        attempted += int(table.get("solves_attempted") or 0)
                        budget_exhausted = (
                            budget_exhausted
                            or bool(table.get("budget_exhausted")))
                        if merged is None:
                            merged = table
                        else:
                            merged = {
                                **table,
                                "kind": "+".join(applicable_kinds),
                                "options": list(merged.get("options") or [])
                                + list(table.get("options") or []),
                                "solves_attempted": attempted,
                                "comparable_solved": int(
                                    merged.get("comparable_solved") or 0)
                                + int(table.get("comparable_solved") or 0),
                            }
                    # Always surface soft-skips on the payload (even if store
                    # is None or every kind was inapplicable).
                    if merged is None:
                        merged = {
                            "kind": "+".join(kinds),
                            "options": [],
                            "solves_attempted": 0,
                            "comparable_solved": 0,
                            "aborted": False,
                        }
                    merged = {**merged, "budget_exhausted": budget_exhausted}
                    if skipped_kinds:
                        merged = {**merged, "skipped_kinds": list(skipped_kinds)}
                    if store is not None:
                        store["eh_lever_comparison"] = merged
                    sec_status, sec_note = lev.levers_section_status(merged)
                    if skipped_kinds and not applicable_kinds:
                        skip_note = (
                            "all lever kinds inapplicable: "
                            + "; ".join(skipped_kinds)
                        )
                        sec_status = "not_established"
                        sec_note = skip_note
                        _mark("levers", "skipped",
                              note=skip_note)
                    else:
                        note = sec_note or f"{attempted} solves"
                        if skipped_kinds:
                            note = (
                                f"{note}; soft-skipped "
                                + ", ".join(skipped_kinds)
                            )
                        _mark("levers", "run",
                              solves_charged=attempted,
                              note=note)
                    section_payloads["levers"] = (sec_status, merged, sec_note)
                    solves += attempted
                except Exception as exc:
                    logger.exception("lever compare failed")
                    _mark("levers", "aborted", note=str(exc))
                    section_payloads["levers"] = (
                        "not_established", None, str(exc))


        if (not aborted and "dtc_stress" in requested
                and not _blocked("dtc_stress")):
            if stop_event.is_set():
                aborted = True
                _mark("dtc_stress", "aborted")
            else:
                from services.adequacy import dtc as dtc_mod
                from models.energy_hub import DtcConfig
                try:
                    cfg_dtc = dtc_config
                    if cfg_dtc is None:
                        # Derive a minimal config from import Links + critical tags.
                        from services.adequacy.archetypes import select_import_links
                        links = select_import_links(network, pack.import_overlay)
                        crit_buses = []
                        if network.buses is not None and "eh_critical" in getattr(
                                network.buses, "columns", []):
                            crit_buses = [
                                str(b) for b in network.buses.index
                                if network.buses.at[b, "eh_critical"] is True
                                or str(network.buses.at[b, "eh_critical"]).lower()
                                in ("true", "1", "yes")
                            ]
                        if not links or not crit_buses:
                            raise dtc_mod.DtcStressError(
                                "dtc_stress requested but no import Links / "
                                "critical buses resolved; pass dtc_config"
                            )
                        cfg_dtc = DtcConfig(
                            critical_bus_ids=crit_buses,
                            islanding_contingencies=list(links),
                        )
                    table = dtc_mod.run_dtc_stress(
                        network, cfg,
                        lock=lock,
                        stop_event=stop_event,
                        log_queue=log_queue,
                        dtc=cfg_dtc,
                        store=store,
                        pack_hash=pack_h,
                        assumptions_hash=_assumptions_hash(cfg),
                        max_solves=_remaining(),
                    )
                    n_attempted = int(table.get("solves_attempted") or 0)
                    sec_status, sec_note = dtc_mod.dtc_section_status(table)
                    _mark("dtc_stress", "run",
                          solves_charged=n_attempted,
                          note=sec_note or f"{n_attempted} solves")
                    section_payloads["dtc"] = (sec_status, table, sec_note)
                    solves += n_attempted
                except Exception as exc:
                    logger.exception("DtC stress failed")
                    _mark("dtc_stress", "aborted", note=str(exc))
                    section_payloads["dtc"] = (
                        "not_established", None, str(exc))


        # Optional / not-yet-implemented stages → section skipped (never pending).
        for optional, section in (
            ("frontier", "frontier"),
            ("fmea_top", "fmea_top"),
        ):
            if optional not in IMPLEMENTED:
                if optional == "fmea_top":
                    reason = FMEA_TOP_LINK_PRIMARY_NOTE
                    if optional not in requested:
                        reason = f"{reason}; stage not requested"
                    else:
                        reason = f"{reason}; stage not implemented in sync driver"
                else:
                    reason = (
                        f"{optional} not implemented in sync driver"
                        if optional in requested
                        else f"{optional} not requested"
                    )
                section_payloads.setdefault(
                    section, ("skipped", None, reason))

        if (not aborted and "dtc_planning" in requested
                and not _blocked("dtc_planning")):
            if stop_event.is_set():
                aborted = True
                _mark("dtc_planning", "aborted")
            else:
                from services.adequacy import dtc as dtc_mod
                from models.energy_hub import DtcConfig
                try:
                    cfg_dtc = dtc_config
                    if cfg_dtc is None:
                        from services.adequacy.archetypes import select_import_links
                        links = select_import_links(network, pack.import_overlay)
                        crit_buses = []
                        if network.buses is not None and "eh_critical" in getattr(
                                network.buses, "columns", []):
                            crit_buses = [
                                str(b) for b in network.buses.index
                                if network.buses.at[b, "eh_critical"] is True
                                or str(network.buses.at[b, "eh_critical"]).lower()
                                in ("true", "1", "yes")
                            ]
                        if not links or not crit_buses:
                            raise dtc_mod.DtcPlanningError(
                                "dtc_planning requested but no import Links / "
                                "critical buses resolved; pass dtc_config"
                            )
                        cfg_dtc = DtcConfig(
                            critical_bus_ids=crit_buses,
                            islanding_contingencies=list(links),
                        )
                    table = dtc_mod.run_dtc_planning(
                        network, cfg,
                        lock=lock,
                        stop_event=stop_event,
                        log_queue=log_queue,
                        dtc=cfg_dtc,
                        store=store,
                        pack_hash=pack_h,
                        assumptions_hash=_assumptions_hash(cfg),
                        max_solves=_remaining(),
                    )
                    n_attempted = int(table.get("solves_attempted") or 0)
                    sec_status, sec_note = dtc_mod.dtc_planning_section_status(table)
                    _mark("dtc_planning", "run",
                          solves_charged=n_attempted,
                          note=sec_note or f"{n_attempted} solves")
                    # Merge with existing dtc stress payload when present.
                    prev = section_payloads.get("dtc")
                    if prev and isinstance(prev[1], dict) and prev[1].get("mode") in ("stress", "stress_fixed_plan"):
                        merged = {
                            "mode": "stress+planning",
                            "stress": prev[1],
                            "planning": table,
                            "attribution": "bus_aggregate_not_per_load",
                        }
                        section_payloads["dtc"] = (sec_status, merged, sec_note)
                    else:
                        section_payloads["dtc"] = (sec_status, table, sec_note)
                    solves += n_attempted
                except Exception as exc:
                    logger.exception("DtC planning failed")
                    _mark("dtc_planning", "aborted", note=str(exc))
                    section_payloads.setdefault(
                        "dtc", ("not_established", None, str(exc)))

        if "redundancy" not in requested:
            section_payloads.setdefault(
                "redundancy", ("skipped", None, "redundancy not requested"))
        if "levers" not in requested:
            section_payloads.setdefault(
                "levers", ("skipped", None, "levers not requested"))
        if "dtc_stress" not in requested and "dtc_planning" not in requested:
            section_payloads.setdefault(
                "dtc", ("skipped", None, "dtc_stress/dtc_planning not requested"))

        if pack.archetype == "weak_flexible":
            # P9 thin SCR warn-only gate (feasibility flag, not co-opt).
            # Orthogonal to mc_certify: missing MC stays on the pipeline stage;
            # gates.scr / emt_recommended are the dynamics product fields.
            from services.adequacy import scr_gate as scr_gate_mod
            gate_block, gate_status, gate_payload, gate_note = (
                scr_gate_mod.evaluate_network_scr_gate(network))
            section_payloads["gates"] = (gate_status, gate_payload, gate_note)
            if gate_block is not None:
                gates_obj = gate_block
        elif pack.mc_certify_required and "mc_certify" not in IMPLEMENTED:
            section_payloads["gates"] = (
                "not_established", None,
                "mc_certify required by pack but not implemented in P1.5",
            )
        elif pack.mc_certify_required and "mc_certify" not in requested:
            section_payloads["gates"] = (
                "not_established", None,
                "mc_certify required by pack but not requested",
            )
        else:
            section_payloads.setdefault(
                "gates", (
                    "skipped", None,
                    "SCR gate not required for this archetype (P9 thin slice "
                    "is weak_flexible only)",
                ))

        section_payloads.setdefault(
            "tea", ("skipped", None, "TEA not produced (ens_solve did not run)"))
        section_payloads.setdefault(
            "multi_energy",
            ("skipped", None, "multi_energy not produced (ens_solve did not run)"),
        )
        section_payloads.setdefault(
            "fmea_top", section_payloads.get(
                "fmea_top", ("skipped", None, "fmea_top not requested")))

        tea_obj = None
        tea_sec = section_payloads.get("tea")
        if tea_sec and tea_sec[0] == "ok" and isinstance(tea_sec[1], dict):
            from models.energy_hub import TeaBlock
            tea_obj = TeaBlock.model_validate(tea_sec[1])

        for rec in records:
            if rec.status == "pending" and rec.stage != "assemble":
                reason = (
                    "not reached: study aborted" if aborted
                    else f"not run: {failed_reason}" if failed_reason
                    else "not reached")
                rec.status = "skipped"  # type: ignore[assignment]
                rec.note = reason
                section = _STAGE_SECTION.get(rec.stage)
                if section is not None:
                    section_payloads.setdefault(
                        section, ("not_established", None, reason))

        if "assemble" in requested and not (aborted and adequacy_report is None):
            _mark("assemble", "run")
        elif "assemble" in requested:
            _mark("assemble", "aborted", note="no fragments to assemble")

    finally:
        try:
            undo()
        except Exception:
            logger.exception("EH pack undo failed")

    pipeline = report_mod.pipeline_from_records(
        records, budget_solves=budget_solves, solves_consumed=solves,
        aborted=aborted)

    report = report_mod.assemble_reference_design_report(
        archetype=pack.archetype,
        pack_hash=pack_h,
        assumptions_hash=_assumptions_hash(cfg),
        section_payloads=section_payloads,
        pipeline=pipeline,
        ens_cap_permyriad=ens_cap,
        achieved_ens_permyriad=achieved_ens_permyriad,
        achieved_shed_hours=achieved_shed_hours,
        cost_at_target_eur=cost_at_target,
        period_basis=period_basis,
        tea=tea_obj,
        gates=gates_obj,
        notes=pack_notes,
    )
    if store is not None:
        report_mod.store_eh_report(store, report)
    return report
