# Reserve-margin constraint spec (Phase 8) — v1, BINDING

Workers implement THIS document. Rationale and review findings live in the plan
(`docs/superpowers/plans/2026-08-29-fmea-phase8-reserve-margin.md`, v2 — the
[B*]/[S*] tags); where the two disagree, this spec wins and the master is told.
Amendments are recorded at the bottom, never silently.

**Scope boundary:** the coupling loop does NOT gain a margin lever in this
phase (plan [B5]). Phase 9 owns that. Nothing here may rename or re-key
`coupling.py`.

## 1. Config

`services/solver_service.py::SolverConfig` (a dataclass — no validation there):
```python
reserve_margin: float | None = None            # fraction; 0.15 == 15%
prm_peak_hours: int | None = None              # None ⇒ the §3.3 rule
prm_storage_duration_h: float = 4.0
```
`models/schemas.py::SolverConfigSchema` carries the bounds:
`reserve_margin: float | None = Field(default=None, ge=0, le=5)`,
`prm_peak_hours: int | None = Field(default=None, ge=1)`,
`prm_storage_duration_h: float = Field(default=4.0, gt=0)`.
Both knobs live on the CONFIG, never as module constants: `InputsBlock.
assumptions_hash` is computed from `asdict(cfg)`, so a constant would let two
reports made under different conventions carry the same hash.
★ test: schema rejects `reserve_margin=-1` and `=6` (422 at the API boundary,
the Phase-1 QA lesson), accepts `0`, `None`, `0.15`.

## 2. The wrapper — `_wrap_with_reserve_margin(network, user_fn, cfg, log_queue)`

Composes like the others (`solver_service.py` ~line 766). Returns `user_fn`
unchanged when `reserve_margin` is None/non-finite/≤ 0.

Per active investment period P, add ONE constraint:
```
Σ d_g·P_g (extendable, LP var)  +  Σ d_g·p_nom_g (fixed, constant)
    +  storage terms (§3.4)      ≥  (1 + m) · peak_P
```

### 2.1 Membership and activity [B1, B2, B3]
- Classification (scope, `source`, `basis`, `q`) comes from
  `copt._membership_walk(n, elec_buses, keep_zero_capacity=True)`. **The
  `keep_zero_capacity=True` is mandatory**: without it `solved_capacity` reads
  `p_nom_opt == 0.0` at LP-build time and drops every unbuilt extendable — the
  exact assets the margin exists to force into being. ★ bitten test: the S17
  fixture's `peaker` (p_nom=0, extendable) must appear on the LHS.
- **Capacity terms never come from `solved_capacity`.** Extendable ⇒ the
  `Generator-p_nom` variable; fixed ⇒ `float(gens.at[g, "p_nom"])`.
- Activity: `n.components.generators.get_active_assets(P)` masks BOTH sides.
  ★ bitten test: a `build_year=2040` extendable must not satisfy the 2030
  constraint.
- Coord membership before `.sel`: `if name in p_nom_var.indexes["name"]`.
  Do NOT mirror `_wrap_with_capex_budget` (it guards only the variable's
  existence and raises `KeyError` on an inactive extendable). Mirror the
  curtailment wrapper.
- Slack exclusion uses `slack.slack_generator_mask` (BOTH tiers), never
  `involuntary_slack_mask`. ★ bitten test on a DSR-configured network: a
  `__dsr_*` generator must not contribute firm capacity.
- Single-period / no-vintage networks: `Generator-p_nom` has no period
  dimension, so the per-period constraints share one variable and the system
  degenerates to one horizon-wide constraint at `max_P peak_P`. Record it in
  the stash as `horizon_wide: true` so the panel can say so.

### 2.2 Derating `d_g` [B4, S1, S3]
`d_g = (1 − q_g) × static_p_max_pu_g`, clamped to [0, 1].
- `static_p_max_pu_g` = `float(gens.at[g, "p_max_pu"])` when the column exists
  and is finite, else 1.0 — applies ONLY to units with occurrence data (a
  time-series unit is handled by §3.3).
- `source == "missing"` splits by EVIDENCE:
  - the unit HAS a `generators_t.p_max_pu` column ⇒ must-take, §3.3 credit;
  - otherwise ⇒ **excluded from the LHS**, and preflight errors (§4).
  **Nothing in `d_g` may default to 1.0.** ★ bitten test: a `geothermal`
  generator with no outage data and no profile must NOT be credited at 1.0.
