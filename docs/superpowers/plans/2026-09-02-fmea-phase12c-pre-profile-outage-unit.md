# Phase 12c-pre — a generator with BOTH a profile and outage data (plan, v1)

The fix Phase 12a's warning stood in for, and the gate for the portfolio
ELCC (Phase 12c). Recorded in 12a §2(a) as "its own phase, with its own
benchmark re-run"; scheduled now.

## 0. The defect, restated as a membership rule

`copt.fleet_and_residual` applies ONE rule (`copt.py:341-359`): a generator
with resolvable outage data (`source != "missing"`) becomes a two-state
`CoptUnit` at firm capacity and **its `p_max_pu` profile is discarded**; a
generator without outage data is must-take, netted into the residual at
`profile × capacity`. So a 100 MW wind farm at a 25 % capacity factor with a
4 % forced-outage rate is simulated by the COPT and the sequential MC as
**96 MW of firm capacity**, while the reserve margin credits the same unit
at `(1 − q) × mean(profile)` = 22.5 MW. Four numbers on one panel, 4× apart,
about one asset (Phase 12a's finding, `2963fc8`). And it is not rare: the
carrier-defaults library reaches hydro, gas, coal, nuclear, biomass and
battery with zero user input, so a hydro unit with an inflow profile is in
this set on every real project.

12a **warned**. This phase **models**: the unit's available capacity is its
profile, and its outages are sampled on top of that.

## 1. One representation, two engines, one expectation

`CoptUnit` gains `profile: np.ndarray | None` — the unit's availability
fraction per hour, `(H,)`, or `None`. The membership rule becomes:

| generator | outage data | profile | today | after |
|---|---|---|---|---|
| must-take | none | any | netted at `profile × cap` | **unchanged** |
| thermal | yes | none / static 1.0 | two-state at `cap` | **unchanged** |
| thermal, static `p_max_pu < 1` | yes | constant `< 1` | two-state at `cap` (the static value was ignored) | two-state at **`cap × level`**, exact |
| profiled + outage data | yes | **varying** | two-state at `cap` (profile discarded) | **`profile` attached**; treated per engine below |

A **constant** profile is folded into `capacity_mw` and `profile` stays
`None`: a two-state unit at `cap × level` is exact and the COPT convolves it
as today. This reuses Phase 12b's varying/constant split (`max − min > 1e-9`
over finite values) so the three engines and the margin cannot disagree
about what "has a profile" means.

For a **varying** profile the two engines do different, stated things, and
agree in expectation:

- **Sequential MC — exact.** `sample_capacity` accumulates
  `np.add(acc, cap, out=acc, where=state_path)` with a scalar `cap`
  (`mc.py:404, ~430`). For a profiled unit `cap` becomes the `(H, 1)` vector
  `profile × capacity_mw`, broadcast over draws: available capacity when UP
  is the profile; DOWN is zero. The outage chain, its stream and its
  consumption are untouched, so **the CRN contract is unaffected and the
  no-profile path is byte-identical** — the scalar branch does not change.
