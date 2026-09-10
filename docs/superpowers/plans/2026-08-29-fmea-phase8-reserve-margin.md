# Phase 8 — the firm-capacity (planning reserve margin) constraint (plan, v2 post-review)

v1 was adversarially reviewed and did badly: **7 blockers, 11 should-fixes**. Two
of them killed v1's shape outright, and both are recorded here rather than
quietly repaired:

- **v1's central bet was false [B5].** "The whole search discipline carries over
  unchanged — the loop just gets a second `solve_at`" is wrong in seven
  independent places: the controller's step function only ever *decreases*
  (raising a margin is the direction that helps), the informed step divides by
  `cap_mwh`, the unreachability floor is expressed in MWh, the ≤0 clamp turns a
  legitimate `m = 0` into a 1 % margin, the refinement bracket's inequality is
  backwards for a lever whose *met* end is the larger value, the tie-break
  prefers the wrong end, and `eps_permyriad` is a wire-visible field name.
  Worse, the binding cannot work at all: `build_adequacy_report` only fires when
  `_ens_cap_targets` is set, so a margin-only solve returns **no report**, every
  iterate reads `no_report` → failed, and the loop grinds the margin to zero and
  reports `budget_exhausted`. **Consequence for scope: the loop lever is NOT in
  this phase.** It becomes Phase 9, a controller refactor (inject direction +
  step policy, rename `eps_permyriad` → a neutral `lever_value` with a `lever`
  discriminator) with its own review and test round.
- **v1's own acceptance test was arithmetically guaranteed to fail [B6].** On
  the S17 fixture, `m = 0.3` buys exactly 20 MW of peaker; the LOLE-driving
  state is "one 100 MW unit out", where 120 MW against a 150 MW load is *still*
  a loss-of-load hour. LOLE would not move until `m ≥ 0.49`. v1's stop-rule
  ("if this fails the phase has not delivered its point") would have killed a
  sound phase on a badly chosen constant. **General lesson, now normative: a
  reserve margin sized in MW moves EUE continuously but moves LOLE in STEPS set
  by the largest unit's capacity.** Acceptance tests must be self-calibrated,
  never hardcoded.

## Why this phase at all (unchanged from v1, and the review did not dispute it)

Phase 7's first live run reported `unreachable` in two solves on a fixture where
the LP sheds nothing at any cap, and its verdict tells the user the fix is *firm
capacity the LP sees no deterministic reason to build*. The tool diagnoses a
problem it gives no lever for. This phase supplies the lever as a first-class
standard; Phase 9 hands it to the loop.

## 1. The constraint

For each investment period P:

```
Σ_{g ∈ active(P)}  d_g · P_g   +   Σ_{s ∈ active(P)}  d_s · S_s
        ≥   (1 + m) · peak_P
```

### 1.1 The capacity terms — what `P_g` actually is [B1, B2, B3]

- **`Generator-p_nom` is ONE variable for the whole horizon, not one per
  period** (`pypsa/optimization/variables.py:340-360` builds it on
  `extendables ∩ active_assets`, coords `(name,)` only). So on a network with
  no per-period vintages the per-period constraints share one variable and the
  system **collapses to a single horizon-wide constraint at the maximum peak**.
  That is a *different standard* from "per period" and the panel must label it
  as such — silently calling it per-period is the kind of claim this program
  does not make.
- **Activity must be masked explicitly** with
  `n.components.generators.get_active_assets(P)`. The variable for a
  `build_year=2040` extendable EXISTS while solving 2030 (verified by probe), so
  an unmasked 2030 constraint would let the LP satisfy the 2030 margin with 2040
  capacity. This is not hypothetical: `vintage_service.apply_vintage_bounds`
  sets `build_year = period`, which is the repo's standard multi-period idiom.
  Fixed rows need the same mask in the other direction (a not-yet-built fixed
  asset must not appear as a constant).