- Every derating row carries its `basis` ("FOR" | "EFORd") and `source`.

### 2.3 Must-take VRE credit [S5]
`d_g` = mean of that unit's `p_max_pu` over the peak snapshots of P, where
`N = min(100, max(1, round(0.01 * len(P_snapshots))))` and **every snapshot
tied with the Nth-highest demand is included**. ★ bitten test: on a flat-demand
fixture (all snapshots tied) the credit equals the profile's mean over the WHOLE
period, not over the first N by index order.

### 2.4 Storage [S10]
`d_s = min(1, max_hours / cfg.prm_storage_duration_h) × (1 − q_s)`, on
`StorageUnit-p_nom` (extendable) or `p_nom` (fixed), same activity/coord rules.
`Store` components are excluded. A `StorageUnit` with non-zero `inflow` is
credited by the same rule and flagged `energy_limited: true` in the table
(recorded limitation, not fixed here).

### 2.5 The peak [S4]
Build the demand series exactly as `_wrap_with_ens_cap` does (electrical buses;
`loads_t.p_set` overriding static per column). `peak_P = float(demand[in_P].max())`.
**Weights must NOT enter the peak.** ★ bitten test: a network with snapshot
weightings of 50 must report the same `peak_mw` as one with weightings of 1.

### 2.6 The stash
`n._reserve_margin_targets = {"margin": m, "horizon_wide": bool, "periods":
{str(P): {"peak_mw", "peak_snapshots": [str,...], "n_peak_hours",
"required_mw", "firm_fixed_mw", "max_achievable_mw"}}, "assets": [ {name, kind,
capacity_mw|None, derate, basis, source, extendable, energy_limited} ]}`.
`max_achievable_mw` = derated fixed + derated `p_nom_max` of active extendables
(needed by §4's diagnosis). Cleaned up by `run_simulation` after the report is
built, exactly like `_ens_cap_targets`.

## 3. Preflight / validation [S2, S11, 6.3]

In `validation_service`, when `reserve_margin > 0`:
- **ERROR** when any active generator has `source == "missing"` AND no
  `p_max_pu` time series — naming them: "the reserve margin cannot price N
  generators (no outage data, no availability profile)".
- **ERROR** when `max_achievable_mw < required_mw` for any period computable
  pre-solve — "no plan built from your candidate set can reach this margin"
  with both numbers. This replaces "let the LP go infeasible", which is not
  implementable: linopy raises `TypeError` on a constant constraint, and
  `Generator-p_nom` does not exist when nothing extendable is active.
- **WARNING** when any credited unit's `source == "carrier_default"`: "the
  reserve margin derates N generators using carrier class averages you did not
  enter — those numbers change what gets built".
- The rolling/myopic coherence question: mirror `_check_ens_cap_coherence`'s
  decision for the margin and state it (the margin's denominator is a peak, not
  a period demand sum, so it may be admissible where the cap is not — adjudicate
  and report).

`_diagnose_infeasibility` gains a clause reading `_reserve_margin_targets`:
"the reserve margin requires X MW of derated capacity in period P; the maximum
buildable derated capacity is Y MW". ★ bitten test.

## 4. Report + endpoint [S6, S7, 6.5]

- `models/adequacy.py`: a NEW sibling block `reserve_margin: ReserveMarginBlock
  | None` on `AdequacyReport`. **Do NOT widen `TargetBlock.binding`** — it is a
  three-value `Literal` re-declared in the frontend with an exhaustive label
  `Record`, pinned by tests, and read by the loop's never-bound diagnosis; a
  fourth value renders `undefined`. `ReserveMarginBlock` carries per-period rows
  each with their own `binding: bool`.
- **`build_adequacy_report` must fire when EITHER stash is present** (today:
  only `_ens_cap_targets`), or a margin-only run produces no report at all.
  ★ bitten test.
- The margin block is published ONLY when `status in ("ok","optimal")` — the
  identical guard the ENS cap got after QA round 2 found an infeasible solve
  publishing a "target met" report. ★ bitten test.
- `GET /results/reserve_margin` serves the PERSISTED stash (emitted into solver
  state like `last_lost_load`), never a recomputation — a post-solve
  recomputation reads the restored loads and drifts. 204 before any solve with
  a margin set.
- `ROUTE_SURFACES` + the series/aggregate census (two registries).

## 5. Sweep [B7]

`services/adequacy/sweep.py`'s `dataclasses.replace(cfg, ens_cap_permyriad=None,
ens_zone_cap_multiple=None)` gains `reserve_margin=None`, same one-line
rationale as the cap. ★ bitten test: a class-B contingency on a margin-set
network completes rather than failing infeasible.

## 6. Frontend

- Solver Settings: a margin field beside the ENS target, with the sentence that
  a met margin is NOT a met reliability target (a proxy standard justified by
  convention and the derating factors, not by the sampler).
- `ReserveMarginPanel` on the Adequacy tab: achieved-vs-required per period with
  the `horizon_wide` label when it applies, the derating table (name, kind,
  capacity, derate, basis, source, energy_limited), the peak-hour timestamps and
  N, and the `derating_bases` roll-up with the FOR-is-optimistic note.
- Mounts unconditionally (the Adequacy tab's no-early-return invariant).

## 7. Acceptance [B6]

★ **A1 — the lever moves capacity.** Solve at `m=0` and at a computed `m`;
built capacity strictly increases.
★ **A2 — the capacity moves the metric, self-calibrated.** `m` is COMPUTED as
the smallest margin whose derated LHS exceeds `peak + largest_active_unit_mw`
(the threshold at which the one-unit-out state stops being a loss-of-load
hour). MC-LOLE on that plan must be strictly lower than at `m=0`, **intervals
separated under paired seeds** (S16.5's discipline). A hardcoded margin is
forbidden: on S17 `m=0.3` buys 20 MW and cannot move LOLE at all.
★ **A3 — the step behaviour.** At a margin below that threshold, EUE falls
while LOLE does not. This pins the arithmetic the review caught.

## 8. Gates

Backend adequacy gate (`tests/test_adequacy_*.py`, golden coverage, results
range) 0 failed with the 4 slow benchmarks unchanged; frontend `tsc -b` clean +
full vitest 0 failed; live S18 added to `qa_e2e.py`. Every ★ red first, bitten,
no worker commits.

## Amendments

### v1.1 — Wave A adjudications (ratified by the master)

1. **A unit with BOTH occurrence data and a `p_max_pu` profile** takes its
   availability from the profile's peak-coincidence mean × `(1 − q)`. §2.2's
   `static_p_max_pu` path applies only where there is no profile. This is the
   only reading under which "nothing in `d_g` may default to 1.0" actually
   holds — PyPSA's static `p_max_pu` default IS 1.0, so a wind unit with a
   user-entered outage rate would otherwise be credited at nameplate.
2. **Storage with `source == "missing"` is EXCLUDED** and named in the `[PRM]`
   log — the direct analogue of the generator evidence split, since storage has
   no profile to fall back to. A blank- or exotic-carrier `StorageUnit` gets no
   credit; `battery`/`hydro` resolve from the defaults library and do.
3. **`assets` rows are per (asset, period)** with an added `"period"` key, since
   a must-take derate is period-dependent. All eight spec-named keys remain on
   every row, so §4's reader is unaffected on single-period runs.
4. **`horizon_wide` is true iff the active EXTENDABLE set contributing an LP
   term is identical in every period** — precisely when the periods share one
   variable set and the system degenerates to `max_P peak_P`. Trivially true on
   single-period networks.
5. **The flat-network period key is `"ALL"`**, matching `_wrap_with_ens_cap`'s
   stash convention so the two stashes key alike.
6. **`max_achievable_mw` is `inf`** when an active extendable has an unbounded
   `p_nom_max`. Mathematically right and it makes §3's `max_achievable <
   required` test behave (always False), but it is NOT JSON-serialisable —
   **§4's endpoint MUST clamp or null it**. Recorded as a Wave-C obligation.
7. **A period whose margin is already met, or unreachable with no extendable
   term, adds no constraint** and emits a `[PRM]` line saying which way it fell
   (linopy raises `TypeError` on a constant constraint, and the nominal
   variable does not exist when nothing extendable is active). `required_mw` and
   `max_achievable_mw` are stashed regardless so §3 can error and
   `_diagnose_infeasibility` can speak.
8. **The wrapper's explicit `slack_generator_mask` re-check is redundant** with
   the walk's own filtering (proven: biting the wrapper alone did not bite) and
   is kept deliberately — both-tier exclusion is normative for THIS constraint,
   while the walk's default is a shared-module decision that could change.

### v1.2 — Wave B adjudications (ratified by the master)

1. **Rolling = ERROR, myopic = WARNING** (§3's open question, settled by the
   denominator). `optimize_with_rolling_horizon` calls `extra_functionality`
   once per WINDOW, so §2.5's `peak_P` silently becomes the window peak — a
   weaker standard enforced under the right name, the same class of defect that
   made the ENS cap refuse rolling. A myopic iteration's snapshots ARE one
   investment period, so the constraint it installs is the correct one; what
   breaks is only the report (each iteration overwrites the stash). A correct
   standard with an incomplete report is a warning; a silently different
   standard is an error. Codes `reserve_margin_unsupported_strategy` /
   `reserve_margin_myopic_report_is_partial`.
2. **One shared core, not two derating chains.** The classification/peak/derate
   computation is extracted to `solver_service.reserve_margin_facts(...)`, used
   by both the wrapper and preflight. Duplicating it would have created two
   standards — the one preflight blocks on and the one the LP enforces.
3. **Preflight is LOPF-only** (unlike `_check_ens_cap_coherence`, which runs
   unconditionally): a `pf` run enforces no margin, so blocking it would be a
   refusal with no standard behind it.
4. **Amendment v1.1(6) resolved as NULL, not clamp.** `max_achievable_mw`
   becomes `None` plus `max_achievable_unbounded: bool` at the two wire
   surfaces; the stash keeps the honest `inf`. Clamping would invent a ceiling
   nobody entered, which §3's `max_achievable < required` test could then fire
   on by accident.
5. **`met` and `binding` are separate fields.** `met` = the plan reaches the
   standard; `binding` = firm capacity sits on the bound. A margin the fixed
   fleet already satisfies is met and NOT binding — calling it binding would
   credit the margin for capacity that was always there.
6. **A margin-only report keeps `TargetBlock.binding == "voll"` and
   `cap_mwh == 0`** — the Literal is not widened (a new test pins it to exactly
   three values).
7. **Both surfaces publish the identical shape** so the endpoint payload and
   `AdequacyReport.reserve_margin` cannot drift; asset rows carry BUILT capacity
   (`p_nom_opt`) on the wire while the stash keeps LP-time truth.
8. **`_diagnose_infeasibility` takes the stash as a PARAMETER**, captured before
   the cleanup `delattr` — the diagnoser runs after the report step, so reading
   the attribute there finds nothing (a bite proved it).
9. **New solver-state key `last_reserve_margin`**, registered for persistence
   and reset at BOTH per-solve reset sites — without the reset a stale margin
   republishes onto the next plan (a bite proved it).

**Findings from Wave B worth keeping:**
- The `inf` path is currently unreachable through a live solve
  (`_check_extendable_bounds` already refuses an infinite `p_nom_max`), so
  amendment v1.1(6) is defence for a restored payload or a relaxed bound. The
  tests therefore seed a payload built by the REAL wrapper rather than a
  hand-written dict, and assert `json.dumps(..., allow_nan=False)` — what
  Starlette actually does.
- **Wave B's own preflight made a Wave-A test vacuous** and it was repointed:
  `test_stash_is_cleaned_up_after_a_failed_solve` used a fixture that preflight
  now blocks, so the LP — and the cleanup path the test exists for — was never
  reached while its assertions still passed. It now uses a transmission-limited
  fixture (preflight passes, the constraint is live, the dispatch is infeasible
  behind a 10 MW line) and additionally asserts
  `condition != "validation_failed"` so it cannot go vacuous the same way again.

**Bite-quality note, recorded because it is the process working:** three of the
worker's first-draft bite variants did not bite — the coord-membership test
(masked by the activity mask), the slack re-check (masked by the walk), and the
`Store` exclusion (masked by the activity mask). Each was replaced by a variant
that reaches the real defence (the full capex-wrapper mirror; both membership
call sites; stores repointed as a power rating). A test whose first bite fails
is not a passing test — it is an untested one.

### v1.3 — the net-load window (Phase 12b, plan v5 + v5.1)

1. **§2.5 — one window rule, shared.** The peak-coincidence selection
   (top 1 %, capped at 100, never fewer than 1, every snapshot tied with the
   Nth-highest included, `prm_peak_hours` honoured) is extracted to
   `services/adequacy/window.peak_window` and used for BOTH the gross window
   the constraint credits on and the net-load window the payload selects
   post-solve, so the two can only differ by their series.
2. **§2.6 — the stash carries the demand and the profile leg.** Per period:
   `demand_mw` (the SCALED demand `pd.Series` the constraint was built on)
   and `peak_hours_override`. Per asset row: `q`, `profile_kind ∈ {"none",
   "constant", "varying"}` (what the row's availability looked like IN THIS
   PERIOD), `nettable` (= `profile_kind == "varying"`), and `profile` (the
   member's `p_max_pu` series restricted to the period — the exact object
   the derate was computed from — stashed ONLY when varying). All in memory,
   never serialised; cleared at solve start and deleted at the report step.
   The profile is stashed rather than re-read because the restore drops the
   columns the vintage expansion cloned (`wind@2030`), and because every
   demand- and profile-derived number must come from the same system.
3. **§4 — the payload computes the net-load window, post-solve.** In
   `reserve_margin_payload`, from the stashed demand, the stashed profiles
   and BUILT capacity through the payload's own vintage-aware `_built`
   (the rule `firm_mw` is computed with — one capacity rule, not two). A row
   is `netted` when `nettable` and `cap is not None and cap > 0`; the net
   series is `demand − Σ fillna(profile, 0) × cap` over netted rows (rule 1:
   an unknown hour is unavailable). Per period a `net_window` block is
   ALWAYS present with `status ∈ {"ok", "nothing_netted", "no_finite_demand",
   "empty_window"}` (the last: finite demand but an empty window — the
   threshold landed on a NaN — unreachable from the facts loop today, named
   so the enum never lies),
   `netted_assets`, `snapshots`, `n_hours`, `net_peak_mw`,
   `gross_at_net_peak_mw`, `netted_mw`, `overlap_hours`, `firm_gross_mw`,
   `firm_net_mw` (numerics null unless `ok`). Per asset row: `profile_kind`,
   `nettable`, `netted`, and `derate_net` = `(1 − q) × _finite(mean(profile
   over the net window), 0.0)` — `derate`'s own expression on the other
   window (rule 2), null for a row without a varying profile AND null for
   every row in a period whose window is not `ok` (there is no other window
   to compute it on). A constant
   profile is window-independent and is never netted; a thermal maintenance
   schedule varies and IS netted, as a decision — "netted capacity" is not
   "VRE". The block is a SECOND PROXY in the margin's own units, never a
   correction; the copy never says "corrected".
4. **§4 — models and sanitiser.** `models/adequacy.py` gains
   `NetWindowBlock`, `ReserveMarginPeriod.net_window`, and the four asset
   fields; `ReserveMarginPeriod.peak_mw` / `required_mw` widen to
   `float | None` so the report surface degrades on non-finite demand as the
   route already did, instead of throwing the whole adequacy report away.
   `sanitize_reserve_margin_payload` descends into `net_window` and into
   asset rows. Amendment v1.2(7) is pinned by a test that compares the
   report block to the route payload field for field.
5. **§6 — the panel** gains a `derate_net` column beside `derate` (`—` with
   "no profile" or "constant in this period — window-independent" by
   `profile_kind`), a `netted` marker, and one summary line per period.
   Under myopic the block is the last period solved, as every other margin
   field already is — and the payload now SAYS so: `partial_periods: bool`
   on the block, set from the solve strategy at the report step, and the
   panel prefixes every line with "last period solved (myopic)". The
   shipped-code review found the first version promised this copy without
   carrying the fact.
6. **Two shipped defects fixed as the precondition (`2aa4dcd`):** the payload
   credited zero to every vintage-expanded asset (the restore dropped the
   rows before `_built` ran); and a run that failed between the wrapper and
   the report step leaked its stash — both standards' — into the next solve.

### v1.4 — the facts read the LP's demand basis from every caller (Phase 12c-0)

`reserve_margin_facts(n, cfg, snapshots=None, emit=None, *,
demand_scaled_in_place=False)`. §2.5's demand series is built from
`services.adequacy.demand.demand_frame_for`: from a route (preflight §3,
the margin loop's probes, any post-solve reader) the frame is
`loads_t.p_set` with the solver config's load scalers applied through the
shared resolution; inside the solve wrapper the transforms are already in
place and the wrapper says so with the switch. Consequence: the preflight's
reachability check (§3) and the margin loop's probe now see the same peaks
the constraint enforces on a scaled project; before this amendment they
read the raw series (v3 review of the 12c plan, verified). A static
`loads.p_set` is untouched everywhere, as the LP leaves it.
