"""Variant-1 driver v2: the year study.

Increment 1's `planning.run_39bus_slice` proved one hour end to end. This
driver runs the same chain over a whole year and then decides, from the
dispatch itself, WHICH hours are worth a detailed load flow — the selection is
the deliverable, and the per-hour exports are what a client takes into
PowerFactory.

Every stage writes its artifact before the next starts: the file IS the
boundary, so a client can recompute the metrics and the selection from
`dispatch.csv`, `loads.csv` and the unit-parameter template without ever
loading pypsa or pandapower. Engines are reached only through the caged
modules, exactly as in increment 1.

A selected hour whose load flow does not converge is RECORDED, not raised:
`selected.csv` carries a `converged` column and the run moves to the next
hour. Non-convergence at a thin-inertia hour is a finding about the system,
and a driver that crashed on it would throw away the rest of the study to
report it.

The 8760 h run is a CLI job (minutes), not a test:

    pixi run python -m gridspine.drivers.year_study --out results/gridspine_year
"""
import argparse
import dataclasses
import hashlib
import json
from pathlib import Path

import time

import numpy as np
import pandas as pd

from gridspine.drivers.planning import CASE39_F_HZ
from gridspine.drivers.planning import LEDGER as PLANNING_LEDGER
from gridspine.handoff.bundle import BundleInputs, export_bundle
from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.pandapower_source import (
    RES_LEDGER,
    load_case39_res,
    registry_from_net,
)
from gridspine.ingest.synthetic_profiles import (
    PROFILE_LEDGER,
    solar_cf,
    wind_cf,
    year_load_shape,
)
from gridspine.producers.pypsa_nodal import (
    DEFAULT_MIP_REL_GAP,
    run_uc_rolling,
    to_dispatch_table,
    to_loads_table,
    to_pypsa,
)
from gridspine.ranking.metrics import snapshot_metrics
from gridspine.ranking.severity import SEVERITY_LEDGER, n1_severity_dc
from gridspine.ranking.select import select_snapshots, validate_selection
from gridspine.schema.contracts import ContractError
from gridspine.schema.dc import save_dc_sensitivities
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.schema.errors import StageError
from gridspine.static.contingency import (
    N1_LEDGER,
    measure_prune_threshold,
    n1_severity_ac,
    screen_n1,
    screen_n2,
)
from gridspine.static.contingency_set import (
    EXT_GRID_EXCLUSION_LEDGER,
    branch_contingencies,
    n2_candidates,
    unit_contingencies,
)
from gridspine.static.loadflow import LFResult, apply_snapshot, run_lf
from gridspine.static.lodf import N2_LEDGER, dc_base, to_sensitivities
from gridspine.static.shortcircuit import FAULT_LEDGER, fault_levels
from gridspine.static.strength import SCR_LEDGER, scr
from gridspine.templates.unit_params import load_unit_params, load_unit_templates

STAGES = ["ingest", "dispatch", "ranking", "loadflow", "screening", "handoff"]

#: Canonical sgen name prefix -> the profile generator that drives it.
#: `load_case39_res` names its wind sites `W_BUS_xx` and its solar sites
#: `S_BUS_xx`; the prefix is the technology, so the mapping is a lookup rather
#: than a per-site table that would drift from the ledger.
RES_PROFILE_BY_PREFIX = {"W_": wind_cf, "S_": solar_cf}

#: `reasons` is a list per row and CSV has no list type. Joined on a character
#: that cannot occur in a criterion name (they are all `[a-z_]`), so the split
#: is lossless and needs no quoting rules.
REASON_SEP = "|"

SOURCE_TAGS = ("measured", "datasheet", "assumed")


@dataclasses.dataclass
class StudyResult:
    #: The selection table: `hour`, `reasons` (list[str]) and `converged`.
    selected: object
    #: Artifact name -> Path. Per-hour keys are `lf_<hour>_bus` and `raw_<hour>`.
    artifacts: dict
    #: Selected hour -> its LFResult, convergent or not.
    lf_results: dict
    #: Increment 3 (empty when screen=False). Selected hour -> validated
    #: N-1 + N-2 results, fault levels (both cases), SCR table, bundle dir.
    screening: dict = dataclasses.field(default_factory=dict)
    fault_levels: dict = dataclasses.field(default_factory=dict)
    scr: dict = dataclasses.field(default_factory=dict)
    bundles: dict = dataclasses.field(default_factory=dict)


