# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 16 (design §9.3 "eh_concentrator_staged"; GK A (28) GO,
Q1-Q6): the gate facts of the mining concentrator run, registered in
`services.run_gates.FACTS` by name.

- `process.mass_balance`: the tonne buses' nodal residual of the run's own
  solve, per period, against the tonnes fed in from outside the site (Q1).
  The LP enforces nodal balance, so this catches result corruption (stale or
  rewritten frames, a reader that misses a component), never a design flaw.
- `process.motor_start`: the PoC voltage change of each process unit's
  motor start, from the scorecard's own rows (`grid_readiness._motor_start_rows`),
  never recomputed (Q2).
- `process.restart`: the restart after a grid trip (`gridspine` restart
  sequence) of the units the demo dip classes trip, each motor start screened
  on the same PoC source as the motor-start rows (Q3).
- `process.n_1.site_transformer`: the site transformers whose forced outage
  stops a process unit (Q4); no site transformer is no evidence.
- `ic.counterfactual.basis`: the investment case's counterfactual basis
  (`site_today` or `analytic`) when the case is established (Q5).
- `process.lost_production`, `process.shed_level`: the KPIs (Q6) read from
  the run's own solve.
"""

from __future__ import annotations

import math

from services import run_gates as G

TONNE = "t"
MASS_BALANCE_BASIS = (
    "The tonnes on every tonne bus balance in every period: in = out, to 1e-6 of the tonnes fed in from outside "
    "the site. The LP enforces nodal balance, so this catches corrupt results (stale or rewritten frames, a reader "
    "that misses a component); it is not a check of the design."
)
NO_SITE_TRANSFORMER = (
    "the project has no site transformer (an electricity link that feeds the process units, not the grid import), "
    "so N-1 on it is not evaluated"
)
SHED = "concentrate_shed"


# ── mass balance (Q1) ────────────────────────────────────────────────────────


def _signed(frame, names, sign, index):
    """Σ sign·p of `names` per snapshot, or None when a column is missing or a value not finite."""
    total = [0.0] * len(index)
    for name in names:
        if name not in frame.columns:
            return None
        col = [float(v) for v in frame[name].reindex(index)]
        if not all(math.isfinite(v) for v in col):
            return None
        s = float(sign.get(name, 1.0)) if sign is not None else 1.0
        total = [t + s * v for t, v in zip(total, col)]
    return total


def _link_ports(n):
    """(port index, the bus column) of every Link port the network has: bus0, bus1, bus2, …"""
    ports = []
    for col in n.links.columns:
        if col.startswith("bus") and col[3:].isdigit():
            ports.append((int(col[3:]), col))
    return sorted(ports)


def bus_terms(n, bus, index):
    """The named in/out terms on `bus` per snapshot (`{label: [values]}`, injection positive), or None when a
    flow the bus carries is unreadable. One-ports by their `sign`; a Link at every port by −p_i."""
    terms = {}
    for comp, static, dynamic in (
        ("Generator", n.generators, n.generators_t.p),
        ("StorageUnit", n.storage_units, n.storage_units_t.p),
        ("Store", n.stores, n.stores_t.p),
        ("Load", n.loads, n.loads_t.p),
    ):
        names = [str(x) for x in static.index[static["bus"] == bus]]
        for name in names:
            values = _signed(
                dynamic,
                [name],
                static["sign"] if "sign" in static.columns else None,
                index,
            )
            if values is None:
                return None
            terms[f"{comp} {name}"] = values
    links_t = n.links_t
    for i, col in _link_ports(n):
        names = [str(x) for x in n.links.index[n.links[col].astype(str) == bus]]
        frame = getattr(links_t, f"p{i}", None)
        for name in names:
            if frame is None:
                return None
            values = _signed(frame, [name], None, index)
            if values is None:
                return None
            terms[f"Link {name} (port {i})"] = [-v for v in values]
    return terms


def _tonne_buses(n):
    from services.process_products import bus_unit

    return [str(b) for b in n.buses.index if bus_unit(n, str(b)) == TONNE]


def _fed_in(n, buses, index):
    """The tonnes fed in from outside the site per snapshot: the supply Generators on tonne buses. A shortfall
    role's slack and any LP slack Generator (`slack_generator_mask`, #1461 S1) are not a supply. None when one
    is unreadable."""
    from models.process_unit import product_role_generators
    from services.adequacy.slack import slack_generator_mask

    slack = set(product_role_generators(n, "shortfall"))
    slack |= {str(g) for g in n.generators.index[slack_generator_mask(n.generators)]}
    names = [
        str(g)
        for g in n.generators.index[n.generators["bus"].astype(str).isin(buses)]
        if str(g) not in slack
    ]
    return _signed(n.generators_t.p, names, None, index)


def mass_balance_of(n):
    """`Detailed(worst residual share, rows)` over the tonne buses and periods of a solved `n`, or NoEvidence.
    Each row: the bus, the period, Σ w·|Σ terms| and its share of the period's fed-in tonnes Σ w·feed."""
    index = n.snapshots
    buses = _tonne_buses(n)
    if not buses:
        return G.NoEvidence("no_tonne_bus", "the network has no bus counted in tonnes")
    weights = n.snapshot_weightings["generators"].reindex(index)
    w_all = [float(v) for v in weights]
    if not all(math.isfinite(v) and v >= 0 for v in w_all):
        return G.UNREADABLE_WEIGHTS
    feed = _fed_in(n, buses, index)
    if feed is None:
        return G.UNREADABLE_DISPATCH
    per_bus = {}
    for bus in buses:
        terms = bus_terms(n, bus, index)
        if terms is None:
            return G.UNREADABLE_DISPATCH
        per_bus[bus] = [
            math.fsum(v[k] for v in terms.values()) for k in range(len(index))
        ]
    periods = (
        list(index.get_level_values(0).unique())
        if len(n.investment_periods)
        else [None]
    )
    rows = []
    for period in periods:
        pick = (
            [k for k, p in enumerate(index.get_level_values(0)) if p == period]
            if period is not None
            else list(range(len(index)))
        )
        fed = math.fsum(w_all[k] * feed[k] for k in pick)
        if (
            not fed > 0
        ):  # Q1 (c): a period with nothing fed in is no evidence, never a pass
            label = "the whole horizon" if period is None else f"period {period}"
            return G.NoEvidence(
                "nothing_fed_in",
                f"no tonnes are fed in from outside the site in {label}, so the "
                "mass balance has no scale",
            )
        for bus in buses:
            residual = math.fsum(w_all[k] * abs(per_bus[bus][k]) for k in pick)
            rows.append(
                {
                    "bus": bus,
                    "period": None if period is None else int(period),
                    "residual_t": residual,
                    "share": residual / fed,
                    "fed_in_t": fed,
                    "assumed": False,
                }
            )
    return G._worst(rows, "share")


