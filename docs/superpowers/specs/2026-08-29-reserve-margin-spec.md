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

(none yet)