def res_cf_for(net, hours: int) -> dict:
    """Per-hour capacity factor for EVERY sgen on `net`, keyed by canonical name.

    `to_pypsa` refuses a `res_cf` that misses an sgen, so the dict is built by
    walking `net.sgen` rather than by listing names: a site added to
    `RES_LEDGER` is picked up here without touching this function.

    An sgen whose name carries no known technology prefix raises. Defaulting it
    to a zero capacity factor would model the new technology as permanently
    curtailed and the study would report that as a result — the same class of
    silent wrong answer the ledger exists to prevent.
    """
    profiles = {prefix: fn(hours) for prefix, fn in RES_PROFILE_BY_PREFIX.items()}
    cf = {}
    for name in net.sgen["name"]:
        for prefix, series in profiles.items():
            if str(name).startswith(prefix):
                cf[name] = series
                break
        else:
            raise ContractError(
                f"sgen {name!r} has no known technology prefix; expected one of "
                f"{sorted(RES_PROFILE_BY_PREFIX)}. Add its profile rather than "
                f"letting it default to a zero capacity factor."
            )
    return cf


def _dc_vs_ac(dc: pd.Series, ac: pd.Series) -> dict:
    """Spearman rho and the worst rank gap between the DC proxy and the AC
    number over the hours where both are finite; None when the comparison is
    meaningless (fewer than three hours, or a constant side)."""
    both = pd.concat([dc.rename("dc"), ac.rename("ac")], axis=1).dropna()
    if len(both) >= 3 and both["dc"].nunique() > 1 and both["ac"].nunique() > 1:
        rho = float(both["dc"].rank().corr(both["ac"].rank(), method="pearson"))
        gap = int((both["dc"].rank() - both["ac"].rank()).abs().max())
    else:
        rho, gap = None, None
    return {"hours_compared": int(len(both)), "spearman_rho_dc_vs_ac": rho, "worst_rank_gap_dc_vs_ac": gap}


def _ledger(unit_params, screen: bool = True, ac_pass: dict | None = None) -> list:
    """The report appendix: what was measured, what was assumed, by whom.

    `planning.LEDGER` is carried in full so the increment-1 assumptions that
    still hold (fixed-Q machines, the 3 GW import, the 60 Hz export, the
    constant-power-factor loads) stay attached to the artifacts they describe.
    One of its entries no longer applies, and is superseded explicitly rather
    than dropped: silently editing a ledger entry is indistinguishable from
    never having made the assumption.
    """
    counts = unit_params["source"].value_counts()
    provenance = ", ".join(f"{int(counts.get(t, 0))} {t}" for t in SOURCE_TAGS)
    sites = "; ".join(
        f"{e['name']} at {e['bus']} {e['p_mw']:.0f} MW {e['tech']}"
        for e in RES_LEDGER
    )
    return [
        *PLANNING_LEDGER,
        "SUPERSEDES the 24 h LOAD_SHAPE entry above: the year study drives "
        "demand from year_load_shape(hours), not the increment-1 24 h "
        "LOAD_SHAPE; the daily shape is the same but hour 19 is no longer the "
        "only peak (assumed)",
        *PROFILE_LEDGER,
        f"RES sites and capacities are invented, not sourced (assumed) — {sites}",
        f"unit H params: {provenance} (templates/data/case39_units.yaml)",
        f"rolling UC solved to mip_rel_gap={DEFAULT_MIP_REL_GAP}: an OPTIMALITY "
        "tolerance only — min up/down times, p_min_pu and the hourly energy "
        "balance stay hard constraints at any gap",
        "snapshot ranking sorts on inertia_excl_equiv_mws, which excludes the "
        "aggregated interconnection equivalent G_BUS_39 (h = 500 s); "
        "inertia_mws is the absolute figure to quote",
        "selection is the UNION of the k most extreme hours under each "
        "criterion, so it holds between k and 5k hours, never exactly k",
        "max_n1_severity ranks on n1_severity_ac: the AC N-1 screen (branch and "
        "unit outages, overload depth plus voltage excursion) run at EVERY hour "
        "of the year on the same contingency set the selected hours are screened "
        "with (follow-ups F2); n1_severity_dc is the DC proxy kept for the "
        "year-wide comparison"
        + (
            f" — measured this run: Spearman rho = "
            f"{'n/a' if ac_pass.get('spearman_rho_dc_vs_ac') is None else f'{ac_pass['spearman_rho_dc_vs_ac']:.2f}'}, "
            f"worst rank gap {ac_pass.get('worst_rank_gap_dc_vs_ac')} over "
            f"{ac_pass.get('hours_compared')} hours, AC pass {ac_pass.get('seconds')} s"
            if ac_pass else ""
        ),
        *SEVERITY_LEDGER,
        *EXT_GRID_EXCLUSION_LEDGER,
        *(
            [
                *N1_LEDGER,
                *N2_LEDGER,
                *FAULT_LEDGER,
                *SCR_LEDGER,
                "SCR is taken at the IEC 60909 MINIMUM case, the conservative choice "
                "for weak-grid screening (assumed)",
                "N-2 verified at prune threshold 0 by default: the measured lossless "
                "threshold on case39 prunes nothing (task 5), so every connected pair "
                "is AC-solved and the per-hour measured threshold is recorded",
            ]
            if screen
            else []
        ),
    ]


