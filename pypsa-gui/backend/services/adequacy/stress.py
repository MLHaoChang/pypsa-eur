"""
Class-C stress scenarios: correlated weather+demand extremes as
whole-scenario re-solves (Phase 4 Task 3).

Design: spec §4.1 class C; plan 2026-08-28-fmea-phase4-taxonomy.md. Class C
exists because coincident extremes — Dunkelflaute, cold snaps — dominate
real adequacy tails and CANNOT be composed from independent outage draws:
availability and demand must move together, which is exactly what one
whole-scenario re-solve does.

DATA HONESTY. The design decision was to bundle reference climate years;
that data cannot be procured from this environment and is a RECORDED
procurement follow-up, not silently dropped. This module ships the
machinery: ``kind="parametric"`` scenarios (load/availability multipliers,
loudly labelled parametric in their occurrence basis) run today;
``kind="profiles"`` entries are accepted by the registry for forward
compatibility and reported as not-yet-runnable by the sweep — a real
climate year later becomes just another scenario entry with real profiles.

Registry: a per-project JSON sidecar (``adequacy_stress_scenarios.json``)
on the worksheet-service pattern — atomic, schema-versioned, capped,
reject-don't-truncate.

Scope rules reuse the adequacy classifiers: the load multiplier hits
ELECTRICAL loads only; the availability multiplier hits generators whose
availability is PROFILE-BORNE (no resolvable occurrence data — the same
must-take rule the COPT applies), never the thermal fleet, whose outages
are classes A/B's business.
"""
from __future__ import annotations

import json
import math
import pathlib
import re

from services.atomic_io import atomic_write_text

SIDECAR_NAME = "adequacy_stress_scenarios.json"
SCHEMA = 1
MAX_SCENARIOS = 10
_ID_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")
VALID_KINDS = ("parametric", "profiles")


class StressValidationError(ValueError):
    pass


def _validate(scenarios: list[dict]) -> None:
    if len(scenarios) > MAX_SCENARIOS:
        raise StressValidationError(
            f"too many scenarios ({len(scenarios)} > {MAX_SCENARIOS})")
    seen: set[str] = set()
    for sc in scenarios:
        sid = str(sc.get("id", ""))
        if not _ID_RE.match(sid):
            raise StressValidationError(
                f"scenario id '{sid}' must match [a-z0-9_-]{{1,64}}")
        if sid in seen:
            raise StressValidationError(f"duplicate scenario id '{sid}'")
        seen.add(sid)
        if sc.get("kind") not in VALID_KINDS:
            raise StressValidationError(
                f"scenario '{sid}': kind must be one of {VALID_KINDS}")
        try:
            freq = float(sc.get("frequency_per_year"))
        except (TypeError, ValueError):
            freq = float("nan")
        if not (math.isfinite(freq) and 0 < freq <= 365):
            raise StressValidationError(
                f"scenario '{sid}': frequency_per_year must be in (0, 365] — "
                "it is the empirical events-per-year of the stress condition")
        if sc.get("kind") == "parametric":
            lm = float(sc.get("electrical_load_multiplier", 1.0) or 1.0)
            rm = float(sc.get("renewable_availability_multiplier", 1.0) or 1.0)
            if not (0 < lm <= 10):
                raise StressValidationError(
                    f"scenario '{sid}': load multiplier {lm:g} outside (0, 10]")
            if not (0 <= rm <= 1.5):
                raise StressValidationError(
                    f"scenario '{sid}': availability multiplier {rm:g} "
                    "outside [0, 1.5]")


def load_scenarios(project_dir: pathlib.Path) -> list[dict]:
    path = project_dir / SIDECAR_NAME
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict) or raw.get("__schema__") != SCHEMA:
        return []
    return list(raw.get("scenarios") or [])


def save_scenarios(project_dir: pathlib.Path, scenarios: list[dict]) -> list[dict]:
    _validate(scenarios)
    atomic_write_text(
        project_dir / SIDECAR_NAME,
        json.dumps({"__schema__": SCHEMA, "scenarios": scenarios},
                   indent=2, sort_keys=True))
    return scenarios


# ── the re-solve ──────────────────────────────────────────────────────────

