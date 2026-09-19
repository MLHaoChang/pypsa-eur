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
``kind="profiles"`` scenarios swap absolute load / renewable availability
series (inline or via a synthetic ``profile_pack``). Real climate-year
bundles remain optional behind data availability — a climate year later
becomes just another profiles scenario entry.

Incomplete profiles (no series / pack / mismatched lengths) are
fail-closed as ``profiles_incomplete``, never solved as parametric.

Registry: a per-project JSON sidecar (``adequacy_stress_scenarios.json``)
on the worksheet-service pattern — atomic, schema-versioned, capped,
reject-don't-truncate.

Scope rules reuse the adequacy classifiers: the load multiplier hits
ELECTRICAL loads only; the availability multiplier hits generators whose
availability is PROFILE-BORNE (no resolvable occurrence data — the same
must-take rule the COPT applies), never the thermal fleet, whose outages
are classes A/B's business.

Phase 12h note: a generator whose availability is declared to already
include its outages (``p_max_pu_includes_outages``) still resolves outage
data — its rate is zeroed, not removed — so it stays on the occurrence side
of that rule and the availability multiplier does NOT reach it. That is the
conservative reading: the multiplier is a climate lever, and this unit's
availability is a fleet statistic, not a weather year.
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

# Bundled synthetic profile packs (P8a). Real climate years are NOT here —
# they are a procurement follow-up. Paths relative to this module resolve
# to the test fixtures tree when present; production can overlay a
# directory later without changing the scenario schema.
_SYNTHETIC_PACK_DIRS: tuple[pathlib.Path, ...] = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tests" / "fixtures" / "eh_class_c",
)


class StressValidationError(ValueError):
    pass


def load_synthetic_profile_pack(pack_id: str) -> dict:
    """Load a bundled synthetic profiles scenario by id (no ``.json``)."""
    sid = str(pack_id).strip()
    if not sid or not _ID_RE.match(sid):
        raise StressValidationError(
            f"profile_pack id '{pack_id}' must match [a-z0-9_-]{{1,64}}")
    for root in _SYNTHETIC_PACK_DIRS:
        path = root / f"{sid}.json"
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StressValidationError(
                f"profile_pack '{sid}' unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise StressValidationError(
                f"profile_pack '{sid}' must be a JSON object")
        out = dict(raw)
        out["kind"] = "profiles"
        out.setdefault("id", sid)
        return out
    raise StressValidationError(
        f"unknown synthetic profile_pack '{sid}' "
        f"(looked in {[str(p) for p in _SYNTHETIC_PACK_DIRS]})")


def _series_map(raw) -> dict[str, list[float]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, list[float]] = {}
    for key, vals in raw.items():
        if not isinstance(vals, (list, tuple)) or not vals:
            return None
        try:
            series = [float(v) for v in vals]
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in series):
            return None
        out[str(key)] = series
    return out or None


def _profiles_payload(scenario: dict) -> tuple[
        dict[str, list[float]] | None, dict[str, list[float]] | None]:
    """Resolve inline series or ``profile_pack`` into (loads, gens) maps."""
    sc = scenario
    if sc.get("profile_pack"):
        pack = load_synthetic_profile_pack(str(sc["profile_pack"]))
        # Inline keys on the scenario override pack fields.
        loads = _series_map(sc.get("loads_p_set")) or _series_map(
            pack.get("loads_p_set"))
        gens = _series_map(sc.get("generators_p_max_pu")) or _series_map(
            pack.get("generators_p_max_pu"))
        return loads, gens
    return (_series_map(sc.get("loads_p_set")),
            _series_map(sc.get("generators_p_max_pu")))


def _profiles_ready(scenario: dict) -> bool:
    loads, gens = _profiles_payload(scenario)
    if loads is None and gens is None:
        return False
    lengths = [len(v) for v in (loads or {}).values()]
    lengths += [len(v) for v in (gens or {}).values()]
    if not lengths:
        return False
    return len(set(lengths)) == 1 and lengths[0] > 0