- **Never `.sel(name=…)` without a coord membership test** [B2]:
  `v.sel(name="nosuch")` raises `KeyError`, and an extendable-but-inactive row
  is in the frame mask yet absent from the variable's coords. Test
  `name in p_nom_var.indexes["name"]`. **Do not mirror
  `_wrap_with_capex_budget` here — it has exactly this bug** (guards the
  variable's existence, not the coord). Mirror the *curtailment* wrapper, which
  is the one that gets it right.
- **Capacity comes from the LP, never from `solved_capacity`** [B3]. This is the
  single most load-bearing detail in the wrapper. `copt._membership_walk` →
  `solved_capacity` returns `p_nom_opt` **including 0.0** for an extendable row,
  and the wrapper runs at LP *build* time when no `p_nom_opt` for this solve
  exists. On the S17 fixture that silently drops the peaker — the one asset the
  margin exists to force into being — leaving only the fixed units on the LHS,
  so no `m` above 0.173 could ever be met and the constraint would go
  **infeasible instead of building**. Call the walk with
  `keep_zero_capacity=True` and use it **only for classification** (`source`,
  `basis`, scope); take the capacity term from the variable (extendable) or
  `p_nom` (fixed).

### 1.2 The derating factor `d_g` [B4, S1, S2, S3]

`d_g = (1 − q_g) × static_p_max_pu_g`, with `q_g` from
`occurrence.resolve_outage_params` — the same chain the COPT and MC read.

- **`static_p_max_pu` is part of the derate** [S3]. PyPSA caps dispatch at
  `p_max_pu × p_nom`, so a thermal unit with static `p_max_pu = 0.9` can never
  deliver nameplate; crediting it at `1 − q` alone overstates firmness. (This is
  the real answer to v1's §6.4, which asked about committables instead. For
  commitment state `1 − q` *is* enough: min-up/down is a dispatch property, and
  this constraint is about installed firmness.)
- **A missing carrier must never default to 1.0** [B4 — the worst of the
  derating findings]. `resolve_outage_params` returns `source="missing"` for any
  carrier outside the 10-entry defaults library — geothermal, waste, CHP, a
  user-typed carrier, a blank one — and `must_take_generators` is defined as
  exactly that predicate. Under v1's rule such a unit had no profile, so its
  credit fell back to PyPSA's `p_max_pu` default of **1.0**: a unit the tool
  knows *nothing* about would get MORE firm credit than a gas unit with a
  carrier default (0.95). Silent, and it changes what gets built.
  **Split the "missing" bucket by EVIDENCE, not by absence:**
  - has a `generators_t.p_max_pu` column → must-take, credited per §1.3;
  - otherwise → **excluded from the LHS**, and preflight blocks the solve:
    "N generators have no outage data and no availability profile — the reserve
    margin cannot price them" (naming them).
  Nothing anywhere in `d_g` may be 1.0 by default.
- **The basis rides with the number** [S1]. The occurrence module's rule is
  "THE TOOL NEVER SILENTLY CONVERTS": `1 − EFORd` is the correct UCAP derate,
  `1 − FOR` is not (FOR excludes reserve-shutdown hours and is optimistic
  exactly for peakers, the units that matter at the margin). The COPT tags its
  metrics with the basis mix; this constraint must too — and with more force,
  because here a wrong basis moves the *built plan*, not a diagnostic. The
  derating row carries `basis`, the payload carries a `derating_bases` roll-up,
  and the panel states that FOR-based rows are optimistic. (The defaults library
  is 9/10 EFORd with `battery` on FOR, so the mix is non-uniform on default data
  alone.)
- **Carrier-default derating is a hidden assumption that changes the plan**
  [S2]. Publishing `source` in the table is necessary but not sufficient — the
  user cannot see it before the solve. Preflight warns: "The reserve margin
  derates N generators using carrier class averages you did not enter. Those
  numbers change what gets built." (Precedent: `_check_ens_cap_coherence`
  already warns about config that constrains nothing.)
- **Slack exclusion uses `slack_generator_mask`, not the ENS cap's
  `involuntary_slack_mask`** [S9]. The latter excludes only the VoLL tier by
  design, so DSR slacks would count as firm capacity — and a `__dsr_` p_nom is
  `share × bus peak load`, potentially enough to satisfy any margin by itself.
  ★ The bitten test must use a DSR-configured network, not just VoLL.

### 1.3 Must-take VRE: a peak-coincidence proxy, labelled as one [S5, 6.1]

`d_g` = mean `p_max_pu` over the N highest-demand snapshots of P, where
`N = clamp(round(0.01 · |P|), 1, 100)` and **all snapshots tied with the Nth are
included**. v1's fixed `N = 10` was indefensible on the QA fixtures (21–42 % of
a 24–48 snapshot horizon), and a "top 0.1 %" rule floors to 1 on every horizon
under 1000 snapshots, making the credit a single-hour draw. The tie rule is not
a nicety: S17's load is a flat static `p_set`, so "the N highest-demand
snapshots" is a 48-way tie that `nlargest` would resolve by index order —
turning the credit into "the mean over the first 10 snapshots", deterministic
and meaningless.