def dispatch_year(outdir, hours: int = 8760, window: int = 168, overlap: int = 24):
    """Stages ingest and dispatch: the case, its registry, and the rolling unit
    commitment for `hours` hours. Writes `loads.csv` (before the solve — demand
    is an INPUT) and `dispatch.csv` into `outdir`. Returns
    (net, registry, dispatch, loads). `window`/`overlap` are handed to
    `run_uc_rolling` unchanged and validated there.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stage = "ingest"
    try:
        net = load_case39_res()
        registry = registry_from_net(net)

        stage = "dispatch"
        n = to_pypsa(
            net,
            snapshots=hours,
            load_shape=year_load_shape(hours),
            res_cf=res_cf_for(net, hours),
        )
        # Demand is an INPUT to the unit commitment, not a result of it, so the
        # loads artifact is taken before the solve — it is then independent of
        # whether the solve succeeded, exactly as in increment 1.
        loads = to_loads_table(n, net)
        loads.to_csv(outdir / "loads.csv", index=False)

        dispatch = to_dispatch_table(run_uc_rolling(n, window=window, overlap=overlap))
        dispatch.to_csv(outdir / "dispatch.csv", index=False)
        return net, registry, dispatch, loads
    except Exception as exc:
        StageError(stage=stage, element_ids=[], cause=repr(exc)).write(outdir)
        raise


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resume_from_dispatch(
    src,
    outdir,
    k: int = 5,
    screen: bool = True,
    n2_prune_threshold_pct: float = 0.0,
) -> StudyResult:
    """Follow-ups F3: stages ranking to handoff from another run's `dispatch.csv`
    and `loads.csv`, without re-solving the unit commitment (~2 h for a year
    against ~20 min for everything after it).

    The tables are validated, required to cover the same hours, and copied
    byte-for-byte into `outdir`; the manifest's `dispatch_source` names the
    source directory and both files' sha256, so a bundle made here is traceable
    to the solve it came from. `window`/`overlap` are None in the manifest —
    they belong to the solve, which did not happen here.
    """
    src = Path(src)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stage = "ingest"
    try:
        for name in ("dispatch.csv", "loads.csv"):
            if not (src / name).is_file():
                raise ContractError(f"resume_from_dispatch: {src / name} not found — need dispatch.csv and loads.csv")
        dispatch = validate_dispatch(pd.read_csv(src / "dispatch.csv"))
        loads = validate_loads(pd.read_csv(src / "loads.csv"))
        d_hours, l_hours = set(dispatch["hour"].tolist()), set(loads["hour"].tolist())
        if d_hours != l_hours:
            raise ContractError(
                f"resume_from_dispatch: dispatch covers {len(d_hours)} hours, loads {len(l_hours)}; "
                f"they must be the same hours ({sorted(d_hours ^ l_hours)[:5]} differ)"
            )
        for name in ("dispatch.csv", "loads.csv"):
            (outdir / name).write_bytes((src / name).read_bytes())
        dispatch_source = {
            "path": str(src),
            "dispatch_sha256": _sha256(src / "dispatch.csv"),
            "loads_sha256": _sha256(src / "loads.csv"),
            "hours": len(d_hours),
        }
        net = load_case39_res()
        registry = registry_from_net(net)
    except Exception as exc:
        StageError(stage=stage, element_ids=[], cause=repr(exc)).write(outdir)
        raise
    return study_dispatch(
        outdir, net, registry, dispatch, loads, k=k, screen=screen,
        n2_prune_threshold_pct=n2_prune_threshold_pct, dispatch_source=dispatch_source,
    )


def run_year_study(
    outdir,
    hours: int = 8760,
    k: int = 5,
    window: int = 168,
    overlap: int = 24,
    screen: bool = True,
    n2_prune_threshold_pct: float = 0.0,
) -> StudyResult:
    """Run the whole chain for `hours` hours and study the `k`-extreme snapshots:
    `dispatch_year` then `study_dispatch`. Parameters mirror the CLI; `k` is
    validated by `select_snapshots`.
    """
    net, registry, dispatch, loads = dispatch_year(outdir, hours=hours, window=window, overlap=overlap)
    return study_dispatch(
        outdir, net, registry, dispatch, loads, k=k, screen=screen,
        n2_prune_threshold_pct=n2_prune_threshold_pct, window=window, overlap=overlap,
    )


def study_dispatch(
    outdir,
    net,
    registry,
    dispatch,
    loads,
    k: int = 5,
    screen: bool = True,
    n2_prune_threshold_pct: float = 0.0,
    *,
    window: int | None = None,
    overlap: int | None = None,
    dispatch_source: dict | None = None,
) -> StudyResult:
    """Stages ranking, loadflow, screening and handoff for a dispatch that is
    already solved and already written to `outdir` as `dispatch.csv` and
    `loads.csv` (by `dispatch_year` or `resume_from_dispatch`). `window` and
    `overlap` are recorded in the manifest only; `dispatch_source` is the
    resumed run's provenance record (None for a composed run).
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    art = {"loads": outdir / "loads.csv", "dispatch": outdir / "dispatch.csv"}
    lf_results = {}
    n_hours = int(pd.Series(dispatch["hour"]).nunique())
    stage = "ranking"
    try:
        stage = "ranking"
        unit_params = load_unit_params()
        metrics = snapshot_metrics(dispatch, loads, unit_params, registry)
        # Pass 1 of the increment-3 decision: DC ranks the year, AC verifies the
        # selection. Topology is fixed, so the sensitivities are built once on the
        # native net and every hour is one multiply on the ranking side.
        sens = to_sensitivities(dc_base(net))
        art["dc_sensitivities"] = outdir / "dc_sensitivities.npz"
        save_dc_sensitivities(sens, art["dc_sensitivities"])
        metrics["n1_severity_dc"] = n1_severity_dc(dispatch, loads, registry, sens)
        # Follow-ups F2: the ranking's severity criterion is the AC screen's own
        # number at every hour (static/contingency.n1_severity_ac). The DC column
        # stays as the proxy whose agreement with it is measured over the whole
        # year, below. The contingency set is the same one the selected hours are
        # screened with, so the column at a selected hour IS that hour's
        # n1_worst_severity.
        n1_set = pd.concat(
            [branch_contingencies(net), unit_contingencies(registry)], ignore_index=True
        )
        t_ac = time.perf_counter()
        metrics["n1_severity_ac"] = n1_severity_ac(net, n1_set, dispatch, loads, registry)
        ac_seconds = time.perf_counter() - t_ac
        art["metrics"] = outdir / "metrics.csv"
        metrics.to_csv(art["metrics"])
        ac_pass = _dc_vs_ac(metrics["n1_severity_dc"], metrics["n1_severity_ac"])
        ac_pass.update({
            "seconds": round(ac_seconds, 1),
            "hours": int(len(metrics)),
            "hours_not_converged": [int(h) for h in metrics.index[metrics["n1_severity_ac"].isna()]],
        })

        selection = validate_selection(select_snapshots(metrics, k=k), metrics)

        templates = load_unit_templates()
        if screen:
            n2_set = n2_candidates(branch_contingencies(net))
        screening, faults, strength, thresholds, ac_severity = {}, {}, {}, {}, {}

        converged = []
        for hour in selection["hour"]:
            hour = int(hour)
            stage = "loadflow"
            apply_snapshot(net, dispatch, loads, hour=hour, registry=registry)
            lf = run_lf(net)
            lf_results[hour] = lf
            converged.append(bool(lf.converged))
            art[f"lf_{hour}_bus"] = outdir / f"lf_{hour}_bus.csv"
            lf.bus.to_csv(art[f"lf_{hour}_bus"])

            if screen:
                stage = "screening"
                n1 = screen_n1(net, n1_set, dispatch, loads, hour, registry)
                n2, _log = screen_n2(
                    net, n2_set, dispatch, loads, hour, registry,
                    prune_threshold_pct=n2_prune_threshold_pct,
                )
                thresholds[hour], _report = measure_prune_threshold(
                    net, n2_set, dispatch, loads, hour, registry
                )
                screening[hour] = pd.concat([n1, n2], ignore_index=True)
                ok = n1[n1["converged"] & ~n1["islanded"]]
                ac_severity[hour] = float(ok["severity"].max()) if len(ok) else np.nan
                faults[hour] = pd.concat(
                    [fault_levels(net, dispatch, loads, hour, registry, templates, case=c)
                     for c in ("max", "min")],
                    ignore_index=True,
                )
                strength[hour] = scr(faults[hour][faults[hour]["case"] == "min"], registry, templates)

            stage = "handoff"
            # The export is written whether or not the flow converged: the
            # snapshot on the net is the thing being handed over, and a
            # non-convergent hour is precisely the one a client wants to open
            # in PowerFactory.
            art[f"raw_{hour}"] = outdir / f"case39_h{hour}.raw"
            write_raw(
                net,
                art[f"raw_{hour}"],
                title=f"case39_res UC dispatch hour {hour}",
                f_hz=CASE39_F_HZ,
            )

        stage = "ranking"
        selection = selection.assign(converged=converged)
        art["selected"] = outdir / "selected.csv"
        selection.assign(
            reasons=[REASON_SEP.join(r) for r in selection["reasons"]]
        ).to_csv(art["selected"], index=False)
        hours_selected = [int(h) for h in selection["hour"]]

        bundles, extra = {}, {}
        if screen:
            # The two numbers the ledger README declares, measured on the hours
            # this run actually selected — which is why bundles are written in a
            # second pass, after every hour has been screened.
            dc = metrics.loc[hours_selected, "n1_severity_dc"]
            ac = pd.Series({h: ac_severity[h] for h in hours_selected})
            cmp = _dc_vs_ac(dc, ac)
            rho, gap = cmp["spearman_rho_dc_vs_ac"], cmp["worst_rank_gap_dc_vs_ac"]
            blind_spot = {"hours": len(hours_selected), "hours_compared": cmp["hours_compared"],
                          "spearman_rho": rho, "worst_rank_gap": gap,
                          "year_spearman_rho": ac_pass["spearman_rho_dc_vs_ac"],
                          "year_worst_rank_gap": ac_pass["worst_rank_gap_dc_vs_ac"]}
            thr_vals = [thresholds[h] for h in hours_selected]
            measurements = {
                "n2_prune_threshold": (
                    f"measured per selected hour as the largest DC new-loading threshold "
                    f"that loses no pair with a new AC violation: "
                    f"{min(thr_vals):.1f}-{max(thr_vals):.1f} % over {len(thr_vals)} hours; "
                    f"N-2 was verified at {n2_prune_threshold_pct:g} % (every connected pair)"
                ),
                "dc_severity_blind_spot": (
                    f"DC n1_severity_dc vs AC worst N-1 severity over the {cmp['hours_compared']} selected "
                    f"hours with a converged N-1 row: Spearman rho = "
                    f"{'n/a' if rho is None else f'{rho:.2f}'}, worst rank gap "
                    f"{'n/a' if gap is None else gap}; over all {ac_pass['hours_compared']} hours of the "
                    f"year: rho = "
                    f"{'n/a' if ac_pass['spearman_rho_dc_vs_ac'] is None else f'{ac_pass['spearman_rho_dc_vs_ac']:.2f}'}, "
                    f"worst rank gap {ac_pass['worst_rank_gap_dc_vs_ac']} (measured on UC-dispatched hours; "
                    f"the ranking reads the AC column)"
                ),
            }
            ledger = _ledger(unit_params, screen=True, ac_pass=ac_pass)
            stage = "handoff"
            for hour in hours_selected:
                apply_snapshot(net, dispatch, loads, hour=hour, registry=registry)
                bundles[hour] = export_bundle(outdir, BundleInputs(
                    net=net, hour=hour, dispatch=dispatch, loads=loads, registry=registry,
                    unit_params=unit_params, templates=templates, contingency_set=n1_set,
                    lf=lf_results[hour], ledger_entries=ledger, measurements=measurements,
                    f_hz=CASE39_F_HZ, case_name="case39",
                    screening=screening[hour], fault_levels=faults[hour],
                ))
                art[f"bundle_{hour}"] = bundles[hour]
            per_hour = {}
            for hour in hours_selected:
                rows = screening[hour]
                n1 = rows[~rows["contingency_id"].str.contains("--")]
                n2 = rows[rows["contingency_id"].str.contains("--")]
                ok1 = n1[n1["converged"]]
                per_hour[str(hour)] = {
                    "n1_rows": int(len(n1)),
                    "n1_islanded": int(n1["islanded"].sum()),
                    "n1_diverged": int((~n1["converged"] & ~n1["islanded"]).sum()),
                    "n1_worst_severity": float(ok1["severity"].max()) if len(ok1) else None,
                    "n2_rows": int(len(n2)),
                    "n2_islanded": int(n2["islanded"].sum()),
                    "n2_diverged": int((~n2["converged"] & ~n2["islanded"]).sum()),
                    "violations_total": int(rows.loc[rows["converged"], "n_violations"].sum()),
                    "n2_prune_threshold_measured": float(thresholds[hour]),
                }
            extra = {
                "screening": per_hour,
                "n2_prune_threshold_measured": {str(h): float(thresholds[h]) for h in hours_selected},
                "dc_severity_blind_spot": blind_spot,
                "bundles": {str(h): bundles[h].name for h in hours_selected},
            }

        art["manifest"] = outdir / "manifest.json"
        art["manifest"].write_text(json.dumps({
            "screen": bool(screen),
            "n1_severity_ac_pass": ac_pass,
            **extra,
            "stages": STAGES,
            "network": "pandapower case39_res, canonical names",
            "hours": n_hours,
            "k": k,
            "window": window,
            "overlap": overlap,
            "dispatch_source": dispatch_source,
            "mip_rel_gap": DEFAULT_MIP_REL_GAP,
            "selected_hours": hours_selected,
            "converged_hours": [h for h, c in zip(hours_selected, converged) if c],
            "non_converged_hours": [
                h for h, c in zip(hours_selected, converged) if not c
            ],
            "load_consistency": "per-snapshot loads artifact (increment 2)",
            "artifacts": {name: p.name for name, p in sorted(art.items())},
            "ledger": _ledger(unit_params, screen=screen, ac_pass=ac_pass),
        }, indent=2))
        return StudyResult(
            selected=selection, artifacts=art, lf_results=lf_results,
            screening=screening, fault_levels=faults, scr=strength, bundles=bundles,
        )
    except Exception as exc:
        StageError(stage=stage, element_ids=[], cause=repr(exc)).write(outdir)
        raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="gridspine year study")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=int, default=8760)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--window", type=int, default=168)
    ap.add_argument("--overlap", type=int, default=24)
    ap.add_argument("--no-screen", action="store_true",
                    help="skip N-1/N-2, fault levels, SCR and bundles (increment-2 behaviour)")
    ap.add_argument("--n2-prune-threshold", type=float, default=0.0,
                    help="DC new-loading %% below which N-2 pairs are not AC-verified (0 = verify all)")
    ap.add_argument("--from-dispatch", metavar="DIR", default=None,
                    help="study the dispatch.csv/loads.csv in DIR instead of solving; "
                         "--hours/--window/--overlap are then ignored (follow-ups F3)")
    args = ap.parse_args()
    if args.from_dispatch:
        res = resume_from_dispatch(
            args.from_dispatch, args.out, k=args.k,
            screen=not args.no_screen, n2_prune_threshold_pct=args.n2_prune_threshold,
        )
    else:
        res = run_year_study(
            args.out, hours=args.hours, k=args.k,
            window=args.window, overlap=args.overlap,
            screen=not args.no_screen, n2_prune_threshold_pct=args.n2_prune_threshold,
        )
    for _, row in res.selected.iterrows():
        print(
            f"hour {int(row['hour']):5d}  converged={bool(row['converged'])}  "
            f"{','.join(row['reasons'])}"
        )
    for name, p in res.artifacts.items():
        print(f"{name}: {p}")