def _mass_balance(env):
    from harness.calculations import solve_matches_inputs

    def read(ctx):
        if not solve_matches_inputs(ctx):
            return G.STALE_SOLVE
        return mass_balance_of(ctx.network)

    return G._own_solve(env, read, typed=True)


# ── motor start and restart (Q2, Q3) ─────────────────────────────────────────


def _scorecard_inputs(env, n):
    """The scorecard's inputs for the run's project on `n` (the live network), held to the project's campus
    grid-code profile (its saved choice, else the default), as `grid_readiness_for` reads them. Saved settings
    it cannot read (not JSON, not an object, a profile that is not a name) or a grid code that does not load
    are no evidence, never the default (#1384 N2b)."""
    import json

    from services import campus_electrical_service as ce
    from services import project_registry
    from services.adequacy import grid_readiness as gr

    d = project_registry.project_dir(env.project)
    settings = d / ce.SUBDIR / ce.SETTINGS_FILE
    try:
        saved = json.loads(settings.read_text()) if settings.is_file() else {}
    except (OSError, ValueError):
        saved = None
    chosen = saved.get("profile") if isinstance(saved, dict) else None
    if not isinstance(saved, dict) or not (chosen is None or isinstance(chosen, str)):
        return G.NoEvidence(
            "grid_code_unreadable",
            f"the project's campus settings ({ce.SETTINGS_FILE}) cannot be read",
        )
    profile_id = chosen or ce.DEFAULTS["profile"]
    profile, error = gr._load_profile(
        profile_id, (d / ce.SUBDIR / ce.GRID_CODES_SUBDIR,)
    )
    if error:
        return G.NoEvidence(
            "grid_code_unreadable",
            f"the grid code {profile_id} does not load: {error}",
        )
    return gr._Inputs(d, n, profile, error, None, None)