Recorded honestly: **this is not ELCC.** It is optimistic where peak hours
happen to be windy, pessimistic otherwise, and blind to the storage
interaction. The seam for the real thing is `d_g` itself — Phase 6's `elcc.py`
already computes it for named existing assets, so an ELCC-weighted PRM is a
substitution plus the fixed-point machinery, not a rewrite. The payload
publishes N **and the selected timestamps**, because a proxy nobody can inspect
is a number nobody can check.

### 1.4 Storage: duration haircut [S10]

`d_s = min(1, max_hours / prm_storage_duration_h) × (1 − q_s)`, on
`StorageUnit-p_nom` with the same extendable/fixed and activity treatment.
Duration is genuinely invariant under extension (PyPSA's SoC bound is
`max_hours × p_nom`), so the rule is well-defined for extendable storage.
`Store` components are excluded, matching the MC's recorded rationale (no power
rating). **Known limitation, recorded not fixed:** a hydro reservoir with
`max_hours = 2000` and an `inflow` series takes full power credit while its
energy limit is what actually binds it — the mirror of the failure the haircut
exists to prevent. Either cap credit for units with non-zero `inflow` or state
it in the panel.

### 1.5 The peak is an unweighted MW maximum [S4]

`peak_P = float(demand[in_P].max())` on the demand series built exactly as
`_wrap_with_ens_cap` builds it (electrical buses, `loads_t.p_set` overriding
static per column). **Weights must not enter the peak** — the ENS cap's
denominator is a weighted *energy sum*, and copying that shape would report a
50× peak on a representative-week run with weight 50. Inherit and record the
known gap: load scalers only touch `n.loads_t.p_set` columns, so a load with
only a static `p_set` (S17's `load_a`) is **not** scaled; v1's "load scalers
already applied" is true for time-series loads only.

### 1.6 The margin is a constraint, never a cost

No penalty term, no soft margin. A standard the LP can buy its way out of is not
a standard.

## 2. Infeasibility must be actionable, not a slogan [B7-adjacent, S11, 6.3]

v1 said "an infeasible PRM is a real answer". Today that answer is unusable, and
one path cannot even be built:

- **Linopy cannot express a constant constraint.** `add_constraints(100 >= 200)`
  raises `TypeError` (probe-verified), and `Generator-p_nom` does not exist at
  all when nothing extendable is active — a live path, since myopic's
  dispatch-only branch solves periods with no active extendables and still
  passes `extra_fn`. So "let the LP go infeasible" is not implementable by
  adding a constraint.
  **⇒ A fleet that cannot reach the margin is a PREFLIGHT ERROR** [6.3
  answered]: every term is a constant before the solve, so
  `derated_active_fixed < (1+m) · peak` with no active extendable capacity is
  fully decidable up front. That is honest *and* actionable, and it beats the
  capex wrapper's precedent of silently skipping — acceptable for a budget,
  never for a standard.
- **`_diagnose_infeasibility` cannot see this constraint** [S11], and its
  peak-vs-buildable hint is gated on `voll <= 0`, so with a VoLL set it never
  fires. A PRM-infeasible run currently tells the user "No obvious structural
  cause found. Check binding capacity bounds, ramp limits, or a too-tight global
  constraint" — three wrong places. The wrapper stashes required-MW and
  max-achievable-derated-MW per period; the diagnoser reads the stash and says
  "the reserve margin requires X MW of derated capacity in period P; the maximum
  buildable derated capacity is Y MW".
- **The status guard is mandatory** [S11]. QA round 2 found an infeasible solve
  publishing a "target met" report; the ENS cap now guards on
  `status not in ("ok","optimal")`. The margin block needs the identical guard
  or an infeasible run republishes a stale margin from the previous solve.

## 3. Interaction with what already ships

- **The contingency sweep must strip the margin** [B7]. `sweep.py` already
  strips `ens_cap_permyriad`/`ens_zone_cap_multiple`; `reserve_margin` would
  survive, and `freeze_capacities` pins bounds while KEEPING
  `p_nom_extendable=True`, so the variable still exists and stays pinned — any
  contingency removing derated capacity then violates the margin, goes
  infeasible, and the whole sweep fails. One line, same rationale as the cap
  ("a binding standard would make every severity read as the standard"), plus a
  ★ test.
- **The frontier** sweeps ε with whatever margin is set: every point now also
  carries the margin. That is correct (the margin is a standing standard, not a
  swept one) but must be stated on the panel, or the curve reads as
  cost-vs-ε when it is cost-vs-ε-at-margin-m.
- **Benchmark anchors are unaffected** — verified: the benchmark suite is
  COPT/MC-only, no LP, no `SolverConfig`.
- **Report contract** [S7, 6.5]: do **not** widen `TargetBlock.binding`. It is a
  three-value `Literal` re-declared in the frontend with an exhaustive
  `Record` label map and pinned by tests, and `NEVER_BOUND_COPY_V1`'s diagnosis
  tests `binding == "system_cap"`; a fourth value renders `undefined` and
  misdiagnoses the loop. Worse, one field cannot report two standards when the
  cap and the margin both bind. Add a **sibling `reserve_margin` block** with
  its own per-period `binding: bool`, exactly as `ZoneTarget` does.
  **And change the report trigger**: `build_adequacy_report` currently fires
  only when `_ens_cap_targets` is set, so a margin-only run produces no report
  at all — the margin invisible exactly when it is the only standard. Fire when
  *either* stash is present. (This is also a prerequisite for Phase 9.)

## 4. Surfaces

- **`SolverConfig`** (dataclass, no validation) gains `reserve_margin`,
  `prm_peak_hours`, `prm_storage_duration_h`; **`SolverConfigSchema`** carries
  the bounds (`reserve_margin` ge=0 le=5; duration gt=0) [S8 — v1 put `Field`
  on the dataclass, which does nothing].
  **The two convention knobs MUST be on the config, not module constants**
  [6.2 answered, and this is the decisive argument]: `InputsBlock.assumptions_hash`
  is computed from `asdict(cfg)` alone, so a module constant is invisible to it
  and two reports produced under different conventions would carry the *same*
  hash — precisely the "Compare tab silently diffs incomparable numbers" failure
  that block exists to prevent.
- **`GET /results/reserve_margin`** serves the **persisted stash**, never a
  recomputation [S6 — v1 contradicted itself by demanding a stash for
  drift-safety and then specifying an endpoint that recomputes from reverted
  loads]. Follow `last_lost_load`: emit into solver state, delete the network
  attribute like `_ens_cap_targets` is deleted. Payload per period:
  `{period, peak_mw, peak_snapshots, n_peak_hours, required_mw, firm_mw,
  margin_achieved, binding, horizon_wide: bool}` plus
  `assets: [{name, kind, capacity_mw, derate, basis, source, firm_mw}]` and a
  `derating_bases` roll-up.
- **Frontend**: a margin field in Solver Settings beside the ENS target, with
  the sentence that a met margin is **not** a met reliability target — it is a
  proxy standard justified by convention and the derating factors, not by the
  sampler; and a ReserveMarginPanel on the Adequacy tab rendering the derating
  table (with basis and source per row), the achieved-vs-required readout, the
  horizon-wide-vs-per-period label from §1.1, and the peak-hour timestamps.

## 5. Acceptance — self-calibrated, never hardcoded [B6]

★ **Test 1 — the lever moves capacity.** Solve at `m = 0` and at a *computed*
`m`; built capacity must strictly increase.
★ **Test 2 — the capacity moves the metric.** The margin must be computed from
the fixture, not chosen: the smallest `m` whose derated LHS exceeds
`peak + largest_active_unit_capacity` (the threshold at which the
one-unit-out state stops being a loss-of-load hour). Then MC-LOLE on that plan
must be strictly lower with **separated intervals under paired seeds**, the
CI-aware discipline S16.5 uses for storage.
The step behaviour is itself worth a pin: EUE should fall at margins below that
threshold while LOLE does not, which is the arithmetic B6 caught.

## 6. Non-goals (v1 of this phase)

- **The loop lever** — Phase 9 [B5], a controller refactor with its own review.
- ELCC-weighted derating (seam at `d_g`; needs the fixed point).
- Per-zone margins (the ENS cap's zone machinery is the model when it lands).
- Interconnector/import credit — whether a neighbour's capacity is yours is a
  policy question, not a default.
- Any soft or penalised margin.

## 7. Review disposition

All five of v1's §6 open decisions are settled by the review, with evidence:
peak-hour count scales and ties are included (6.1); both knobs live on
`SolverConfig` because `assumptions_hash` is computed from it (6.2); an
unreachable fixed fleet is a **preflight error** because linopy cannot express
a constant constraint (6.3); `1 − q` suffices for commitment state but must be
multiplied by static `p_max_pu` (6.4); the margin goes on the report as a
sibling block **and** the report trigger must change (6.5).