def _profiles_match_horizon(scenario: dict, n_snapshots: int) -> bool:
    """True when every series length equals the live network horizon."""
    loads, gens = _profiles_payload(scenario)
    for series_map in (loads, gens):
        if series_map is None:
            continue
        if any(len(v) != n_snapshots for v in series_map.values()):
            return False
    return True


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
        elif sc.get("kind") == "profiles":
            # Incomplete is allowed in the registry (forward-compat climate
            # stub) but series that ARE present must be finite + equal length.
            loads = _series_map(sc.get("loads_p_set"))
            gens = _series_map(sc.get("generators_p_max_pu"))
            if sc.get("loads_p_set") is not None and loads is None:
                raise StressValidationError(
                    f"scenario '{sid}': loads_p_set must be "
                    "{{name: [finite floats, ...]}}")
            if sc.get("generators_p_max_pu") is not None and gens is None:
                raise StressValidationError(
                    f"scenario '{sid}': generators_p_max_pu must be "
                    "{{name: [finite floats, ...]}}")
            lengths = [len(v) for v in (loads or {}).values()]
            lengths += [len(v) for v in (gens or {}).values()]
            if lengths and len(set(lengths)) != 1:
                raise StressValidationError(
                    f"scenario '{sid}': profile series length mismatch "
                    f"(got lengths {sorted(set(lengths))})")
            if sc.get("profile_pack"):
                # Resolve now so a typo fails at save, not mid-sweep.
                load_synthetic_profile_pack(str(sc["profile_pack"]))


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


def _profiles_mutate(scenario: dict):
    """Swap absolute load / renewable p_max_pu series for one climate-like year.

    Only electrical loads and profile-borne (must-take) generators are
    touched — same membership rule as the parametric renewable multiplier.
    Series length must equal ``len(n.snapshots)`` at mutate time; otherwise
    the contingency fails closed (no partial apply).
    """
    loads_map, gens_map = _profiles_payload(scenario)

    def mutate(n):
        import pandas as pd

        from services.adequacy.metrics import electrical_columns
        from services.adequacy.occurrence import resolve_outage_params

        h = len(n.snapshots)
        for series_map, label in ((loads_map, "loads_p_set"),
                                  (gens_map, "generators_p_max_pu")):
            if series_map is None:
                continue
            bad = [k for k, v in series_map.items() if len(v) != h]
            if bad:
                raise StressValidationError(
                    f"scenario '{scenario.get('id')}': {label} length "
                    f"must equal snapshots ({h}); bad: {bad}")

        undo_ops = []
        elec = set(electrical_columns(n, list(n.buses.index)))
        idx = n.snapshots

        if loads_map:
            loads = n.loads
            if loads is not None and not loads.empty and "bus" in loads.columns:
                for name, series in loads_map.items():
                    if name not in loads.index:
                        continue
                    if str(loads.at[name, "bus"]) not in elec:
                        continue
                    p_set_t = getattr(getattr(n, "loads_t", None), "p_set", None)
                    had_col = (
                        p_set_t is not None
                        and name in getattr(p_set_t, "columns", [])
                    )
                    if had_col:
                        orig_t = p_set_t[name].copy()
                        p_set_t[name] = pd.Series(series, index=idx)

                        def _undo_lt(n=n, col=name, orig=orig_t):
                            live = getattr(getattr(n, "loads_t", None),
                                           "p_set", None)
                            if live is not None and col in live.columns:
                                live[col] = orig

                        undo_ops.append(_undo_lt)
                    else:
                        orig_static = float(loads.at[name, "p_set"])
                        loads.at[name, "p_set"] = float(series[0])
                        if p_set_t is None:
                            n.loads_t.p_set = pd.DataFrame(
                                {name: series}, index=idx)
                        else:
                            n.loads_t.p_set[name] = pd.Series(
                                series, index=idx)

                        def _undo_ls(n=n, col=name, orig_s=orig_static):
                            live = n.loads
                            if col in live.index:
                                live.at[col, "p_set"] = orig_s
                            live_t = getattr(getattr(n, "loads_t", None),
                                             "p_set", None)
                            if live_t is not None and col in live_t.columns:
                                live_t.drop(columns=[col], inplace=True)

                        undo_ops.append(_undo_ls)

        if gens_map:
            gens = n.generators
            if gens is not None and not gens.empty:
                params = resolve_outage_params(n, "generators")
                must_take = {
                    g for g in gens.index
                    if params.loc[g, "source"] == "missing"
                    and str(gens.at[g, "bus"]) in elec
                }
                pmp_t = getattr(getattr(n, "generators_t", None), "p_max_pu", None)
                for name, series in gens_map.items():
                    if name not in must_take:
                        continue
                    had_col = (
                        pmp_t is not None
                        and name in getattr(pmp_t, "columns", [])
                    )
                    if had_col:
                        orig_t = pmp_t[name].copy()
                        pmp_t[name] = pd.Series(series, index=idx)

                        def _undo_gt(n=n, col=name, orig=orig_t):
                            live = getattr(getattr(n, "generators_t", None),
                                           "p_max_pu", None)
                            if live is not None and col in live.columns:
                                live[col] = orig

                        undo_ops.append(_undo_gt)
                    elif "p_max_pu" in gens.columns:
                        orig_static = float(gens.at[name, "p_max_pu"])
                        gens.at[name, "p_max_pu"] = float(series[0])
                        if pmp_t is None:
                            n.generators_t.p_max_pu = pd.DataFrame(
                                {name: series}, index=idx)
                        else:
                            n.generators_t.p_max_pu[name] = pd.Series(
                                series, index=idx)

                        def _undo_gs(n=n, col=name, orig_s=orig_static):
                            live = n.generators
                            if col in live.index:
                                live.at[col, "p_max_pu"] = orig_s
                            live_t = getattr(getattr(n, "generators_t", None),
                                             "p_max_pu", None)
                            if live_t is not None and col in live_t.columns:
                                live_t.drop(columns=[col], inplace=True)

                        undo_ops.append(_undo_gs)

        def undo():
            for op in reversed(undo_ops):
                op()

        return undo

    return mutate