def _screened(env, of):
    """`of(inputs)` on the run's live network, or the inputs' NoEvidence."""

    def read(n):
        inp = _scorecard_inputs(env, n)
        return inp if isinstance(inp, G.NoEvidence) else of(inp)

    return _on_network(env, read)


def _on_network(env, read):
    """`read(n)` on the run's live network under the network guard; NoEvidence with no project."""
    from harness.durable import network_guard

    if env.ctx is None or env.project is None:
        return G.NoEvidence("not_run", "the run has no project")

    def guarded():
        with network_guard():
            return read(env.ctx.network)

    return G._on(env.ctx, guarded)


def motor_start_of(inp):
    """Q2: `Detailed("pass" | "fail", rows)` from the scorecard's own motor-start rows; a fail names each
    failing unit's ΔU against the limit. An unknown row with no fail is no evidence."""
    from services.adequacy import grid_readiness as gr

    rows = gr._motor_start_rows(inp)
    detail, fails, unknown = [], 0, []
    for r in rows:
        ms = r.get("motor_start") or {}
        name = r["id"].split(":", 1)[1] if ":" in r["id"] else r["id"]
        if r["verdict"] == "pass":
            detail.append(
                f"{name}: ΔU {r['project_value']:.2f} % ≤ {r['limit']:g} % ({ms.get('start_method')}): PASS"
            )
        elif (
            r["verdict"] == gr.RULE_MOTOR_START["otherwise"]
            and r["project_value"] is not None
        ):
            fails += 1
            detail.append(
                f"{name}: ΔU {r['project_value']:.2f} % > {r['limit']:g} % ({ms.get('start_method')}): FAIL"
            )
        else:
            unknown.append(f"{name}: {r.get('note') or 'not computed'}")
    if fails:
        return G.Detailed(
            "fail",
            detail + unknown,
            "the voltage change at the PoC of each process unit's motor "
            "start, against the grid code's rapid-voltage-change limit",
        )
    if unknown:
        return G.NoEvidence("motor_start_unknown", "; ".join(unknown))
    return G.Detailed("pass", detail)


def _motor_start(env):
    return _screened(env, motor_start_of)


def restart_of(inp):
    """Q3: `Detailed(fail count, rows)` of the restart after a grid trip of the units the dip classes trip
    (`ride_through.trip_table`, as the campus run's screen), each motor start screened on the PoC source of the
    motor-start rows. An unknown unit with no fail is no evidence."""
    from gridspine.drivers.process_chain import restart_units
    from gridspine.static import motor_start as ms
    from gridspine.static import restart_sequence as rs
    from gridspine.static import ride_through as rt

    from services.adequacy import grid_readiness as gr

    n = inp.n
    units = restart_units(n)
    if units.empty:
        return G.NoEvidence(
            "no_process_unit", "the network has no process unit to restart"
        )
    trips = rt.trip_table(units)
    tripped = set(trips.loc[trips["verdict"].isin(("trips", "unknown")), "unit"])
    if not tripped:
        return G.Detailed(0, ["no process unit trips on the dip classes"])
    limit, _ = ms.limit_from_profile(inp.profile)
    sk_min, vn = gr._poc_sk_min(inp)
    source = ms.Source(
        sk_mva=gr._poc_sk_mva(n),
        sk_min_mva=sk_min,
        vn_kv=vn if vn is not None else gr._poc_vn_kv(n),
    )
    q_gfm, _ = gr._q_gfm_mvar(inp)
    table, _, total = rs.restart_sequence(
        units[units["unit"].isin(tripped)],
        "trip",
        poc_source=source,
        q_gfm_mvar=q_gfm,
        limit_pct=limit,
    )
    detail = [
        f"{r['unit']}: motor start {r['start_verdict']}, restart {r['verdict']}; {r['note']}"
        for r in table.to_dict("records")
    ]
    detail.append(
        f"lost while restarting: {total['lost_t']:.1f} t"
        + (" (a lower bound)" if total["lower_bound"] else "")
    )
    fails = int(total["fail_count"])
    if not fails and int(total["unknown_count"]):
        return G.NoEvidence("restart_unknown", "; ".join(detail))
    return G.Detailed(
        fails,
        detail,
        "the units that fail to restart after a grid trip (each motor start on the "
        "PoC against the rapid-voltage-change limit)",
    )


def _restart(env):
    return _screened(env, restart_of)


