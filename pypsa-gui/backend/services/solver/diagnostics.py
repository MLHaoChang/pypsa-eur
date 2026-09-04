"""
Solver diagnostics: infeasibility analysis and the post-solve logging family.

Carved out of `services/solver_service.py` — the largest single cluster in it,
and the safest. Everything here READS a solved network and formats strings onto
the log queue. Nothing mutates the network, touches the LP, or influences a
number the frontend serves, so a defect introduced here shows up in the solver
log and nowhere else.

`solver_service` calls in through five entry points only:

    _diagnose_infeasibility(network, config, log_queue)
    _log_global_constraint_shadow_prices(network, log_queue)
    _emit_core_post_solve_diagnostics(network, sns, current_period, phase)
    _log_cost_decomposition_post_solve(network, cfg, sns, current_period, phase)
    _log_sclopf_post_solve(network, sns, current_period, iter_outages, phase)

Outward it needs `_annuity` and `services.period_utils` and nothing else, which
is why `periodized_costs` had to be carved first. `_per_period_split` is a
nested helper inside `_log_cost_decomposition_post_solve` and travels with its
parent.

Never imports from `solver_service`; `tests/test_solver_facade_surface.py`
enforces that.
"""
import math
import time

import pandas as pd

from services import period_utils as _period_utils
from services.solver.periodized_costs import _annuity


def _diagnose_infeasibility(network, config, log_queue) -> None:
    """
    Heuristic "why is it infeasible?" hints, emitted as ``[INFEASIBLE]`` lines.

    HiGHS (the default solver) exposes no exact irreducible-inconsistent-subsystem
    (IIS), so on a CONFIRMED-infeasible LP we point at the common structural
    causes a modeller can act on: a bus carrying load with no way to serve it; a
    peak demand that exceeds all available + buildable generation with no VOLL;
    and any active global constraint (CO2 cap, …) that may be infeasibly tight.
    Read-only, and it runs ONLY after the solver already returned infeasible — so
    every hint is a safe pointer, never a false block. When the solver is Gurobi,
    also surface its native infeasibility report.
    """
    def emit(msg: str) -> None:
        try:
            log_queue.put(msg)
        except Exception:
            pass

    n = network
    emit("[INFEASIBLE] Diagnosing likely causes (heuristic — the LP solver found "
         "no feasible point):")
    hints = 0

    # 1) Islanded load: a bus with load but no local supply AND no branch.
    try:
        connected: set = set()
        for comp in ("lines", "transformers"):
            df = getattr(n, comp, None)
            if df is not None and not df.empty:
                connected |= set(df["bus0"]) | set(df["bus1"])
        links = getattr(n, "links", None)
        if links is not None and not links.empty:
            for col in ("bus0", "bus1", "bus2", "bus3", "bus4"):
                if col in links.columns:
                    connected |= {b for b in links[col].astype(str) if b and b != "nan"}
        supply_buses: set = set()
        for comp in ("generators", "storage_units", "stores"):
            df = getattr(n, comp, None)
            if df is not None and not df.empty and "bus" in df.columns:
                supply_buses |= set(df["bus"])
        loads = getattr(n, "loads", None)
        if loads is not None and not loads.empty:
            ldt = getattr(n.loads_t, "p_set", None)
            for name, ld in loads.iterrows():
                bus = ld["bus"]
                if ldt is not None and name in ldt.columns:
                    peak = float(ldt[name].abs().max())
                else:
                    peak = float(abs(ld.get("p_set", 0.0)))
                if peak > 1e-6 and bus not in supply_buses and bus not in connected:
                    emit(f"[INFEASIBLE]   • Bus '{bus}' carries ~{peak:,.0f} MW of load but "
                         "has no generator/storage and no line/link — it can never be served.")
                    hints += 1
    except Exception:
        pass

    # 2) Whole-network peak demand vs maximum available + buildable supply.
    try:
        import numpy as _np
        ldt = getattr(n.loads_t, "p_set", None)
        if ldt is not None and not ldt.empty:
            peak_load = float(ldt.sum(axis=1).abs().max())
        else:
            peak_load = float(n.loads["p_set"].abs().sum()) if not n.loads.empty else 0.0
        avail = 0.0
        gens = getattr(n, "generators", None)
        if gens is not None and not gens.empty:
            gmax = getattr(n.generators_t, "p_max_pu", None)
            for name, g in gens.iterrows():
                if bool(g.get("p_nom_extendable", False)):
                    pmax = float(g.get("p_nom_max", _np.inf))
                    avail += pmax if _np.isfinite(pmax) else _np.inf
                else:
                    pu = (float(gmax[name].max()) if gmax is not None and name in gmax.columns
                          else float(g.get("p_max_pu", 1.0)))
                    avail += float(g.get("p_nom", 0.0)) * pu
        # storage/store discharge headroom (an upper bound on instantaneous supply)
        for comp, col in (("storage_units", "p_nom"), ("stores", "e_nom")):
            df = getattr(n, comp, None)
            if df is not None and not df.empty and col in df.columns:
                avail += float(df[col].clip(lower=0).sum())
        voll = float(getattr(config, "voll", 0.0) or 0.0)
        if _np.isfinite(avail) and peak_load > avail * (1.0 + 1e-6) and voll <= 0:
            emit(f"[INFEASIBLE]   • Peak demand ~{peak_load:,.0f} MW exceeds the maximum "
                 f"available + buildable generation ~{avail:,.0f} MW, and no Value of Lost "
                 "Load is set. Add capacity, raise an extendable p_nom_max, or set a VOLL "
                 "to price unserved demand.")
            hints += 1
    except Exception:
        pass

    # 3) Active global constraints (CO2 caps etc.) that may be binding too tight.
    try:
        gc = getattr(n, "global_constraints", None)
        if gc is not None and not gc.empty:
            for name, row in gc.iterrows():
                emit(f"[INFEASIBLE]   • Global constraint '{name}' "
                     f"({row.get('type', '?')} {row.get('sense', '')} "
                     f"{row.get('constant', '?')}) is active — if it's a CO2 / primary-"
                     "energy cap it may be infeasibly tight; relax it or check must-run "
                     "emitters.")
                hints += 1
    except Exception:
        pass

    # 4) Gurobi-only: native infeasibility report (IIS).
    try:
        if str(getattr(config, "solver_name", "")).lower() == "gurobi":
            model = getattr(n, "model", None)
            printer = getattr(model, "print_infeasibilities", None)
            if callable(printer):
                emit("[INFEASIBLE]   • Gurobi infeasibility report (constraints in the "
                     "irreducible conflict set) follows in the solver log.")
                printer()
    except Exception:
        pass

    if hints == 0:
        emit("[INFEASIBLE]   • No obvious structural cause found. Check binding capacity "
             "bounds (p_nom_min/max), reserve / storage-SoC targets, ramp limits, or a "
             "too-tight global constraint. Turning OFF presolve can make the solver report "
             "whether the model is infeasible vs unbounded.")


