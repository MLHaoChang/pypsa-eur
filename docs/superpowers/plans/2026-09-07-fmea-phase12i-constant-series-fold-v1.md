# Phase 12i — a CONSTANT availability series is folded into capacity, inside the COPT's own split (plan v1)

**Status:** plan v1, for review before a line is written.
**Closes:** the item Phase 12h recorded out of scope
(`2026-09-06-fmea-phase12h-static-cf-includes-outages-v6.md` §5). 12h folded
the STATIC `p_max_pu` cell; a constant *series* is still mixed per hour, which
is exact in principle and inexact in practice as soon as the fleet saturates
`K_EXACT`.

**The headline claim of this plan is that 12h's own sketch of 12i was the
wrong design, and a narrower one delivers all of its measured value with none
of its three blockers.** §2 gives the measurement.

---

## 0. The premise, measured

A constant availability needs no per-hour mixture: a unit at a constant `cf`
with outage rate `q` **is** a two-state unit of capacity `cap × cf`. The
shipped COPT does not know that, so such a unit occupies a slot in the `2^k`
exact mixture, and past `K_EXACT = 8` the surplus units are netted at their
expectation.

Nine to twelve units, each 100 MW at a constant `p_max_pu = 0.8`,
`q = 0.05` EFORd, 168 h flat load. "Exact" is the same fleet with `k_exact`
raised past the fleet size, so every unit is mixed and nothing is netted:

| fleet | shipped | folded | exact | shipped vs exact |
|---|---|---|---|---|
| 9 units, load 600 | 9.6171 h | **11.9635** | 11.9635 h | **−19.6 %** |
| 9 units, load 700 | 56.5454 h | **62.1181** | 62.1181 h | **−9.0 %** |
| 12 units, load 800 | 9.6171 h | **3.2875** | 3.2875 h | **+192.5 %** |
| 12 units, load 900 | 56.5454 h | **77.2195** | 77.2195 h | **−26.8 %** |
| 3 units, load 200 | 23.9610 h | 23.9610 | 23.9610 h | 0 % (nothing netted) |

Two facts in that table, and the second is the one worth the phase.

**The fold is exact — 0.000 % on every row**, not "accurate to within". That
is not a claim about a numerical method; it is the definition of a two-state
unit. The last row is the control: a fleet that never saturates `K_EXACT` is
already exact, and the fold does not move it.

**And the shipped error is not a bias, it is a sign-varying discretisation
artifact.** 12c-pre measured that netting a *varying* profile at its
expectation understates LOLE (convexity), and that finding is sound. It does
not carry to a constant series: here netting subtracts a **constant** from the
residual, while the mixture's states are discrete steps of `cap × cf`, so
whether the netted residual crosses a step boundary decides the sign and the
size. **+192.5 % is an overstatement of nearly 3×.**

The mechanism is checkable by hand, and I checked it: the 9-unit load-600 case
nets one unit (76 MW) leaving residual 524, and the 12-unit load-800 case nets
four (304 MW) leaving residual 496. Both fleets then have eight mixed units of
80 MW each, so both need 7 of 8 up to survive — the same threshold, hence the
**identical** 9.6171 h in two rows of the table that share no other parameter.
That is the artifact, exhibited.

**What this phase is NOT worth.** 12h's `deterministic` bucket already handles
the *flagged* half of this population exactly, and the sequential MC samples
on the series and is exact already. The remaining value is precisely the
**unflagged, cap-saturating** constant-series fleet in the COPT.

---

## 1. What ships

**H1 — the fold lives in `split_fleet`, not in `fleet_and_residual`.**

A profiled unit whose series is constant and finite is converted, *inside the
split*, to a two-state unit of capacity `cap × cf` with `profile = None`, and
therefore lands in `table`. `capacity_series` is scaled with it. The unit
records `folded_constant = cf`, and the `folded_units` payload — which 12h
shipped with a `source` field for exactly this — gains `source:
"constant_series"`.

`fleet_and_residual` is **untouched**. That is the whole design, and §2 is why.

**H2 — the zero case needs no decision, because there is no division.**