# ── N-1 on the site transformer (Q4) ─────────────────────────────────────────


def n_1_site_transformer_of(n):
    """Q4: `Detailed([transformers whose outage stops a process unit], rows)`; no site transformer is no
    evidence. The topology screen of `process_loss.units_cut_off`, first order."""
    from services.adequacy import process_loss as PL
    from services.results.grid_risk_production import _site_transformers

    transformers = _site_transformers(n)
    if not transformers:
        return G.NoEvidence("no_site_transformer", NO_SITE_TRANSFORMER)
    failing, detail = [], []
    for link in transformers:
        stopped = PL.units_cut_off(n, link)
        if stopped:
            failing.append(link)
            detail.append(
                f"{link} out of service stops {', '.join(sorted(map(str, stopped)))}"
            )
        else:
            detail.append(f"{link} out of service stops no process unit")
    return G.Detailed(sorted(failing), detail)


def _n_1_site_transformer(env):
    return _on_network(env, n_1_site_transformer_of)


# ── the investment case's basis (Q5) ─────────────────────────────────────────


def _ic_counterfactual_basis(env):
    """`site_today` or `analytic` when the case is established; else no evidence, as `ic.counterfactual`."""
    from services import ic_counterfactual as ic

    fact = ic.read(env.ctx, lambda fn: G._on(env.ctx, fn))
    if fact["state"] == ic.ESTABLISHED:
        return fact["basis"]
    return G.NoEvidence(fact["state"], ic.reason_words(fact))


# ── KPIs (Q6) ────────────────────────────────────────────────────────────────


def _peak(rows, field):
    """A KPI's per-period rows as `Detailed`: the value is the largest `field`, every row is shown (a warn rule
    reads it as established, never scored against a limit)."""
    return G.Detailed(max(r[field] for r in rows), rows)


def lost_production_of(n, lost_load):
    """`Detailed(worst period's lost tonnes per year, rows)` from the grid-risk section's `connection` line
    (the LP's shortfall, `grid_risk_production`); a missing line is no evidence."""
    from services.results.grid_risk_production import grid_risk_production

    out = grid_risk_production(n, lost_load)
    if out is None:
        return G.NoEvidence("no_process_unit", "the network has no process unit")
    line = next(x for x in out["lines"] if x["key"] == "connection")
    if line["status"] == "missing":
        return G.NoEvidence(
            "lost_production_missing",
            "; ".join(line["missing"]) or "the lost production is not readable",
        )
    rows = [
        {
            "period": k,
            "lost_t_per_year": c["t_per_year"],
            "lost_eur_per_year": c["eur_per_year"],
        }
        for k, c in line["by_period"].items()
    ]
    if any(r["lost_t_per_year"] is None for r in rows):
        return G.NoEvidence(
            "lost_production_missing", "a period's lost tonnes are not readable"
        )
    return _peak(rows, "lost_t_per_year")


def _lost_production(env):
    from harness.calculations import solve_matches_inputs

    def read(ctx):
        if not solve_matches_inputs(ctx):
            return G.STALE_SOLVE
        return lost_production_of(
            ctx.network, (ctx.solver_state or {}).get("last_lost_load")
        )

    return G._own_solve(env, read, typed=True)


def shed_level_of(n, store=SHED):
    """`Detailed(peak level in t, rows)`: the concentrate shed's level per period (least, peak, last)."""
    if store not in n.stores.index or store not in n.stores_t.e.columns:
        return G.NoEvidence(
            "no_shed", f"the network has no store {store} with a solved level"
        )
    e = n.stores_t.e[store]
    if not all(math.isfinite(float(v)) for v in e):
        return G.UNREADABLE_DISPATCH
    periods = (
        list(e.index.get_level_values(0).unique())
        if len(n.investment_periods)
        else [None]
    )
    rows = []
    for period in periods:
        s = e.loc[period] if period is not None else e
        rows.append(
            {
                "period": None if period is None else int(period),
                "least_t": float(s.min()),
                "peak_t": float(s.max()),
                "last_t": float(s.iloc[-1]),
            }
        )
    return _peak(rows, "peak_t")


def _shed_level(env):
    from harness.calculations import solve_matches_inputs

    def read(ctx):
        if not solve_matches_inputs(ctx):
            return G.STALE_SOLVE
        return shed_level_of(ctx.network)

    return G._own_solve(env, read, typed=True)