def run_class_c_sweep(network, lock, cfg, scenarios: list[dict], *,
                      log_queue=None, final_state_update=None,
                      stop_event=None) -> tuple[list[dict], dict]:
    """
    Class-C rows via the shared driver. occurrence = the scenario's
    empirical ``frequency_per_year``; severity = ΔEUE × VoLL per event;
    criticality = the product (f×S by construction).

    Provenance is loud:
    * parametric → ``occurrence_basis="scenario:parametric"``
    * profiles (complete) → ``occurrence_basis="scenario:profiles"``
    * profiles incomplete → status ``profiles_incomplete`` (not solved)
    """
    from services.adequacy.sweep import run_contingency_sweep

    _validate(scenarios)
    voll = float(getattr(cfg, "voll", 0.0) or 0.0)
    # Horizon for fail-closed length checks. Missing on stub networks used by
    # unit tests that monkeypatch the sweep — those skip the length gate.
    try:
        n_snapshots = len(network.snapshots)
    except Exception:                                         # noqa: BLE001
        n_snapshots = None
    rows: list[dict] = []
    contingencies: list[dict] = []
    for sc in scenarios:
        sid = f"scenario:{sc['id']}"
        meta = {"name": sc.get("name", sc["id"]),
                "frequency_per_year": float(sc["frequency_per_year"]),
                "kind": sc["kind"]}
        if sc["kind"] == "parametric":
            contingencies.append({
                "id": sid, "mutate": _parametric_mutate(sc), "meta": meta,
                "occurrence_basis": "scenario:parametric",
            })
        elif sc["kind"] == "profiles":
            if not _profiles_ready(sc):
                rows.append({
                    "id": sid, "status": "profiles_incomplete",
                    "delta_eue_mwh": None, "failure_mode": None,
                    "meta": meta,
                })
                continue
            # Binding condition: length ≠ snapshots is a per-scenario
            # incomplete row — never an uncaught mutate raise that aborts
            # the rest of the Class-C sweep.
            if (n_snapshots is not None
                    and not _profiles_match_horizon(sc, n_snapshots)):
                rows.append({
                    "id": sid, "status": "profiles_incomplete",
                    "delta_eue_mwh": None, "failure_mode": None,
                    "meta": {**meta, "note": "series_length_ne_snapshots"},
                })
                continue
            contingencies.append({
                "id": sid, "mutate": _profiles_mutate(sc), "meta": meta,
                "occurrence_basis": "scenario:profiles",
            })
        else:
            rows.append({
                "id": sid, "status": "profiles_incomplete",
                "delta_eue_mwh": None, "failure_mode": None, "meta": meta,
            })

    if not contingencies:
        return rows, {"base_restored": None, "base_restore_status": None,
                      "aborted": False}

    swept = run_contingency_sweep(
        network, lock, cfg, contingencies, stop_event=stop_event,
        log_queue=log_queue, final_state_update=final_state_update)
    for c in contingencies:
        # Phase 12e: aborted sweep carries only what it reached.
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
                "occurrence_basis": c["occurrence_basis"],
                "severity_eur": severity,
                "criticality_eur_per_year": freq * severity,
                "in_metric_scope": True,
                "engine": "lp_proxy",
                "fidelity": "deterministic_scenario",
            },
            "meta": meta,
        })
    rows.sort(key=lambda r: (r["delta_eue_mwh"] or 0.0), reverse=True)
    return rows, {"base_restored": swept.get("base_restored"),
                  "base_restore_status": swept.get("base_restore_status"),
                  "aborted": bool(swept.get("aborted"))}