12h recorded the blocker it expected 12i to inherit: `series_is_informative`
is True for an all-zero series (the ordinary "off for this study" idiom, and
`test_A8`'s own `wind_zero` fixture), so a fold to `folded_constant = 0.0`
would make the portfolio's unfold a division by zero. Under H1 the portfolio
never sees a folded unit, so the division never exists. Measured: three units
at a constant 0.0 convert to 0 MW table units and `screening_analysis`
returns normally (LOLE 168.0 h on a load the fleet cannot serve — which is the
right answer for a fleet that is switched off).

**H3 — what happens to a unit that is BOTH constant-series and rate-zero.**

12h routes a rate-zero profiled unit to `deterministic`, netted at full
availability, and pins it (★H2c: a flagged constant-series unit reads exactly
168.0 MWh on the §0 fixture). If the constant fold runs first, that unit
becomes a `table` unit of `cap × cf` at `q = 0` instead — a deterministic
capacity shift, equally exact.

**This plan folds first, and the pin moves.** The reason is not arithmetic —
both routes are exact — but honesty of the disclosure: after the fold the unit
genuinely has no profile, so reporting it under a bucket named for profiled
units, with `fidelity_note` counting it as one, describes a fleet the numbers
did not come from. 12h's ★H2c is rewritten to assert the same 168.0 MWh
through `table` and `folded_units`, and the reversal is stated here rather
than discovered by whoever reads the diff.

**H4 — the merged split must not report a folded capacity as the asset's.**

`screening_analysis`'s per-block merge rebuilds `FleetSplit` from name sets
over the ORIGINAL `units` list, so a folded unit would be reported at its
**unfolded** capacity while the math used the folded one. That is the right
choice for the split (it names assets, and an asset's capacity is its
nameplate) but it must be *deliberate*, and `folded_units` must carry the
factor so the payload is self-consistent. A unit folded in one block and
mixed in another — possible on the per-block path, where a horizon-varying
series can be constant within a block — is reported by the existing
precedence, and the fold does not change that the block's own math was right.

**H5 — preflight's sentence about a folded unit becomes false, and splits.**

`profile_and_outage_modelled` says the COPT "mixes the unit exactly per hour".
After H1 that is false of a constant-series unit: it is convolved, not mixed.
The population splits by profile kind, with the constant half told what
actually happens to it. **This reverses 12c-pre's Q4 decision** for
carrier-default constant-series units, which is stated here as a reversal
rather than inherited silently.

---

## 2. Why this is not 12h's sketch of 12i, measured

12h §5 sketched 12i as the static fold generalised: fold in
`fleet_and_residual`, then repair the consequences downstream. Its own reviews
produced three blockers from that half, and all three are properties of
folding *upstream*:

| | fold in `fleet_and_residual` (12h's sketch) | fold in `split_fleet` (this plan) |
|---|---|---|
| portfolio membership | the gate is `profile is not None`, so folded units **leave the population** — measured below | untouched: the portfolio reads `inputs.units`, which never sees a fold |
| `capacity_basis_mismatch` | a member reporting folded capacity is refused against the margin's built capacity, for every one | cannot arise |
| zero-constant unfold | `1/0` on `/results/elcc_portfolio` | no unfold exists |
| ELCC `unit_nameplate_mw` | changes for every constant-series asset | unchanged |
| `snapshot_hash` / MC draws | capacity changes ⇒ every hash moves, CRN baselines invalidate | unchanged |
| accuracy delivered | exact | **exact** (0.000 % on all five fleets) |

The membership loss is measured, not argued. On a three-generator network
(`const` at 0.8, `zero` at 0.0, `vary` alternating), `portfolio_population`
returns all three today; under an upstream fold it returns **one**:

```
TODAY            members: const(100.0), zero(100.0), vary(100.0)   unbuilt: []
UPSTREAM FOLD    members: vary(100.0)                              unbuilt: []
```

Two members vanish silently — not into `unbuilt`, which is where a
legitimately absent asset goes, but out of the payload altogether.

**The rule this phase is proposing, stated generally:** a correction that
belongs to ONE consumer's arithmetic should be applied in that consumer, not
in the shared snapshot every consumer reads. The static fold in 12h had to be
upstream, because the margin reads the static cell too and the two had to
agree. Nothing outside the COPT's table construction is wrong about a constant
series today — the MC is exact, the margin's derate reads the series mean,
the portfolio wants the profile — so nothing outside it should move.

---

## 3. Tests (every ★ with its bite)

- ★ I1a: the four-fleet table of §0, all rows pinned, against the
  `k_exact`-raised exact reference **computed in the test** rather than
  hard-coded — so the pin cannot drift from the definition it claims.
  Bite: drop the fold (rows 1–4 read the shipped column).
- ★ I1b: the control row — a 3-unit fleet that never saturates `K_EXACT` is
  **byte-identical** with and without the fold. Bite: fold unconditionally
  into `mixed` rather than `table`.
- ★ I1c: a **near**-constant series (one hour differing by 1e-9, and one by
  1e-3) — the first folds, the second does not. Bite: use `==` on the whole
  array, or a tolerance loose enough to swallow a real profile.
- ★ I2a: the zero-constant fleet returns from `screening_analysis`, and
  `/results/elcc_portfolio` still serves (the case 12h recorded as 12i's
  blocker). Bite: fold upstream — a 500.
- ★ I2b: a NaN or ±inf hour in an otherwise constant series is **not** folded
  (`_occurrence_profile` maps a non-finite hour to availability 0, so the
  series is not constant, and folding its "constant" would credit the unit in
  an hour the engines score at zero). Bite: read `profile[0]` alone.
- ★ I3a: the §0 fixture's flagged constant-series unit reads the same
  **168.0 MWh** through `table` + `folded_units` that 12h read through
  `deterministic` — the reversal, pinned on both sides of the equality.
- ★ I4a: the multi-period merge reports a folded unit at its **unfolded**
  capacity in `split.table`, while `folded_units` carries the factor.
  Bite: report the folded capacity — the payload contradicts itself.
- ★ I4b: a series that varies over the horizon but is constant **within** a
  block folds for that block only, and the block's LOLE matches the exact
  reference. Bite: judge constancy on the horizon before slicing.
- ★ I5a–I5b: preflight's split, one test per half, and the reversal for
  carrier-default constant-series units asserted explicitly.
- ★ I6: **the untouched surfaces** — `portfolio_population`, `elcc_candidates`
  `nameplate_mw`, `snapshot_hash` and `mc_adequacy` under one seed are all
  byte-identical before and after this phase, on a fixture carrying a
  constant series, a zero series and a varying one. This is the test that
  makes §2's claim a fact rather than an intention. Bite: fold upstream.
- Re-measured pins: 12h's ★H2c (moves per H3) and any adequacy-suite pin
  whose fixture carries a constant series — to be enumerated by running the
  suite under a prototype **before** this plan is accepted, not after.

## 4. Live — S32

The COPT payload is the only surface that moves, so S32 is small: build a
cap-saturating constant-series fleet over the API, read `/results/copt`, and
assert the EUE, `fleet.folded_units` carrying `source: "constant_series"`, and
that `fleet.profile_units` no longer names the folded assets. Plus the
negative: `/results/mc`'s numbers are unchanged by this phase.

## 5. Out of scope, recorded

- **A constant series on a MUST-TAKE unit.** It is already netted at
  `static × cap` exactly once by `fleet_and_residual`'s must-take branch;
  there is nothing to fold.
- **Folding a piecewise-constant series** (constant within each of several
  runs, not within a block). The block path picks up the cases that align
  with period blocks; a general run-length fold is a different phase and has
  no measured defect behind it yet.
- **The `+192.5 %` case as a general claim about netting.** This plan asserts
  it for a constant series only. Whether varying-profile fleets can also
  overstate is unmeasured, and saying so is not the same as measuring it.

## 6. The review record

| plan | verdict | findings | what it was rejected on |
|---|---|---|---|
| v1 | *(awaiting review)* | — | — |
