# Phase 8 — the firm-capacity (planning reserve margin) constraint (plan, v1)

## Why this, and why now

Phase 7 shipped a loop that tunes the ENS cap until the plan meets an MC-LOLE
target, and its **first live run reported `unreachable` in two solves** — on a
fixture where the LP sheds nothing at any cap because 200 MW of firm capacity
covers a 150 MW load and the LP models no outages at all. `ens_mwh = 0` and
`binding = "voll"` at every ε. The verdict now names that case honestly and
tells the user what would move the number:

> "What would move this number is firm capacity the LP sees no deterministic
> reason to build — a planning reserve margin, or the candidate unit itself.
> Capping harder will not."

**The tool diagnoses a problem it gives the user no lever to fix.** This phase
supplies the lever: a reserve-margin constraint the LP honours, which the
Phase-7 controller can then tune INSTEAD of ε.

This is the coupling the Phase-6 decision record listed as candidate (ii) and
deferred — but deferred *specifically in its ELCC-weighted form*, because ELCC
depends on the mix (a fixed point on a non-convex quantity) and v1 computed
ELCC only for named existing assets. A **fixed-derating** PRM has neither
problem: the derating factors come from data the assets already carry, are
constants at LP build time, and make the constraint linear. ELCC-weighting
stays a later refinement, and this plan records the seam for it.

## 1. The constraint

For each investment period P:

```
Σ_g  d_g · P_g   ≥   (1 + m) · peak_P
```

- **`P_g`** — the LP's capacity for generator g: the `Generator-p_nom`
  variable when g is extendable, otherwise the fixed `p_nom` as a constant on
  the left (the capex-budget and curtailment wrappers already split
  extendable/fixed exactly this way; mirror them rather than inventing a
  third convention).
