# Phase 12i — a constant availability series replaces the NETTING approximation, not the exact mixture (plan v2)

**Status:** **REJECTED** — and the phase was then closed unbuilt. See
`2026-09-07-fmea-phase12i-constant-series-fold-v3-do-not-build.md`.

> **Two blockers, both reproduced.** (1) "It is never worse" is false: on this
> plan's own first fleet, at load 667, the fold reads 48.7435 h against the
> exact 62.1181 — an error of 13.37 h where shipped's is 5.57, i.e. **2.4×
> worse**. The mechanism is that the table apportions a non-grid capacity
> across two grid states and the upper one is capacity the fleet cannot reach,
> so the fold *manufactures* capacity by rounding up. Worse, §0.3's evidence
> table is **not reproducible from the method this plan states**: it says it
> swept `[0.45,0.80] × total capacity` but the script swept `[0.45,0.80] ×
> Σ(cap × cf)` — the derated total — and load 667 lies inside the claimed
> window and outside the measured one. (2) H5 asks `split.table[i].capacity_mw`
> to carry the nameplate, but `_screen_block` passes `split.table` straight to
> `build_copt`: it is the math input, so H5 is either a `ValueError` or a −72 %
> LOLE error. Four serious findings besides. v3 records why no remedy was worth
> building.

**Status when written:** plan v2, for review before a line is written.
**Supersedes v1** (`…-v1.md`), which was **REJECTED** with three blockers. The
review's findings are recorded in §6 and every one of them is reproduced in
§0 — none is taken on trust, and two of them killed v1's headline claim.

**What changed, in one line:** v1 folded every constant-series unit. v2 folds
**only the units the split would otherwise NET**, because measurement showed
folding a unit that would have been *mixed* makes an already-exact answer
wrong.

---

## 0. The premise, re-measured after the rejection

### 0.1 v1's headline claim was false, and its evidence was a rigged fixture

v1 claimed the fold is "exact — 0.000 % on every row … the definition of a
two-state unit". That is true **in mean** and false in LOLE. v1's fixtures
used `100 × 0.8 = 80` MW, which lands exactly on the 1 MW grid, so the
apportionment `_unit_states` performs was invisible. Off the grid it is not.
Nine units, `cf = 0.833`, against the `k_exact`-raised exact reference:

| load | folded LOLE | exact LOLE | rel | folded EUE | exact EUE | rel |
|---|---|---|---|---|---|---|
| 582 | 2.27422 | 1.40465 | **+61.9 %** | 125.806 | 124.937 | +0.70 % |
| 583 | 4.88292 | 1.40465 | **+247.6 %** | 130.689 | 126.341 | +3.44 % |
| 584 | 8.23697 | 11.96351 | **−31.1 %** | 138.926 | 137.249 | +1.22 % |
| 585 | 10.63272 | 11.96351 | **−11.1 %** | 149.559 | 149.212 | +0.23 % |

EUE is nearly right because the apportionment is mean-preserving; LOLE is a
**threshold** statistic, so smearing a unit across two grid states it cannot
occupy moves it hard. This is exactly the concession 12h wrote into its own
§H3 and ★H3a ("no LOLE equality is asserted"), which v1 deleted. **v2 states
it as a standing property of the table, not as a caveat.**

### 0.2 The rejection's decisive finding: v1 broke the common case

`K_EXACT = 8`, so a fleet of eight or fewer profiled units is mixed exactly
and is **grid-free** — the shipped answer is the exact answer. v1 folded it
anyway. Three units, `cf = 0.833`:

| load | shipped (= exact) | v1 folded | error introduced |
|---|---|---|---|
| 167 | 23.961000 | 12.362070 | **−48.4 %** |
| 168 | 23.961000 | 21.914130 | −8.5 % |
| 250 | 168.000000 | 73.366377 | **−56.3 %** |
| 251 | 168.000000 | 136.887576 | −18.5 % |