- **COPT — expected output, netted.** The COPT is ONE distribution over the
  fleet's available capacity (`build_copt`) evaluated hour by hour against a
  residual (`hourly_adequacy`); a unit whose capacity varies by hour cannot
  enter the convolution. Per-hour convolutions are `O(H · N · C/Δ)` and
  chronology-free multi-state (the FMEA spec's §5.3 phrase) would destroy
  the load–availability correlation, which is the whole point of a profile.
  So a varying-profile unit **leaves the convolved fleet** and its
  **expected available output**, `(1 − q) · profile · cap`, is netted from
  the residual — a new `copt_view(units, residual)` helper returns
  `(convolvable_units, residual_netted)` for the two COPT consumers (the
  `/copt` route and `attribute_criticality`, `results.py:5002-5009`). This
  keeps the per-hour correlation exactly and loses only the unit's outage
  *variance* — for q ≈ 0.02–0.05 that is small against weather variance,
  and it is the screening engine. The fidelity label says so (§4).
- **Reserve margin — unchanged.** It already credits the unit at
  `(1 − q) · mean(profile over the window)` = the window mean of the SAME
  series the COPT nets. One helper, `expected_available(unit) =
  (1 − q) · profile`, is what both read, and a ★ test pins that the margin's
  derate equals its window mean.

So: MC exact; COPT and margin at the identical expectation; and the sum of
the MC's sampled availability over many draws converges to that expectation
(★ A5). Three engines, one series.

## 2. What else the rule touches

- **ELCC.** A profiled unit is `kind="generator"` (exclusion by position,
  unchanged). Its `nameplate_mw` — the bracket ceiling and the dominance
  bound — becomes `max_h(profile × cap)`, in `_resolve` and in
  `elcc_candidates` (`elcc.py:154, ~336`); `cap` alone would be a ceiling
  the unit never reaches. `kind="vre"` (must-take, un-netting) is untouched.
- **Attribution (FMEA worksheet).** `attribute_criticality` prices a unit's
  outages as EUE(as-is) − EUE(unit perfectly available), by deconvolution
  and a deterministic shift. A varying-profile unit is not in the
  distribution, so its counterfactual is per hour: EUE on the residual with
  the unit at `profile × cap` (perfect) versus `(1 − q) · profile · cap`
  (as-is) — two `hourly_adequacy` calls, O(H). `f_i` from `q` and MTTR is
  unchanged.
- **Coupling and margin loops, frontier, sweep** — consume the MC or
  re-solve the LP; inherit.
- **`keep_zero_capacity`** — `profile × 0 = 0`; a superset-fleet member at
  zero capacity contributes nothing either way.
- **The 12a warning is retired, not re-scoped.** Its premise — the profile
  is discarded — is false after this phase. `_check_shadowed_profiles` and
  its tests go; S21 is removed from `qa_e2e.py` and its QA-plan entry is
  annotated *retired by 12c-pre, kept as the record of the defect*. In its
  place an **INFO**-level issue, `profile_and_outage_modelled`, names the
  units the new rule applies to, so a user who entered outage data on a wind
  farm learns how it is modelled rather than having the treatment change in
  silence. §7 Q4.

## 3. Payloads and copy

- `/copt`: `netted_at_expected_output: [names]` and a `fidelity` sentence:
  *"varying-profile units with outage data are netted at expected
  output; the MC samples their outages on the profile."*
- `/mc`: `profile_units: [names]`.
- ELCC candidates: `nameplate_mw` per §2.
- Panel: one sentence on the COPT card and one on the MC card. The
  worksheet row for a profiled unit shows nameplate AND the mean available
  capacity, so the "capacity" column is not silently the derated one.

## 4. Specs amended

- **FMEA spec §5.3**: "VRE as multi-state capacity" → varying-profile
  occurrence units netted at expected output; the reason (chronology).
- **MC spec** → v1.4: §2.1 `CoptUnit.profile`; §2.3 the `(H, 1)` broadcast
  and the byte-identical scalar path; §3 the nameplate rule.
- **12a plan**: superseded note at the top.
- `copt.py`'s membership docstring, and `CARRIER_DEFAULTS`' "deliberately
  absent" note (its double-count reasoning is now only about must-take).

## 5. Acceptance (each ★ with a bite; restores by hash)

★ **A1 — MC uses the profile.** One 100 MW unit, `q = 0.5`, profile
`[1, 0, 1, 0]`, residual 60 MW every hour, many draws: shortfall on hours
1 and 3 with probability 1 (nothing available) and on hours 0, 2 with
probability ≈ 0.5. *Bite: ignore the profile → hours 1, 3 short with
probability 0.5.*

★ **A2 — the scalar path is byte-identical.** `sample_capacity` on a
fixed no-profile fleet and seed hashes to the value recorded BEFORE this
change (computed on `1bce9da` and written into the test). *Bite: any
change to the scalar branch.* This turns "the anchors held" from a side
effect into a checked claim.

★ **A3 — the COPT nets a varying-profile unit at expected output.** COPT
LOLE on the fixture equals COPT LOLE of the fleet without the unit on
`residual − (1 − q) · profile · cap`, to 1e-12. *Bite: convolve it at
`cap`.*

★ **A4 — a constant profile folds in exactly.** A gas unit with static
`p_max_pu = 0.9`: convolved at `0.9 · cap`, `profile is None`. *Bite: keep
`cap` (today's behaviour).*

★ **A5 — the two engines agree in expectation.** Mean sampled availability
of the profiled unit over draws, per hour, is within the CI of
`(1 − q) · profile · cap`. *Bite: profile applied without the outage state
→ mean is `profile · cap`.*

★ **A6 — one series feeds the margin and the COPT.** The margin's `derate`
for a shadowed unit equals `mean(expected_available over the gross window)`.
*Bite: margin computes `(1 − q)` on the static column.*

★ **A7 — attribution prices a profiled unit's outages.** ΔEUE > 0 on a
fixture where the unit's outages matter, and equals the two-evaluation
difference. *Bite: skip profiled units → no row.*

**A8 (contract) — ELCC nameplate** = `max_h(profile × cap)` in candidates
and in `_resolve`. A wider bracket is inert (v2 review B5), so this is a
pin, not a bite, and says so.

★ **A9 — the anchors.** RTS-79 and RBTS, COPT and MC, held to the pinned
values at `rel=1e-6` (existing tests, re-run).

★ **A10 — the warning is gone; the note is there.** Preflight on 12a's
two-farm fixture emits no `outage_shadows_profile` and one
`profile_and_outage_modelled` naming the shadowed farm. *Bite: leave the
old check in.*

★ **A11 — the 4× disagreement is closed, live (S24).** 12a's live fixture:
two identical 100 MW farms, one with outage data. The COPT's LOLE with the
shadowed farm is within a few percent of the LOLE with the must-take farm
(not 4× apart), the MC's `profile_units` names it, and preflight carries
the info note. *Bitten live: with the profile dropped, the COPT number
reverts.*

## 6. Gates

The four anchors bit-for-bit; full backend tree identical to master minus
the retired 12a tests; frontend suite; S15–S20, S22–S24 live on one
port-verified server; every ★ bitten with hash-verified restores.

## 7. Open questions for the review

1. **COPT at expected output loses the unit's outage variance.** State the
   magnitude: on a fixture with one large profiled unit (q = 0.05, 500 MW
   hydro), how far is COPT-LOLE from MC-LOLE, and is that acceptable for a
   screening engine whose divergence from the MC is already "the product"
   (FMEA §5.3)?
2. **Folding a constant profile into `capacity_mw`** changes the worksheet's
   capacity column for that unit. Show nameplate and level separately, or
   accept?
3. **ELCC of a profiled unit**: is `max_h(profile × cap)` the right
   ceiling, or should a profiled unit's credit be bracketed by its own
   `(1 − q)`-derated peak?
4. **INFO note vs silence.** Is `profile_and_outage_modelled` noise on a
   real project (every hydro unit), or exactly the disclosure a changed
   treatment owes?
5. **Static `p_max_pu < 1` on a thermal unit** was ignored by the engines
   until now (12a called it out). Folding it in is a behaviour change on
   existing projects' COPT/MC numbers — flag in the PR body as a fourteenth
   finding, or as part of this phase?