def _parametric_mutate(scenario: dict):
    lm = float(scenario.get("electrical_load_multiplier", 1.0) or 1.0)
    rm = float(scenario.get("renewable_availability_multiplier", 1.0) or 1.0)

    def mutate(n):
        from services.adequacy.metrics import electrical_columns
        from services.adequacy.occurrence import resolve_outage_params

        undo_ops = []
        elec = set(electrical_columns(n, list(n.buses.index)))

        # Electrical loads × lm — static p_set and time-varying columns.
        loads = n.loads
        if loads is not None and not loads.empty and "bus" in loads.columns:
            elec_loads = [l for l in loads.index
                          if str(loads.at[l, "bus"]) in elec]
            if lm != 1.0 and elec_loads:
                orig_static = loads.loc[elec_loads, "p_set"].copy()
                loads.loc[elec_loads, "p_set"] = orig_static * lm

                def _undo_static(n=n, names=list(elec_loads), orig=orig_static):
                    live = n.loads
                    keep = [x for x in names if x in live.index]
                    live.loc[keep, "p_set"] = orig.loc[keep]

                undo_ops.append(_undo_static)
                p_set_t = getattr(getattr(n, "loads_t", None), "p_set", None)
                if p_set_t is not None:
                    t_cols = [l for l in elec_loads
                              if l in getattr(p_set_t, "columns", [])]
                    if t_cols:
                        orig_t = p_set_t[t_cols].copy()
                        p_set_t[t_cols] = orig_t * lm

                        def _undo_t(n=n, cols=list(t_cols), orig=orig_t):
                            live = getattr(getattr(n, "loads_t", None), "p_set", None)
                            if live is not None:
                                keep = [c for c in cols if c in live.columns]
                                live[keep] = orig[keep]

                        undo_ops.append(_undo_t)

        # Profile-borne (must-take) electrical generators × rm — the same
        # membership rule the COPT applies: no resolvable occurrence data.
        gens = n.generators
        if rm != 1.0 and gens is not None and not gens.empty:
            params = resolve_outage_params(n, "generators")
            targets = [g for g in gens.index
                       if params.loc[g, "source"] == "missing"
                       and str(gens.at[g, "bus"]) in elec]
            if targets:
                if "p_max_pu" in gens.columns:
                    orig_static = gens.loc[targets, "p_max_pu"].copy()
                    gens.loc[targets, "p_max_pu"] = orig_static * rm

                    def _undo_gs(n=n, names=list(targets), orig=orig_static):
                        live = n.generators
                        keep = [x for x in names if x in live.index]
                        live.loc[keep, "p_max_pu"] = orig.loc[keep]

                    undo_ops.append(_undo_gs)
                pmp_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
                if pmp_t is not None:
                    t_cols = [g for g in targets
                              if g in getattr(pmp_t, "columns", [])]
                    if t_cols:
                        orig_t = pmp_t[t_cols].copy()
                        pmp_t[t_cols] = orig_t * rm

                        def _undo_gt(n=n, cols=list(t_cols), orig=orig_t):
                            live = getattr(getattr(n, "generators_t", None),
                                           "p_max_pu", None)
                            if live is not None:
                                keep = [c for c in cols if c in live.columns]
                                live[keep] = orig[keep]

                        undo_ops.append(_undo_gt)

        def undo():
            for op in reversed(undo_ops):
                op()

        return undo

    return mutate


def run_class_c_sweep(network, lock, cfg, scenarios: list[dict], *,
                      log_queue=None, final_state_update=None,
                      stop_event=None) -> list[dict]:
    """
    Class-C rows via the shared driver. occurrence = the scenario's
    empirical ``frequency_per_year``; severity = ΔEUE × VoLL per event;
    criticality = the product (f×S by construction). Parametric provenance
    is loud: the occurrence basis is ``scenario:parametric``. ``profiles``
    entries return a not-runnable status row instead of pretending.
    """
    from services.adequacy.sweep import run_contingency_sweep

    _validate(scenarios)
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    runnable = [sc for sc in scenarios if sc["kind"] == "parametric"]
    rows: list[dict] = []
    for sc in scenarios:
        if sc["kind"] != "parametric":
            rows.append({"id": f"scenario:{sc['id']}",
                         "status": "profiles_not_supported_yet",
                         "delta_eue_mwh": None, "failure_mode": None,
                         "meta": {"name": sc.get("name", sc["id"])}})
    if not runnable:
        return rows
    contingencies = [
        {"id": f"scenario:{sc['id']}", "mutate": _parametric_mutate(sc),
         "meta": {"name": sc.get("name", sc["id"]),
                  "frequency_per_year": float(sc["frequency_per_year"])}}
        for sc in runnable
    ]
    swept = run_contingency_sweep(
        network, lock, cfg, contingencies, stop_event=stop_event,
        log_queue=log_queue, final_state_update=final_state_update)
    for c in contingencies:
        # Phase 12e: see the class-B assembler — an aborted sweep carries
        # only what it reached, and the rest are skipped rather than raising.
        res = swept["contingencies"].get(c["id"])
        if res is None:
            continue
        meta = c["meta"]
        if res["status"] not in ("ok", "optimal"):
            rows.append({"id": c["id"], "status": res["status"],
                         "delta_eue_mwh": None, "failure_mode": None,
                         "meta": meta})
            continue
        delta = float(res["delta_eue_mwh"] or 0.0)
        freq = meta["frequency_per_year"]
        severity = delta * voll
        rows.append({
            "id": c["id"],
            "status": res["status"],
            "delta_eue_mwh": delta,
            "failure_mode": {
                "mode_id": c["id"],
                "component_class": "Network",
                "name": meta["name"],
                "failure_class": "C",
                "occurrence_per_year": freq,
                "occurrence_basis": "scenario:parametric",
                "severity_eur": severity,
                "criticality_eur_per_year": freq * severity,
                "in_metric_scope": True,
                "engine": "lp_proxy",
                "fidelity": "deterministic_scenario",
            },
            "meta": meta,
        })
    rows.sort(key=lambda r: (r["delta_eue_mwh"] or 0.0), reverse=True)
    # Phase 12e (shipped-code review, finding 1): the closing restore's
    # outcome rides out beside the rows, as class B's does — a sweep whose
    # re-solve failed must not report `done` while the network sits on the
    # last scenario.
    return rows, {"base_restored": swept.get("base_restored"),
                  "base_restore_status": swept.get("base_restore_status"),
                  "aborted": bool(swept.get("aborted"))}