Most real fleets are in this regime. v1 traded a fix for the rare saturating
fleet against a **regression in the ordinary one**, and its ★I1b asserted the
opposite ("byte-identical with and without the fold") without measuring it.

### 0.3 What v2 does instead, measured

**Fold a constant-series unit only when the split would otherwise NET it.**
The fold then never competes with the exact mixture; it competes with the
netting approximation, which is the only thing it needs to beat.

Absolute error against the exact reference, swept over every integer load in
`[0.45, 0.80] × total capacity`, restricted to points where the exact LOLE is
**at least 1 h** (relative error on a near-zero LOLE is noise, and v1's dense
sweep was misleading for exactly that reason):

| fleet | | shipped | **fold-netted** | fold-if-on-grid |
|---|---|---|---|---|
| 9 × 100 MW, cf 0.833 (99 pts) | max err | 8.2124 h | **5.7487 h** | 8.2124 h |
| | mean err | 1.1152 h | **0.0607 h** | 1.1152 h |
| 9 × 100 MW, cf 0.8 (95 pts) | max err | 8.2124 h | **0.0000 h** | 0.0000 h |
| | mean err | 1.0621 h | **0.0000 h** | 0.0000 h |
| 12 × 100 MW, cf 0.833 (49 pts) | max err | 2.3150 h | **0.9182 h** | 2.3150 h |
| | mean err | 2.3150 h | **0.0221 h** | 2.3150 h |

Three things to read off it, and the third is why v2 exists:

1. **Fold-netted is better on max AND mean, everywhere measured** — mean error
   falls 18× on the first fleet and 105× on the third, and the maximum falls
   too. It is never worse.
2. **It is not exact.** The residual 5.75 h max is the grid term of §0.1. v2
   claims "strictly better than the netting it replaces", which is what was
   measured, and **not** exactness, which is not true.
3. **The on-grid gate — the review's other suggestion — is worthless.** It is
   identical to shipped on every off-grid fleet, because `83.3` is not a
   multiple of 1 MW and the gate simply never fires. It only "works" where
   nothing was wrong. It is rejected on this measurement, not on taste.

And the control now holds **by construction**: a fleet that saturates nothing
has an empty `netted`, so nothing folds and the answer is byte-identical.
That is a property of the gate, not a hope.

### 0.4 The shipped error is still real, and still sign-varying

Unchanged from v1 and re-verified: on-grid, cap-saturating fleets read −19.6 %,
−9.0 %, **+192.5 %** and −26.8 % against exact. The +192.5 % is an
overstatement of nearly 3×, which does **not** follow from 12c-pre's "netting
understates" finding — that was measured for *varying* profiles. Netting a
constant subtracts a constant from the residual while the mixture's states are
discrete steps of `cap × cf`, so whether the netted residual crosses a step
decides the sign. Exhibited: two fleets sharing no parameter read an identical
9.6171 h because both leave eight 80 MW units needing 7 of 8 up.

---

## 1. What ships

**H1 — the fold replaces netting, inside `split_fleet`.**

`split_fleet` computes its ordering as today. A profiled unit that (a) would
land in `netted`, and (b) carries a **finite constant** series, is converted to
a two-state unit of capacity `cap × cf` with `profile = None`, and joins
`table`. Units that would be `mixed` are untouched. `fleet_and_residual` is
untouched.

**H2 — v1's H3 reversal is WITHDRAWN.** 12h's `deterministic` bucket stands.

v1 proposed folding a rate-zero constant-series unit into `table` and moving
12h's ★H2c, on the argument that "both routes are exact". **That argument was
false and the review caught it**, measured on the §0 fixture with `cf = 0.833`:

| | 12h `deterministic` | v1's fold to `table` | truth |
|---|---|---|---|
| LOLE at load 84 | **8.4000 h** | 5.8800 h | 8.4000 h |
| EUE at load 83.5 | **1.6800** | 2.9400 | 1.6800 |
| FMECA `note` | present | **dropped** | — |