def _log_global_constraint_shadow_prices(network, log_queue) -> None:
    """
    On a successful solve, report each BINDING global constraint's shadow price
    (``global_constraints.mu``) as a raw ``[GLOBAL-CONSTRAINT]`` line — e.g. a CO2
    cap's mu is the implied carbon price (the marginal abatement cost at which the
    cap binds). Skips non-binding (mu ≈ 0) constraints. Read-only; mu is already
    in user-facing units.
    """
    try:
        gc = getattr(network, "global_constraints", None)
        if gc is None or gc.empty or "mu" not in gc.columns:
            return
        for name, row in gc.iterrows():
            try:
                mu = float(row.get("mu", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(mu) < 1e-6:
                continue
            ctype = str(row.get("type", ""))
            is_carbon = "co2" in str(name).lower() or "primary_energy" in ctype.lower()
            unit = "€/t" if is_carbon else "€/unit"
            try:
                log_queue.put(
                    f"[GLOBAL-CONSTRAINT] '{name}' ({ctype} {row.get('sense', '')} "
                    f"{row.get('constant', '?')}) BINDS — shadow price {mu:,.2f} {unit} "
                    "(marginal cost of tightening it by one unit)."
                )
            except Exception:
                pass
    except Exception:
        pass


def _log_curtailment_post_solve(network, sns, current_period, phase) -> None:
    """
    After each myopic iteration, log per-vintage build + curtailment so
    the user can verify the wrapper's effect end-to-end. Pushes lines via
    `phase` (= log_queue.put) — same channel as everything else the user
    sees in the Log tab.

    For every generator with curtailment_cost > 0:
      • p_nom_opt        — capacity the LP picked (MW)
      • dispatched_MWh   — Σ p × snapshot_weight × period_weight over `sns`
      • potential_MWh    — Σ p_max_pu × p_nom_opt × snapshot_weight × period_weight
      • curtailed_MWh    — potential − dispatched
      • curt_pct         — curtailed / potential × 100
      • penalty_eur      — curtailed × cc

    The penalty is what the wrapper added to the LP objective for THIS
    generator's curtailment. If you see penalty >> capex_eff × p_nom_opt,
    the LP is paying a fortune in curtailment cost but still building —
    meaning some other benefit (VOLL avoidance, OPEX savings) made it
    worth it. That's the smoking gun for VOLL > cc.
    """
    gens = getattr(network, "generators", None)
    if gens is None or "curtailment_cost" not in gens.columns:
        return
    cc_series = gens["curtailment_cost"].fillna(0.0)
    cc_gens = cc_series.index[cc_series > 0.0]
    if cc_gens.empty:
        return

    # Need solved p (per snapshot, per gen). After PyPSA's optimize call,
    # `n.generators_t.p` holds the dispatch result. If it's empty (rare —
    # e.g. solver returned ok but with no data), bail rather than emit
    # confusing zero-rows.
    p_t = getattr(network.generators_t, "p", None)
    if p_t is None or p_t.empty:
        phase("Post-solve curtailment log: dispatch table empty — skipping.")
        return

    # Weight basis must match what the wrapper used so penalty_eur lines
    # up with the LP's actual contribution: snapshot_w × period_w over
    # this iteration's `sns` only.
    try:
        sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    except Exception:
        sw = pd.Series(1.0, index=sns, dtype=float)
    is_mp = isinstance(sns, pd.MultiIndex)
    if is_mp:
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    # p_max_pu (dense, both scalar and time-varying paths handled)
    p_max_pu = network.get_switchable_as_dense("Generator", "p_max_pu")
    try:
        p_max_pu = p_max_pu.loc[sns]
    except KeyError:
        pass

    p_nom_col = "p_nom_opt" if "p_nom_opt" in gens.columns else "p_nom"
    phase(f"[CURT-POST] Period {current_period} — built capacity + curtailment:")
    for g in cc_gens:
        try:
            p_nom_opt = float(gens.at[g, p_nom_col] or 0.0)
            cc = float(cc_series.at[g])
            if g in p_t.columns:
                p_series = p_t[g].reindex(sns).fillna(0.0)
                dispatched = float((p_series * weights).sum())
            else:
                dispatched = 0.0
            if g in p_max_pu.columns:
                pmp = p_max_pu[g].reindex(sns).fillna(0.0)
                potential = float((pmp * weights).sum() * p_nom_opt)
            else:
                potential = 0.0
            curtailed = max(0.0, potential - dispatched)
            curt_pct = (curtailed / potential * 100.0) if potential > 0 else 0.0
            penalty = curtailed * cc
            phase(
                f"[CURT-POST] {g}: p_nom_opt={p_nom_opt:.1f} MW | "
                f"dispatched={dispatched/1000:.1f} GWh | "
                f"potential={potential/1000:.1f} GWh | "
                f"curtailed={curtailed/1000:.1f} GWh ({curt_pct:.1f}%) | "
                f"penalty_eur={penalty:,.0f}"
            )
        except Exception as exc:
            phase(f"[CURT-POST] {g}: diagnostic failed ({exc})")


def _log_storage_post_solve(network, sns, current_period, phase) -> None:
    """
    Per-storage diagnostic after each myopic iteration.

    The user expectation: with the curtailment-cost merit-order fix, the LP
    should build storage to absorb renewable excess. This logger surfaces:
      • p_nom_opt (MW)    — power capacity the LP picked
      • max_hours         — fixed energy/power ratio (energy_cap = p_nom × max_hours)
      • energy_MWh        — implied storage energy capacity
      • throughput_MWh    — Σ |p| × snapshot_weight × period_weight (gross
                            charge + discharge — measure of cycling)
      • equivalent_cycles — throughput / (2 × energy_MWh), so a full
                            charge-then-discharge counts as 1.0 cycle
      • mean_SoC_pct      — average state-of-charge over the period

    If storage stays at p_nom_opt=0, the LP found it uneconomic. Likely
    causes: capital_cost too high, max_hours too low (storage shifts too
    little energy), or curtailment_cost too low to justify the build.
    """
    sus = getattr(network, "storage_units", None)
    if sus is None or sus.empty:
        return
    # Only log storage that's either extendable in THIS iteration or has
    # non-zero p_nom (so frozen vintages from earlier periods also show up).
    p_nom_col = "p_nom_opt" if "p_nom_opt" in sus.columns else "p_nom"
    rows = sus[sus[p_nom_col].fillna(0) > 0.01]
    if rows.empty:
        # Surface the negative result explicitly — silence here is confusing.
        phase(
            f"[STORAGE-POST] Period {current_period}: no storage built "
            f"(all p_nom_opt < 0.01 MW). LP found storage uneconomic."
        )
        return

    sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    is_mp = isinstance(sns, pd.MultiIndex)
    if is_mp:
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    p_t = getattr(network.storage_units_t, "p", None)
    soc_t = getattr(network.storage_units_t, "state_of_charge", None)

    phase(f"[STORAGE-POST] Period {current_period} — storage build + utilization:")
    for name in rows.index:
        try:
            p_nom_opt = float(rows.at[name, p_nom_col] or 0.0)
            max_hours = float(rows.at[name, "max_hours"] or 0.0) if "max_hours" in rows.columns else 0.0
            energy_cap = p_nom_opt * max_hours
            throughput = 0.0
            mean_soc_pct = 0.0
            if p_t is not None and not p_t.empty and name in p_t.columns:
                p_series = p_t[name].reindex(sns).fillna(0.0).abs()
                throughput = float((p_series * weights).sum())
            if soc_t is not None and not soc_t.empty and name in soc_t.columns and energy_cap > 0:
                soc_series = soc_t[name].reindex(sns).fillna(0.0)
                # SoC is in MWh; convert to % of energy capacity.
                mean_soc_pct = float(soc_series.mean() / energy_cap * 100.0)
            cycles = throughput / (2 * energy_cap) if energy_cap > 0 else 0.0
            phase(
                f"[STORAGE-POST] {name}: p_nom_opt={p_nom_opt:.1f} MW | "
                f"max_hours={max_hours:.1f} h | energy_cap={energy_cap/1000:.2f} GWh | "
                f"throughput={throughput/1000:.1f} GWh | "
                f"equiv_cycles={cycles:.1f} | mean_SoC={mean_soc_pct:.1f}%"
            )
        except Exception as exc:
            phase(f"[STORAGE-POST] {name}: diagnostic failed ({exc})")


def _log_sclopf_post_solve(
    network,
    sns,
    current_period: int,
    iter_outages: list[tuple[str, str]],
    phase,
) -> None:
    """
    Post-solve N-1 contingency analysis for each SCLOPF myopic iteration.

    Reports per-outage:
      • the outaged branch
      • the WORST-CASE loading (max |post-contingency flow| / s_nom)
        across all monitored branches and snapshots in this iteration
      • the affected branch where the worst case occurs
      • whether the contingency is BINDING (worst case ≥ 99 % s_nom →
        the LP is hitting the N-1 limit there; capacity sizing was
        driven by this contingency, not by the base case)

    The LP duals on the contingency constraints themselves aren't exposed
    by PyPSA on the SCLOPF path (its `optimize_security_constrained` adds
    constraints by name but doesn't tag them for our `assign_all_duals`
    extraction). Instead we reconstruct the worst-case post-outage flow
    analytically using the same BODF (Branch Outage Distribution Factor)
    matrix PyPSA uses internally:

        post_outage_flow[affected, t] = base_flow[affected, t]
                                       + BODF[affected, outage]
                                       × base_flow[outage, t]

    Comparing |post_flow| against s_nom tells you whether the LP needed
    to back off the base case to keep the system feasible under N-1.
    """
    if not iter_outages:
        return
    import math as _math

    # `lines_t.p0` carries the base-case flow per line per snapshot. PyPSA
    # writes it after every successful solve.
    try:
        lines_p0 = getattr(network.lines_t, "p0", None)
        trafos_p0 = getattr(network.transformers_t, "p0", None)
    except Exception:
        return
    # Combine both passive-branch classes into one DataFrame indexed by
    # (component, name) so we can look up post-outage flows uniformly.
    flow_frames: list[pd.DataFrame] = []
    s_nom_map: dict[tuple[str, str], float] = {}
    for comp, attr, df_t in (
        ("Line", "lines", lines_p0),
        ("Transformer", "transformers", trafos_p0),
    ):
        static = getattr(network, attr, None)
        if df_t is None or df_t.empty or static is None or static.empty:
            continue
        # Restrict to the iteration's snapshots so the analysis lines up
        # with the LP that just solved.
        try:
            df_sns = df_t.loc[sns]
        except KeyError:
            df_sns = df_t.reindex(sns).fillna(0.0)
        s_nom_col = "s_nom_opt" if "s_nom_opt" in static.columns else "s_nom"
        for name in df_sns.columns:
            if name not in static.index:
                continue
            try:
                s_nom = float(static.at[name, s_nom_col] or 0.0)
            except (TypeError, ValueError):
                s_nom = 0.0
            if s_nom <= 0:
                continue
            s_nom_map[(comp, name)] = s_nom
        flow_frames.append(df_sns)
    if not s_nom_map:
        return
    # Compute the BODF matrix from PyPSA's sub-network helper. PyPSA's
    # `optimize_security_constrained` calls this internally — we replay it
    # here for the post-solve diagnostic. BODF[a, o] gives the fraction
    # of `o`'s pre-outage flow that ends up on `a` after `o` trips.
    try:
        bodf_by_subnet = {}
        for sn in network.sub_networks.obj:
            try:
                sn.calculate_BODF()
                # BODF is a numpy 2D array; wrap as DataFrame indexed by
                # the sub-network's branch (component, name) tuples.
                br_i = sn.branches_i()
                bodf = pd.DataFrame(sn.BODF, index=br_i, columns=br_i)
                bodf_by_subnet[sn.name] = (br_i, bodf)
            except Exception:
                continue
    except Exception:
        return
    if not bodf_by_subnet:
        return
    # For each outage candidate, find the worst affected branch loading.
    phase(f"[SCLOPF-POST] Period {current_period} — N-1 contingency analysis ({len(iter_outages)} outage(s)):")
    binding_threshold = 0.99
    for outage_comp, outage_name in iter_outages:
        # Find which sub-network owns this branch.
        owner = None
        for sn_name, (br_i, bodf) in bodf_by_subnet.items():
            if (outage_comp, outage_name) in br_i:
                owner = (sn_name, br_i, bodf)
                break
        if owner is None:
            continue
        sn_name, br_i, bodf = owner
        try:
            outage_flow = None
            for fr in flow_frames:
                if outage_name in fr.columns:
                    outage_flow = fr[outage_name].values
                    break
            if outage_flow is None:
                continue
            worst_loading = 0.0
            worst_affected = None
            for affected_key in br_i:
                ac, an = affected_key
                if (ac, an) == (outage_comp, outage_name):
                    continue  # outaged branch itself; carries 0 post-outage
                s_nom = s_nom_map.get((ac, an), 0.0)
                if s_nom <= 0:
                    continue
                base_flow = None
                for fr in flow_frames:
                    if an in fr.columns:
                        base_flow = fr[an].values
                        break
                if base_flow is None:
                    continue
                try:
                    factor = float(bodf.at[(ac, an), (outage_comp, outage_name)])
                except (KeyError, ValueError):
                    continue
                post = base_flow + factor * outage_flow
                loading = float(max(abs(p) for p in post)) / s_nom
                if loading > worst_loading:
                    worst_loading = loading
                    worst_affected = an
            binding = "BINDING" if worst_loading >= binding_threshold else "slack"
            if worst_affected is not None and _math.isfinite(worst_loading):
                phase(
                    f"[SCLOPF-POST] outage '{outage_comp}:{outage_name}' → worst affected "
                    f"'{worst_affected}' loaded {worst_loading * 100:.1f}% of s_nom ({binding})"
                )
            else:
                phase(
                    f"[SCLOPF-POST] outage '{outage_comp}:{outage_name}' → no affected branch flow available"
                )
        except Exception as exc:
            phase(f"[SCLOPF-POST] outage '{outage_comp}:{outage_name}' → analysis failed ({type(exc).__name__}: {exc})")


def _log_capacity_summary_post_solve(network, current_period, phase) -> None:
    """
    Aggregate per-carrier capacity AFTER each myopic iteration.

    Shows the total active capacity (parent + vintages, summed across
    same-carrier assets) so the user can see expansion at a glance without
    having to add up per-vintage numbers manually. Includes Generator,
    StorageUnit, and Store carriers.

    Aggregation key is `carrier` — vintages share the parent's carrier,
    so `Solar2 + Solar2@2026 + Solar2@2027 + Solar2@2028` collapse into
    one row labeled by the carrier name.
    """
    phase(f"[CAP-SUM] Period {current_period} — total active capacity by carrier:")
    for comp, attr, unit in (
        ("Generator",   "generators",    "MW"),
        ("StorageUnit", "storage_units", "MW (×max_hours = MWh energy)"),
        ("Store",       "stores",        "MWh"),
    ):
        df = getattr(network, attr, None)
        if df is None or df.empty:
            continue
        cap_col = (
            "p_nom_opt" if "p_nom_opt" in df.columns
            else "e_nom_opt" if "e_nom_opt" in df.columns
            else "p_nom" if "p_nom" in df.columns
            else "e_nom"
        )
        if cap_col not in df.columns:
            continue
        if "carrier" not in df.columns:
            continue
        # Sum capacity per carrier. Skip near-zero rows so the summary
        # doesn't list every empty asset.
        agg = (df.groupby("carrier")[cap_col].sum().sort_values(ascending=False))
        agg = agg[agg > 0.01]
        if agg.empty:
            continue
        items = ", ".join(f"{c}: {v:.1f}" for c, v in agg.items())
        phase(f"[CAP-SUM] {comp} ({unit}) — {items}")


def _log_line_post_solve(network, sns, current_period, phase) -> None:
    """
    Per-line / per-transformer diagnostic after each myopic iteration.

    Captures over-build signals on transmission infrastructure:
      • s_nom_opt           — capacity decided (MW)
      • peak_loading_pct    — max |flow| / s_nom_opt × 100 over `sns`
      • mean_loading_pct    — Σ |flow| × w / (s_nom_opt × Σ w) × 100
      • binding_hours       — count of snapshots where the capacity
                              upper-bound dual (mu_upper) is non-zero;
                              these are the only hours where adding 1 MW
                              of line capacity would have saved system
                              cost. binding_hours ≈ 0 with peak < 70% is
                              a strong over-build signal.
      • congestion_rent_eur — Σ mu × s_nom_opt × snapshot_weight × period_weight
                              over the iteration (LP value of the line's
                              capacity — what extra capacity is worth).

    Surfaces ALL line/transformer rows, including vintages, so the user
    can see per-vintage decisions (parent + Line@2026 + Line@2027 + …).
    """
    sw_full = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    if isinstance(sns, pd.MultiIndex):
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw_full * year_mul
        except Exception:
            weights = sw_full
    else:
        weights = sw_full
    total_w = float(weights.sum()) if weights.sum() > 0 else 1.0

    for comp_class, attr, t_attr in (("Line", "lines", "p0"), ("Transformer", "transformers", "p0")):
        df = getattr(network, attr, None)
        if df is None or df.empty:
            continue
        cap_col = "s_nom_opt" if "s_nom_opt" in df.columns else "s_nom"
        # Include any line with non-trivial capacity OR an extendable flag.
        # Showing zero-capacity rows is noise; if vintage_service created a
        # vintage and the LP picked p_nom_opt=0, that's the "not built" case
        # — surface ONE summary line per asset class instead.
        rows = df[df[cap_col].fillna(0) > 0.01]
        if rows.empty:
            phase(
                f"[LINE-POST] Period {current_period}: no {comp_class} with "
                f"capacity > 0.01 MW."
            )
            continue
        # PyPSA's effective capital_cost (annuitised via periodized_cost). A
        # near-zero value is the smoking gun for "LP builds lines for free".
        # PyPSA exposes this as a property on the component; falls back to
        # the raw column on older PyPSA versions.
        try:
            eff_cap_cost = getattr(getattr(network.c, attr), "capital_cost", None)
        except Exception:
            eff_cap_cost = None
        flow_df = getattr(getattr(network, f"{attr}_t"), t_attr, None)
        # Capacity-upper dual — LP shadow price on s_nom_opt for each hour.
        # Non-zero = the LP would have benefited from more line capacity.
        try:
            mu_up_df = getattr(getattr(network, f"{attr}_t"), "mu_upper", None)
            mu_lo_df = getattr(getattr(network, f"{attr}_t"), "mu_lower", None)
        except Exception:
            mu_up_df = mu_lo_df = None
        phase(f"[LINE-POST] Period {current_period} — {comp_class} build & loading:")
        for name in rows.index:
            try:
                s_nom_opt = float(rows.at[name, cap_col] or 0.0)
                s_nom_max = float(rows.at[name, "s_nom_max"] or 0.0) if "s_nom_max" in rows.columns else float("inf")
                x_val = float(rows.at[name, "x"] or 0.0) if "x" in rows.columns else 0.0
                capex_eff = (
                    float(eff_cap_cost.get(name, float("nan")))
                    if eff_cap_cost is not None else float("nan")
                )
                peak_pct = 0.0
                mean_pct = 0.0
                binding_h = 0
                congestion_eur = 0.0
                if flow_df is not None and not flow_df.empty and name in flow_df.columns:
                    flow_series = flow_df[name].reindex(sns).fillna(0.0).abs()
                    if s_nom_opt > 0:
                        peak_pct = float(flow_series.max() / s_nom_opt * 100.0)
                        mean_pct = float((flow_series * weights).sum() / (s_nom_opt * total_w) * 100.0)
                # mu_upper + mu_lower combined (binding in EITHER direction).
                for mu_df in (mu_up_df, mu_lo_df):
                    if mu_df is not None and not mu_df.empty and name in mu_df.columns:
                        mu_series = mu_df[name].reindex(sns).fillna(0.0).abs()
                        # Use a tiny tolerance — solver leaves micro-duals
                        # around zero for inactive constraints.
                        binding_h += int((mu_series > 1e-6).sum())
                        congestion_eur += float((mu_series * weights).sum() * s_nom_opt)
                (
                    f"[{s_nom_opt:.1f}/{s_nom_max:.0f}]" if s_nom_max != float("inf")
                    else f"[{s_nom_opt:.1f}]"
                )
                phase(
                    f"[LINE-POST] {name}: s_nom_opt={s_nom_opt:.1f} MW "
                    f"(max={s_nom_max:.0f}, x={x_val:.4f}, "
                    f"capex_eff={capex_eff:.0f} €/MW) | "
                    f"peak={peak_pct:.1f}% | mean={mean_pct:.1f}% | "
                    f"binding_hours={binding_h} | "
                    f"congestion_rent_eur={congestion_eur:,.0f}"
                )
            except Exception as exc:
                phase(f"[LINE-POST] {name}: diagnostic failed ({exc})")


def _log_corridor_summary_post_solve(network, sns, current_period, phase) -> None:
    """
    Aggregate per-corridor line capacity vs. peak flow.

    Groups all parallel branches between the same (bus0, bus1) pair —
    parent + every vintage — and reports:
      • total_s_nom (MW) — sum of capacities on the corridor
      • peak_flow (MW)   — max(|flow_t|) where flow is summed across branches
      • corridor_util_%  — peak_flow / total_s_nom × 100
      • total_rent (€)   — sum of congestion_rent across branches

    Surfaces "the corridor is over-built" with a single number rather than
    making the user mentally add per-vintage capacities. Low corridor_util_%
    + total_rent>0 = LP needed the capacity for some hours but average
    utilisation is poor.

    Pairs of (bus0,bus1) and (bus1,bus0) are treated as the same corridor.
    """
    sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    if isinstance(sns, pd.MultiIndex):
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    phase(f"[CORRIDOR] Period {current_period} — corridor-level capacity vs. flow:")
    for comp_class, attr, t_attr in (("Line", "lines", "p0"), ("Transformer", "transformers", "p0")):
        df = getattr(network, attr, None)
        if df is None or df.empty:
            continue
        cap_col = "s_nom_opt" if "s_nom_opt" in df.columns else "s_nom"
        flow_df = getattr(getattr(network, f"{attr}_t"), t_attr, None)
        try:
            mu_up_df = getattr(getattr(network, f"{attr}_t"), "mu_upper", None)
            mu_lo_df = getattr(getattr(network, f"{attr}_t"), "mu_lower", None)
        except Exception:
            mu_up_df = mu_lo_df = None
        # Group by unordered (bus0, bus1) — frozenset handles either direction.
        corridors: dict = {}
        for name in df.index:
            try:
                bus0 = str(df.at[name, "bus0"])
                bus1 = str(df.at[name, "bus1"])
                key = frozenset((bus0, bus1))
                corridors.setdefault(key, []).append(name)
            except Exception:
                continue
        for key, names in corridors.items():
            try:
                bus_pair = "↔".join(sorted(key))
                total_sn = sum(float(df.at[n, cap_col] or 0.0) for n in names)
                # Per-snapshot total flow = Σ |p0| across branches in this corridor.
                # Branches can carry flow in different directions; the corridor's
                # net delivered power is sum(signed flow). For "is the corridor
                # over-built?" the relevant metric is total ENERGY moved, so we
                # use abs(p0) summed across branches.
                total_flow_series = None
                if flow_df is not None and not flow_df.empty:
                    cols = [n for n in names if n in flow_df.columns]
                    if cols:
                        # Sum across branches (signed sum better captures net
                        # corridor flow than abs-then-sum — counter-flow on
                        # one branch cancels on another).
                        total_flow_series = flow_df[cols].sum(axis=1).reindex(sns).fillna(0.0).abs()
                peak_flow = float(total_flow_series.max()) if total_flow_series is not None else 0.0
                mean_flow = float((total_flow_series * weights).sum() / weights.sum()) if total_flow_series is not None and weights.sum() > 0 else 0.0
                util_pct = (peak_flow / total_sn * 100.0) if total_sn > 0 else 0.0
                # Sum congestion rent across all branches in this corridor.
                total_rent = 0.0
                for mu_df in (mu_up_df, mu_lo_df):
                    if mu_df is None or mu_df.empty:
                        continue
                    for n in names:
                        if n not in mu_df.columns:
                            continue
                        s_nom_n = float(df.at[n, cap_col] or 0.0)
                        mu_s = mu_df[n].reindex(sns).fillna(0.0).abs()
                        total_rent += float((mu_s * weights).sum() * s_nom_n)
                phase(
                    f"[CORRIDOR] {comp_class} {bus_pair} [{len(names)} branches]: "
                    f"total_s_nom={total_sn:.1f} MW | "
                    f"peak_flow={peak_flow:.1f} MW ({util_pct:.1f}%) | "
                    f"mean_flow={mean_flow:.1f} MW | "
                    f"total_rent_eur={total_rent:,.0f}"
                )
            except Exception as exc:
                phase(f"[CORRIDOR] {comp_class} corridor failed: {exc}")


def _log_bus_balance_post_solve(network, sns, current_period, phase) -> None:
    """
    Per-bus power balance over the iteration. Useful for diagnosing
    transmission bottlenecks: if bus A has surplus renewable and bus B
    has unmet load, but the line A→B is binding, the LP can't deliver.

    For each bus:
      • load_MWh    — Σ load × snapshot_weight × period_weight
      • gen_MWh     — Σ p_at_bus × snapshot_weight × period_weight
      • net_export  — gen − load (MWh through lines)

    Negative net_export = bus imports. Large absolute values relative to
    load suggest a heavily-transit'd bus.
    """
    buses = getattr(network, "buses", None)
    if buses is None or buses.empty:
        return

    sw = network.snapshot_weightings.loc[sns, "objective"].astype(float)
    if isinstance(sns, pd.MultiIndex):
        try:
            ipw = network.investment_period_weightings["objective"]
            period_lvl = sns.get_level_values(0)
            year_mul = pd.Series(
                [float(ipw.get(int(p), 1.0)) for p in period_lvl],
                index=sns, dtype=float,
            )
            weights = sw * year_mul
        except Exception:
            weights = sw
    else:
        weights = sw

    # Per-bus load
    load_per_bus: dict[str, float] = {bus: 0.0 for bus in buses.index}
    loads = network.loads
    loads_t_p = getattr(network.loads_t, "p", None)
    loads_t_pset = getattr(network.loads_t, "p_set", None)
    # Prefer solved p (matches the load actually served), fall back to p_set.
    load_frame = loads_t_p if (loads_t_p is not None and not loads_t_p.empty) else loads_t_pset
    if load_frame is not None and not load_frame.empty and not loads.empty:
        for load_name in loads.index:
            if load_name not in load_frame.columns:
                continue
            bus = str(loads.at[load_name, "bus"])
            if bus not in load_per_bus:
                continue
            series = load_frame[load_name].reindex(sns).fillna(0.0)
            load_per_bus[bus] += float((series * weights).sum())

    # Per-bus generation (renewables + thermal + storage discharge)
    gen_per_bus: dict[str, float] = {bus: 0.0 for bus in buses.index}
    for comp_attr, t_attr, sign in (
        ("generators", "p", +1),  # gen output
        ("storage_units", "p", +1),  # storage net (>0 discharge, <0 charge)
        ("stores", "p", +1),
    ):
        df = getattr(network, comp_attr, None)
        if df is None or df.empty:
            continue
        t_df = getattr(getattr(network, f"{comp_attr}_t"), t_attr, None)
        if t_df is None or t_df.empty:
            continue
        for asset in df.index:
            if asset not in t_df.columns:
                continue
            bus = str(df.at[asset, "bus"])
            if bus not in gen_per_bus:
                continue
            series = t_df[asset].reindex(sns).fillna(0.0)
            gen_per_bus[bus] += sign * float((series * weights).sum())

    phase(f"[BUS-BAL] Period {current_period} — per-bus energy balance (GWh):")
    for bus in buses.index:
        load_mwh = load_per_bus.get(bus, 0.0)
        gen_mwh = gen_per_bus.get(bus, 0.0)
        net = gen_mwh - load_mwh  # > 0 export, < 0 import
        phase(
            f"[BUS-BAL] {bus}: load={load_mwh/1000:.1f} | "
            f"gen={gen_mwh/1000:.1f} | net_export={net/1000:+.1f}"
        )


def _log_cost_decomposition_post_solve(network, cfg, sns, current_period, phase) -> None:
    """
    Decompose the LP objective by period × cost category.

    Answers the question "why did the LP build capacity in period X instead
    of Y" by surfacing:

      • Per-period total OPEX-variable (Σ generator dispatch × marginal_cost)
      • Per-period CO2 surcharge (Σ emitter dispatch × CO2_intensity / eff
        × per-period CO2 price), separated from the base OPEX so the user
        can see how much the LP is "paying" for CO2 in each period.
      • Per-period new CAPEX added (assets with build_year=P, capital_cost
        × Δp_nom_opt). Reveals the LP's chronological investment plan.
      • Per-period dispatch by carrier — the carrier-level GWh feeding into
        the OPEX-CO2 split above.

    PLUS a one-shot "marginal capacity value" table at the end:
      • For each extendable asset, the LP dual on its p_nom_max constraint
        (= the EUR/MW the LP would have paid for one more MW of headroom).
      • Helps diagnose "the LP wanted more in 2027 but ran into p_nom_max"
        vs. "the LP didn't want more — the cap wasn't binding".

    Called once per myopic iteration (passing the iteration's sns and the
    current period). On the full-horizon path, called once with all periods.
    """
    import math as _math


    gens = getattr(network, "generators", None)
    if gens is None or gens.empty:
        return

    is_multi = isinstance(sns, pd.MultiIndex)

    # Per-snapshot effective weight = snapshot.objective × period.years.
    # This is the multiplier the LP sees for any cost term aggregated over
    # snapshots; matches how cost_breakdown / asset_economics report €.
    # `sns` is the LP's ACTUAL snapshot span, which for myopic foresight and
    # SCLOPF is a subset of n.snapshots — pass it explicitly so the weights
    # cover the same rows the objective did.
    weights = _period_utils.snapshot_weights(network, "objective", sns=sns)

    # Per-period bucket structure — periods are level-0 of the MultiIndex
    # or the single sentinel "ALL" on flat horizons.
    if is_multi:
        periods_seen = list(dict.fromkeys(int(p) for p in sns.get_level_values(0)))
    else:
        periods_seen = ["ALL"]
    period_data: dict = {
        p: {"opex_var_mc0": 0.0, "co2_surcharge": 0.0, "curt_penalty": 0.0,
            "voll_shed_cost": 0.0, "voll_shed_mwh": 0.0,
            "new_capex": 0.0,
            "co2_emitted_t": 0.0,
            "by_carrier_mwh": {}}
        for p in periods_seen
    }

    # ── Per-period dispatch + OPEX split ────────────────────────────────
    # User-typed marginal_cost (NOT the LP-modified one). The CO2 surcharge
    # is the gap between the LP-applied marginal_cost (potentially
    # time-varying after our co2_price_per_period wrapper) and the user's
    # typed scalar. Compute both:
    p_t = getattr(network.generators_t, "p", None)
    getattr(network.generators_t, "marginal_cost", None)
    co2_by_carrier = {}
    try:
        if "co2_emissions" in network.carriers.columns:
            co2_by_carrier = {
                str(k).lower(): float(v)
                for k, v in network.carriers["co2_emissions"].items()
                if isinstance(v, (int, float)) and _math.isfinite(v)
            }
    except Exception:
        pass

    def _per_period_split(series, weights):
        """Group a per-snapshot series by period, returning {period → sum}."""
        weighted = (series.reindex(sns).fillna(0.0) * weights)
        if is_multi:
            return {int(p): float(v) for p, v in weighted.groupby(sns.get_level_values(0)).sum().items()}
        return {"ALL": float(weighted.sum())}

    if p_t is not None and not p_t.empty:
        for g in p_t.columns:
            if g not in gens.index:
                continue
            try:
                p_series = p_t[g].reindex(sns).fillna(0.0)
            except Exception:
                continue
            # User-typed marginal_cost (scalar). The wrapper restores this
            # after solve so what's on n.generators is the user's value.
            mc_scalar = float(gens.at[g, "marginal_cost"]) if "marginal_cost" in gens.columns else 0.0
            # LP-effective marginal_cost (time-varying if our wrapper wrote
            # to generators_t.marginal_cost during solve, but those get
            # cleared in restore — so this typically equals the scalar).
            # For the surcharge, we compute what CO2 cost WOULD HAVE been
            # applied analytically from cfg.co2_price_per_period.
            mwh_per_period = _per_period_split(p_series, weights)
            opex0_per_period = _per_period_split(p_series * mc_scalar, weights)
            carrier = str(gens.at[g, "carrier"]).lower() if "carrier" in gens.columns else ""
            eff = float(gens.at[g, "efficiency"]) if "efficiency" in gens.columns else 1.0
            if not _math.isfinite(eff) or eff <= 0:
                eff = 1.0
            intensity = co2_by_carrier.get(carrier, 0.0)
            for p, m in mwh_per_period.items():
                period_data.setdefault(p, {
                    "opex_var_mc0": 0.0, "co2_surcharge": 0.0,
                    "curt_penalty": 0.0, "voll_shed_cost": 0.0,
                    "voll_shed_mwh": 0.0, "new_capex": 0.0,
                    "co2_emitted_t": 0.0, "by_carrier_mwh": {}})
                period_data[p]["by_carrier_mwh"][carrier] = (
                    period_data[p]["by_carrier_mwh"].get(carrier, 0.0) + m
                )
            for p, e in opex0_per_period.items():
                period_data[p]["opex_var_mc0"] += e
            # CO2 emitted + per-period surcharge from cfg.co2_price_per_period.
            if intensity > 0:
                out_intensity = intensity / eff
                tco2_per_period = _per_period_split(p_series * out_intensity, weights)
                for p, t_co2 in tco2_per_period.items():
                    period_data[p]["co2_emitted_t"] += t_co2
                # CO2 surcharge = tCO2_per_period × co2_price_per_period[p]
                pp = getattr(cfg, "co2_price_per_period", None) or {}
                co2_scalar = getattr(cfg, "co2_price", 0.0) or 0.0
                for p, t_co2 in tco2_per_period.items():
                    try:
                        price = float(pp.get(str(int(p)), co2_scalar)) if p != "ALL" else float(co2_scalar)
                    except (TypeError, ValueError):
                        price = float(co2_scalar)
                    period_data[p]["co2_surcharge"] += t_co2 * price
            # VOLL load shedding — special-cased: carrier="load_shedding".
            if carrier == "load_shedding":
                mwh_p = mwh_per_period
                cost_p = _per_period_split(p_series * mc_scalar, weights)
                for p in mwh_p:
                    period_data[p]["voll_shed_mwh"] += mwh_p[p]
                    period_data[p]["voll_shed_cost"] += cost_p[p]

    # ── New CAPEX by build_year ─────────────────────────────────────────
    # Capital expenditure for assets newly built (Δp_nom_opt > 0) and built
    # in the iteration's current period (or any period for "ALL"). Mirrors
    # what cost_breakdown computes but split by build_year so the user sees
    # the LP's chronological allocation.
    for comp_attr, nom in (
        ("generators",    "p_nom"),
        ("storage_units", "p_nom"),
        ("stores",        "e_nom"),
        ("links",         "p_nom"),
        ("lines",         "s_nom"),
        ("transformers",  "s_nom"),
    ):
        df = getattr(network, comp_attr, None)
        if df is None or df.empty:
            continue
        if "build_year" not in df.columns:
            continue
        if f"{nom}_opt" not in df.columns:
            continue
        for name in df.index:
            try:
                by_raw = float(df.at[name, "build_year"])
                if not _math.isfinite(by_raw):
                    continue
                by_int = int(by_raw)
                if not (1900 <= by_int <= 2200):
                    continue
            except (TypeError, ValueError):
                continue
            if by_int not in period_data and "ALL" not in period_data:
                continue
            target_key = by_int if by_int in period_data else "ALL"
            try:
                opt = float(df.at[name, f"{nom}_opt"])
                ini = float(df.at[name, nom]) if nom in df.columns else 0.0
                cc = float(df.at[name, "capital_cost"]) if "capital_cost" in df.columns else 0.0
            except (TypeError, ValueError):
                continue
            # When capital_cost is 0 / NaN, fall back to annuitising
            # overnight_cost — mirrors PyPSA's periodized_cost(overnight_cost,
            # discount_rate, lifetime) so the diagnostic captures the LP's
            # actual investment signal for assets configured via overnight_cost
            # (the typical GUI setup, where capital_cost is left at 0 and the
            # solver derives the annuity at run time).
            if (not _math.isfinite(cc) or cc <= 0) and "overnight_cost" in df.columns:
                try:
                    oc = float(df.at[name, "overnight_cost"])
                except (TypeError, ValueError):
                    oc = 0.0
                if _math.isfinite(oc) and oc > 0:
                    dr = float(getattr(cfg, "discount_rate", 0.0) or 0.0)
                    if "discount_rate" in df.columns:
                        try:
                            dr_asset = float(df.at[name, "discount_rate"])
                            if _math.isfinite(dr_asset):
                                dr = dr_asset
                        except (TypeError, ValueError):
                            pass
                    lt = float(getattr(cfg, "default_lifetime", 25.0) or 25.0)
                    if "lifetime" in df.columns:
                        try:
                            lt_asset = float(df.at[name, "lifetime"])
                            if _math.isfinite(lt_asset) and lt_asset > 0:
                                lt = lt_asset
                        except (TypeError, ValueError):
                            pass
                    cc = oc * _annuity(dr, lt) if lt > 0 else 0.0
            delta = max(0.0, opt - ini)
            if delta <= 1e-6 or cc <= 0:
                continue
            period_data[target_key]["new_capex"] += cc * delta

    # ── Marginal capacity value (LP duals on p_nom_max) ─────────────────
    # Read from n.model.dual["{Class}-ext-p_nom-upper"] when assign_all_duals
    # was set (default in our run_simulation). The dual is € per relaxing
    # the upper bound by 1 MW — i.e., the LP's "willingness to pay" for one
    # more MW of capacity. Positive ⇒ the LP wanted more but the cap held;
    # 0 ⇒ the cap wasn't binding.
    marginal_caps: list[tuple[str, str, float, float]] = []  # (comp_class, name, p_nom_opt, mu)
    model = getattr(network, "model", None)
    if model is not None:
        for comp_class, df_attr in (
            ("Generator",    "generators"),
            ("StorageUnit",  "storage_units"),
            ("Store",        "stores"),
            ("Link",         "links"),
            ("Line",         "lines"),
            ("Transformer",  "transformers"),
        ):
            df = getattr(network, df_attr, None)
            if df is None or df.empty:
                continue
            nom_col = "e_nom" if comp_class == "Store" else (
                "s_nom" if comp_class in ("Line", "Transformer") else "p_nom"
            )
            if f"{nom_col}_opt" not in df.columns:
                continue
            try:
                dual_key = f"{comp_class}-ext-{nom_col}-upper"
                dual = model.dual.get(dual_key, None)
            except Exception:
                dual = None
            if dual is None:
                continue
            try:
                dual_dict = {str(k): float(v) for k, v in dual.to_pandas().items()}
            except Exception:
                try:
                    dual_dict = {str(k): float(v) for k, v in dual.items()}
                except Exception:
                    continue
            for name, mu in dual_dict.items():
                if not _math.isfinite(mu) or abs(mu) < 1e-3:
                    continue
                try:
                    p_opt = float(df.at[name, f"{nom_col}_opt"])
                except (KeyError, TypeError, ValueError):
                    p_opt = 0.0
                marginal_caps.append((comp_class, name, p_opt, mu))

    # ── Emit log ────────────────────────────────────────────────────────
    phase(f"[COST-DECOMP] Period {current_period} — investment-vs-OPEX breakdown:")
    for p in sorted([k for k in period_data.keys() if k != "ALL"],
                    key=lambda x: int(x) if isinstance(x, int) else 99999) + (
            ["ALL"] if "ALL" in period_data else []):
        d = period_data[p]
        total = d["new_capex"] + d["opex_var_mc0"] + d["co2_surcharge"] + d["voll_shed_cost"]
        # Highlight the dominant cost component so user can scan visually.
        components = [
            ("CAPEX(new)", d["new_capex"]),
            ("OPEX(mc)",   d["opex_var_mc0"]),
            ("CO2 surcharge", d["co2_surcharge"]),
            ("VOLL shed",  d["voll_shed_cost"]),
        ]
        dominant = max(components, key=lambda x: abs(x[1]))[0]
        carrier_str = ", ".join(
            f"{c}={m/1000:.1f} GWh" for c, m in
            sorted(d["by_carrier_mwh"].items(), key=lambda x: -abs(x[1]))[:6]
        )
        phase(
            f"[COST-DECOMP] {p}: new_CAPEX={d['new_capex']/1e6:.2f}M€ | "
            f"OPEX(mc)={d['opex_var_mc0']/1e6:.2f}M€ | "
            f"CO2_surcharge={d['co2_surcharge']/1e6:.2f}M€ "
            f"({d['co2_emitted_t']/1e3:.1f} kt) | "
            f"VOLL_shed={d['voll_shed_cost']/1e6:.2f}M€ ({d['voll_shed_mwh']:.1f} MWh) | "
            f"dispatch: {carrier_str} | "
            f"dominant={dominant}, total={total/1e6:.2f}M€"
        )

    # Marginal capacity value table (compact — only assets with non-zero dual).
    if marginal_caps:
        phase(f"[COST-DECOMP] Period {current_period} — marginal capacity value (LP dual on p_nom_max):")
        # Top 10 by magnitude — these are the assets where the cap was binding.
        for cls, name, p_opt, mu in sorted(marginal_caps, key=lambda x: -abs(x[3]))[:10]:
            phase(
                f"[COST-DECOMP]   {cls} '{name}' @ p_nom_opt={p_opt:.1f} MW: "
                f"μ(p_nom_max)={mu:.2f} €/MW — "
                f"{'CAP BINDING (would build more)' if mu > 1e-3 else 'cap inactive'}"
            )
    else:
        phase(
            f"[COST-DECOMP] Period {current_period} — no extendable assets had a "
            "non-zero p_nom_max dual (every cap had headroom; LP didn't want more)."
        )


def _emit_core_post_solve_diagnostics(network, sns, current_period, phase) -> None:
    """
    The SIX per-solve diagnostic loggers shared by the full-horizon and myopic
    paths: curtailment / storage / line / corridor / capacity / bus-balance.

    Deliberately BARE (no try/except) so each caller keeps its OWN error policy
    — the divergence is intentional, not accidental:
      • full-horizon (`run_simulation`) wraps this in a swallow-all try/except:
        diagnostics are best-effort inspectability on a completed joint LP and
        must not fail the solve.
      • myopic (`_run_myopic_foresight`) calls it BARE: a diagnostic failure in a
        rolling iteration should SURFACE (propagate → abort that iteration),
        because a broken diagnostic there usually means the iteration's network
        state is itself malformed and the next iteration would compound it.

    Path-specific extras stay at the call site: full-horizon also emits
    `_log_cost_decomposition_post_solve` + `_log_global_constraint_shadow_prices`
    (the joint-LP global-constraint duals); myopic also emits the conditional
    `_log_sclopf_post_solve` (per-iteration contingencies) + cost-decomposition.
    """
    _log_curtailment_post_solve(network, sns, current_period, phase)
    _log_storage_post_solve(network, sns, current_period, phase)
    _log_line_post_solve(network, sns, current_period, phase)
    _log_corridor_summary_post_solve(network, sns, current_period, phase)
    _log_capacity_summary_post_solve(network, current_period, phase)
    _log_bus_balance_post_solve(network, sns, current_period, phase)