- **`d_g`** — the derating factor, `1 − q_g`, where `q_g` is the unavailability
  the adequacy stack already resolves (`occurrence.resolve_outage_params`, the
  same chain the COPT and the MC use, so the constraint and the engines can
  never disagree about a unit's availability). A generator with **no**
  resolvable occurrence data is must-take (VRE) — see §2.
- **`peak_P`** — the maximum over P's snapshots of electrical demand,
  computed INSIDE the callback at optimize time from `n.loads`/`n.loads_t.p_set`
  restricted to electrical buses, exactly as `_wrap_with_ens_cap` computes its
  demand denominator (load scalers already applied; the target and the Results
  tab cannot disagree on scope).
- **`m`** — `reserve_margin` on `SolverConfig`, a fraction (0.15 = 15 %).
  Unset/None/≤ 0 ⇒ the wrapper returns `user_fn` unchanged, exactly like the
  ENS cap's no-target path.

Membership (electrical bus, non-slack) uses the SAME classifiers as everything
else in `services/adequacy/`. Slack generators are excluded: counting the VoLL
slack as firm capacity would satisfy any margin trivially — that is the whole
failure mode this constraint exists to prevent, so it gets a ★ bitten test.

## 2. The three decisions this constraint cannot dodge

Each is recorded here because each is a place where a defensible-looking
default would quietly produce a wrong number.

### 2.1 Must-take VRE contributes at a **capacity credit**, not nameplate

A 200 MW wind farm is not 200 MW of firm capacity, and it is not 0 either.
v1 uses the **peak-coincident availability** already computable from the
profile: `d_g = mean of p_max_pu over the N snapshots of highest demand in P`
(N = `PRM_PEAK_HOURS`, default 10 — the ~top 0.1 % of an annual horizon, the
convention behind "peak-hour ELCC proxies"). This is a CONSTANT at build time,
so the constraint stays linear.

Recorded honestly: a peak-coincidence proxy is not ELCC. It is systematically
optimistic where the peak hours happen to be windy and pessimistic otherwise,
and it cannot see the storage interaction. The panel says so, and the seam for
the real thing is `d_g` — Phase 6's `elcc.py` already computes the real number
for named existing assets, so an ELCC-weighted PRM is a substitution here plus
the fixed-point machinery, not a rewrite. **Users must be able to see the
derating table**, so the constraint publishes it (see §4).

### 2.2 Storage contributes at a **duration-haircut**, and the haircut is a rule

Power alone is optimistic: a 60 MW / 1 h battery does not cover a 4-hour
evening ramp. v1 uses the standard duration rule
`d_s = min(1, max_hours / PRM_STORAGE_DURATION_H)` × `(1 − q_s)` with
`PRM_STORAGE_DURATION_H` default 4 — i.e. full credit at 4 h duration or more,
pro-rata below. Extendable storage contributes via `StorageUnit-p_nom` on the
same extendable/fixed split.

Alternative considered and rejected for v1: excluding storage entirely. It is
"safe" only in the sense that it is wrong in a known direction, and on a
storage-heavy plan it would demand a firm fleet nobody builds.

### 2.3 The margin is a **constraint**, never a cost

No penalty term, no soft margin, no shadow-price-as-objective. A reserve
margin is a standard, and a standard the LP can buy its way out of is not one.
Consequence, stated in the UI: **an infeasible PRM is a real answer** ("no
plan built from your candidate set can reach this margin"), not an error to
be smoothed away — the same posture the frontier takes for an unreachable
ε-point.

## 3. What it does for the loop (the payoff, and the honest caveat)

The Phase-7 controller takes any `solve_at`. Phase 8 adds a second binding:
`solve_at(m)` = `dataclasses.replace(cfg, reserve_margin=m)`, and the loop
tunes **m** instead of ε. The whole search discipline carries over unchanged —
informed tightening, plan-hash plateau reuse, infeasibility monotonicity
(feasible sets are nested in m too: a larger margin is strictly harder), the
verified-only stopping rule, the abort path.

Two things must be true for this to be worth shipping, and both are testable:
1. **Raising m raises firm capacity.** ★ live test: two solves at m = 0 and
   m = 0.3 on the S17 fixture must differ in built capacity.
2. **Raising firm capacity lowers MC-LOLE.** ★ live test: the MC evaluated on
   the m = 0.3 plan must report a strictly lower LOLE, intervals separated
   under paired seeds — the same CI-aware discipline S16.5 uses for storage.

If (2) fails on the QA fixture, the phase has not delivered its point and the
plan says so rather than shipping a lever that moves nothing.

**Caveat that must ship with it:** a PRM is a *proxy standard* too. It is
justified by convention and by the derating factors, not by the sampler — so
a plan meeting a 15 % margin has no guaranteed MC-LOLE. That is precisely why
the loop remains the thing that certifies, and the PRM is what gives it a
lever. The UI must never present a met margin as a met reliability target.

## 4. Surfaces

- **`SolverConfig`**: `reserve_margin: float | None` (Field bounds `ge=0`,
  `le=5` — a 500 % margin is a typo, and the Phase-1 QA round found four
  unbounded reliability fields shipped as a real defect).
- **Report**: the achieved margin and its binding status join `TargetBlock`
  the way the ENS cap does — `basis` gains nothing (per Phase 7's [N3], a
  basis the solve *can* honour is different from one it cannot; the margin IS
  enforced, so a `reserve_margin` block on the report is honest). Stash the
  solve-time denominators (`peak_P`, the derating table) exactly as
  `_ens_cap_targets` does, because restore reverts the load transforms and a
  post-solve recomputation would drift.
- **`GET /results/reserve_margin`** — the derating table, per period:
  `{periods: [{period, peak_mw, required_mw, firm_mw, margin_achieved,
  binding}], assets: [{name, kind, capacity_mw, derate, firm_mw, source}]}`.
  Read-only, on demand, no worker thread (it is one snapshot and arithmetic).
  Its job is to make §2's proxies **inspectable** — a derating table nobody
  can see is a number nobody can check.
- **Frontend**: a margin field in Solver Settings beside the ENS target (with
  the "this is a proxy standard, not an MC verdict" sentence), and a
  ReserveMarginPanel on the Adequacy tab rendering the derating table and the
  achieved-vs-required readout. The loop panel gains a **lever selector**
  (cap ε \| margin m) once §3's two tests pass.

## 5. Non-goals (v1)

- ELCC-weighted derating (seam recorded at `d_g`; needs the fixed point).
- Per-zone margins (system-wide only; the ENS cap's zone machinery is the
  model when it lands).
- Interconnector/import credit — a Link's contribution to firm capacity is a
  policy question (is the neighbour's capacity yours?) and gets its own
  decision, not a default.
- Any soft/penalised margin (§2.3).
- Making the loop tune m by default: the lever selector ships only after the
  §3 tests prove the lever moves the metric.

## 6. Open decisions for review

1. `PRM_PEAK_HOURS = 10` — right for a 168 h horizon, or should it scale with
   horizon length (e.g. top 0.1 % of snapshots, floored at 1)?
2. `PRM_STORAGE_DURATION_H = 4` — the common convention, but ERCOT/PJM differ;
   does it belong in `SolverConfig` rather than as a constant?
3. Should a **non-extendable** fleet that already violates the margin make the
   LP infeasible, or should the constraint be skipped with a loud warning?
   (Infeasible is honest but hostile on a fixed network the user cannot
   change; the frontier's "unreachable point" precedent argues for infeasible
   + a clear message.)
4. Does `d_g` for a *committable* generator need its minimum-up/down state, or
   is `1 − q` enough at the capacity layer? (Probably enough — the constraint
   is about installed firmness, not dispatch — but state it.)
5. Report placement: a `reserve_margin` block on `TargetBlock`, or its own
   sibling like the COPT/MC studies? (Plan says on the report, because unlike
   those it IS enforced by the solve.)