`deterministic_output` nets in float and is grid-free; the table is not. The
true answer at load 84 is `0.05 × 168 = 8.40 h` and the fold reads 5.88 — a
30 % understatement — while silently deleting `DETERMINISTIC_ROW_NOTE`. So a
rate-zero unit is excluded from the fold, `deterministic` takes precedence,
and 12h's ★H2c **stands unmodified**.

**H3 — the disclosure is plumbed, or the phase deletes one.**

The review found that `folded_units` is built from `units` in
`routers/results.py`, which `split_fleet` never touches, so a folded unit
would appear in **no** list at all — measured, a 12-unit constant fleet
produced `profile_units []`, `folded_units []`, `fidelity_note None`. Those
assets are named today. So:

- `FleetSplit` gains a fifth field, `folded`, set in `_screen_block`;
- `screening_analysis`'s merge rebuilds it **as a field, not a merge-only
  extra** — the shape 12h's v4 review forced for `deterministic`, because a
  merge-only field is empty on every single-block network;
- `/copt`'s `folded_units` is built from `units` (static folds, 12h) **and**
  from `split.folded` (constant-series folds), each with its `source`;
- `fidelity_note` names the folded population, because the netting sentence
  is no longer true of them.

**H4 — the merge precedence gains the missing subtraction.**

`table_names` subtracts `mixed` and `netted` but not `det_names`. Under H1 a
unit can be `table` in one block and `deterministic` in another (constant in
one period, varying with `q = 0` in another), and the review measured it
**double-reported**: `table ['u1','t1']` while `deterministic ['u1']`, with
`fleet.units` reading 3 against 4 named. Fixed explicitly, with a ★.

**H5 — `split.table[i].capacity_mw` means one thing on both paths.**

Measured to mean two: the single-block path reports the **folded** capacity
(80.0) and the multi-block path the **nameplate** (100.0), and
`folded_constant` survives only on the single-block path. v2 reports the
**nameplate** on both, with the factor carried in `folded_units` — an asset's
capacity is its nameplate, and the payload must not contradict itself.

**H6 — preflight, specified rather than gestured at.**

| code | fires when | says |
|---|---|---|
| `profile_and_outage_modelled` (existing) | asset-typed, `q > 0`, informative series **that was not folded** | unchanged sentence — the unit is mixed exactly per hour, which is still true of it |
| `constant_availability_convolved` (new, warning) | asset-typed, `q > 0`, constant series **that was folded** | this unit's availability is constant, so it is modelled as a two-state unit at `nameplate × cf` rather than mixed per hour; the answer is strictly closer to exact than the netting it replaces, and is subject to the table's rounding increment |

v1's claim that a profile-kind split "reverses 12c-pre's Q4 for
carrier-default constant-series units" is **withdrawn**: the population is
gated on `source == "asset"`, so carrier-default units are silent today
regardless of kind, and no split changes that. Reversing it would require
widening that gate, which this phase does not do.

**H7 — the dead line is removed.** v1's "`capacity_series` is scaled with it"
can never execute: `split_fleet`'s only caller is `_screen_block`, whose
per-block branch passes `capacity_series=None` and whose single-block branch
is taken only when no unit has one. Dropped.

---

## 2. Tests

- ★ I1a: the §0.3 table — max and mean absolute error over the swept loads,
  fold-netted against shipped, on **off-grid** `cf = 0.833`. The exact
  reference is computed in the test by raising `k_exact`, never hard-coded.
  Bite: fold unconditionally (mean error rises to shipped's).
- ★ I1b: the control, now true by construction — a 3-unit fleet at `cf = 0.833`
  is **byte-identical** with and without the phase, because nothing is netted.
  Bite: fold every constant-series unit (reads 12.36 against 23.96).
- ★ I1c: exactness is **not** asserted. The pin is `fold-netted error ≤ shipped
  error` at every swept point, plus the on-grid fleet reading exactly 0.
  Bite: assert equality with exact — the test fails on shipped code, honestly.
- ★ I2a: a rate-zero constant-series unit stays in `deterministic` and reads
  **8.4000 h** at `cf = 0.833`, load 84, with its FMECA `note` intact.
  Bite: v1's H3 (reads 5.8800 h, note gone).
- ★ I2b: a NaN/±inf hour in an otherwise constant series is not folded.
- ★ I3a: `/copt`'s `folded_units` names the constant-series folds with
  `source: "constant_series"` **and** the static ones with `source: "static"`,
  on single-block and multi-block fixtures, driven through `get_copt`.
  Bite: build the list from `units` alone (the constant folds vanish).
- ★ I3b: `fidelity_note` names the folded population.
- ★ I4a: the double-report case — a unit constant in one block and varying
  with `q = 0` in another is in exactly ONE merged bucket, and the buckets sum
  to `fleet.units`. Bite: drop `- det_names`.
- ★ I5a: `split.table` reports the **nameplate** on BOTH paths.
- ★ I6: the untouched surfaces — `portfolio_population`, `elcc_candidates`,
  `snapshot_hash`, `mc_adequacy` under one seed — pinned by literal values
  recomputed in the test from `fleet_and_residual`, not by a
  before/after comparison that cannot be expressed in a single tree.
- ★ I7a–I7b: preflight's two codes, one test each.
- Re-measured pins: the review ran the adequacy suite under a v1 prototype and
  got **1 failed, 208 passed** across twelve files, the single failure being
  12h's ★H2c. Under v2 that test is **not** modified (H2), so the expected
  blast radius is **zero shipped pins** — to be confirmed under a v2 prototype
  before build, not asserted here.

## 3. Live — S32

Build a cap-saturating constant-series fleet over the API, read `/results/copt`,
assert the EUE against the unit-suite pin, `fleet.folded_units` carrying
`source: "constant_series"`, and that the folded assets have left
`profile_units`. Plus the negative: `/results/mc` is unchanged by this phase.

## 4. Out of scope, recorded

- **Making the fold exact.** That means a finer `delta_mw` for folded units, or
  a per-unit grid, and it is a different phase with a different risk.
  §0.3 claims better-than-netting, and that is all it claims.
- **A general run-length fold** for piecewise-constant series.
- **Whether varying-profile fleets can also overstate.** §0.4's sign-varying
  result is asserted for constant series only. Saying it might generalise is
  not measuring it.

## 5. The review record

| plan | verdict | findings | what it was rejected on |
|---|---|---|---|
| v1 | **REJECT** | 9 (3 blockers) | the fold is not exact — the §0 fixture was on-grid and could not see it; folding a unit that would be *mixed* regresses the ordinary non-saturating fleet (−48 %); the H3 reversal was an accuracy regression sold as a relabelling (8.40 → 5.88 h) that also deleted a FMECA note; `folded_units` is built from a list the fold never touches, so the phase deleted a disclosure; the merge double-reports; `split.table`'s capacity means two different things; a dead line; preflight underspecified and its "reversal" claim unfounded |
| v2 | *(awaiting review)* | — | — |

## 6. What v1 got right, kept

The structural claim of v1 §2 survived review intact and is retained: the fold
belongs in `split_fleet`, not in `fleet_and_residual`. `split_fleet`'s only
caller is `_screen_block`, itself called only by `screening_analysis`, called
only by `/copt`; `portfolio.py`, `elcc.py`, `mc.py` and `coupling.snapshot_hash`
all read `fleet_and_residual`'s list, which the fold never reaches. The
membership loss that produced three of 12h's blockers — measured again here,
`members: const, zero, vary` becoming `members: vary` under an upstream fold —
genuinely does not arise. So does H2 of v1: the zero-constant case needs no
decision, because no unfold exists to divide by zero.

**The rule, restated:** a correction that belongs to one consumer's arithmetic
is applied in that consumer, not in the shared snapshot every consumer reads.
