# Phase 12c-pre — a generator with BOTH a profile and outage data (plan, v2)

Supersedes v1 (`2026-09-02-fmea-phase12c-pre-profile-outage-unit.md`),
rejected: its COPT treatment — net a profiled unit's *expected* output —
was measured to understate LOLE by up to 3.1× and a unit's criticality by
14×, one-signed, because LOLP is convex in the shortfall (Jensen). v1's MC
half was verified exact and byte-identical on the no-profile path and is
kept. v2 replaces the COPT half with the exact per-hour mixture, drops the
constant-profile fold-in (SERIOUS 5), uses a warning not a nonexistent
INFO severity (SERIOUS 4), adds a membership-level pin the anchors cannot
provide (SERIOUS 6), and answers every minor by number.

## 0. The defect, restated

`copt.fleet_and_residual` (`copt.py:341-359`): a generator with resolvable
outage data becomes a two-state `CoptUnit` at firm capacity and its
`p_max_pu` **time series is discarded**; one without becomes must-take,
netted at `profile × cap`. A 100 MW wind farm at CF 25 % with q = 0.04 is
96 MW of firm capacity to the COPT and the MC while the reserve margin
credits it at 22.5 (Phase 12a's finding, `2963fc8`). Where it occurs: a
GUI-native project with a hydro (or any defaults-library carrier)
Generator carrying an inflow or availability *series*; and any project
where a user entered outage data on a wind or solar farm. A PyPSA-Eur
import is a different case — nuclear with a **static** capacity factor —
and §1.3 treats it separately.

12a **warned**. This phase **models** the series case exactly, in both
engines.

## 1. One representation; two engines; one expectation, exactly

### 1.1 The representation

`CoptUnit` gains `profile: np.ndarray | None = field(default=None,
compare=False, hash=False)` — availability fraction per hour, `(H,)`. The
membership rule:

| generator | outage data | `p_max_pu` | today | after |
|---|---|---|---|---|
| must-take | none | any | netted at `profile × cap` | **unchanged** |
| thermal | yes | none / series ≡ 1.0 | two-state at `cap` | **unchanged** |
| thermal, static `< 1` | yes | static | two-state at `cap` (static ignored) | **unchanged** — §1.3 |
| profiled + outage data | yes | **varying series** | two-state at `cap`, profile discarded | **profile attached** |

"Varying" is Phase 12b's predicate (`max − min > 1e-9` over finite values,
`solver_service.py`), so the engines and the margin cannot disagree about
what has a profile. A constant series is not varying and is left as today
(§1.3 says why).

### 1.2 The engines

**Sequential MC — exact.** `sample_capacity` accumulates
`np.add(acc, cap, out=acc, where=state_path)` (`mc.py:~431`) with a scalar
`cap`; for a profiled unit `cap` is the `(H, 1)` vector `profile × cap`,
broadcast over draws: UP is the profile's value that hour, DOWN is zero.
The chain, its stream and its consumption are untouched. **Verified by the
v1 review**: a no-profile fleet hashes identically before and after; with
one unit profiled every other unit's contribution is bitwise unchanged; the
`q ≤ 0` branch (`acc += cap`) broadcasts. Accumulation is elementwise, not a
reduction, so there is no order sensitivity.

**COPT — the exact per-hour mixture.** The table is built ONCE over the
fleet **without** the k profiled units. Per hour, with `a_{i,h} =
profile_i(h) × cap_i` and each unit up with probability `1 − q_i`:

```
LOLP_h = Σ_{s ∈ {0,1}^k}  P[s] · ( 1 − S( r_h − Σ_i s_i · a_{i,h} ) )
EUE_h  = Σ_{s ∈ {0,1}^k}  P[s] · ES( r_h − Σ_i s_i · a_{i,h} )
```

where `S` and `ES` are the without-unit table's survival and expected
shortfall (`CapacityDistribution.survival`, `.expected_shortfall`) and
`P[s] = Π_i (1 − q_i)^{s_i} q_i^{1 − s_i}`. That is `2^k` vectorised
evaluations over H — for one unit, the two `hourly_adequacy`-style calls
v1's own attribution proposed. It is **exact**: it is the law of total
probability over the profiled units' independent states, and the
without-unit table is exact by construction. Measured (v1 review): it lands
inside the MC's 95 % CI on all three fixtures where v1's netting fell
outside it on two.

**The cap on k.** Exact for `k ≤ K_EXACT = 8` (256 evaluations of O(H)).
Beyond that, the `K_EXACT` profiled units with the largest `mean(a_{i,h})`
are mixed exactly and the remainder are netted at expected output, and the
`/copt` payload's `fidelity` names **how many and which** were netted — the
approximation is then a disclosed, bounded choice on the smallest units,
not the rule. §7 Q1 asks whether a per-hour convolution of the remainder
(`O(H · (k − K) · C/Δ)`) should replace netting; it is implementable but
not vectorisable across hours.

**Reserve margin — unchanged, and no refactor.** It computes
`(1 − q) · mean(profile over the window)` already (v1.1(1)). v1's "one
helper both read" and its A6 are withdrawn: the margin's series *is* the
expectation of the mixture's per-hour availability, and a contract pin
(A6′) asserts value equality on a fixture rather than pretending a bite
exists.

So: MC exact; COPT exact up to the disclosed cap; and the margin at the
window mean of the same expectation.

### 1.3 The static `p_max_pu < 1` case is deferred — and is a finding

v1 folded a static `p_max_pu < 1` into `capacity_mw` "exactly". It is not
exact, because the field carries two incompatible meanings in the wild: a
typed capacity factor on a farm (12a's "commonest way this is entered"),
and PyPSA-Eur's `nuclear_p_max_pu.csv` — a historical CF table that
**already contains forced outages** — written to the static column
(`config.default.yaml:429`, `add_electricity.py`). Folding it in and then
applying `q` counts those outages twice on every PyPSA-Eur import with
nuclear, and nothing in the gates could see it. So this phase leaves the
engines' treatment of the static column **unchanged** (ignored, as today)
and re-scopes 12a's warning to it (§2). **Recorded as a finding against the
reserve margin**, which already folds the static column in (v1.1(1),
`avail_static`) and therefore already double-counts on those imports; its
adjudication — a per-asset "this CF includes outages" flag, or a documented
convention — is its own item, not this phase's. §7 Q3.

## 2. What else the rule touches

- **Attribution** (`attribute_criticality`): a profiled unit's row is
  `ΔEUE_i = EUE(mixture as-is) − EUE(mixture with s_i ≡ 1)` — the same
  `2^k` machinery with unit *i*'s state fixed up. This agrees with the
  deconvolve-and-shift rows by construction (v1 review: 361.9 vs 400.1
  on the seasonal fixture, the difference being that the unit's perfect
  capacity is genuinely `a_{i,h}`, not `cap`), and it is **continuous across
  the varying threshold** (A7). `f_i` from `q` and MTTR is unchanged.
- **ELCC**: `kind="generator"`, exclusion by position, unchanged.
  `nameplate_mw = max_h(a_{i,h})` in `_resolve` and `elcc_candidates`
  (`elcc.py:154, ~353`) — the firm block dominates the unit hour by hour
  and `q` only makes the removed unit weaker, so the dominance tripwire
  holds; a `(1−q)`-derated peak would make `not_bracketed` reachable on the
  unit's best hour. A **zero-peak profile is excluded** from candidates, as
  the vre branch already excludes it (`elcc.py:166-168`).
- **`/copt` route**: `fleet.must_take` is computed from the membership
  walk's must-take set, not `n_elec_gens − len(units)` (v1 review M12).
  Payload gains `profile_units: [names]`, `netted_beyond_cap: [names]`, and
  a `fidelity` sentence.
- **`/mc`**: `profile_units: [names]`.
- **Coupling and margin loops, frontier, sweep** — inherit.
- **`keep_zero_capacity`** — `profile × 0 = 0`.
- **The 12a warning is re-scoped, not retired.** Its *series* branch
  (`_profile_is_informative` on a column) is replaced: the profile is now
  modelled, so that case emits a **`warning`**-severity (there is no other
  severity on the wire) disclosure, `profile_and_outage_modelled`, naming
  the units and saying how they are modelled — *"outages sampled on the
  availability series; the COPT mixes them exactly"* — emitted from the
  **membership walk**, so it fires on carrier-default-only networks too
  (v1 review M12). Its *static* branch stays, re-worded: *"a static
  `p_max_pu < 1` on a unit with outage data is not applied by the COPT or
  the MC. If it is an availability, enter it as a time series; if it is a
  capacity factor that already includes outages, set q = 0."* S21 is
  updated to the new codes, not removed.

## 3. Payloads, copy, specs

As v1 §3–§4 with these changes: `fidelity` on `/copt` states the mixture
and, when k > K_EXACT, the netted remainder; the worksheet's capacity
column is nameplate (no fold-in, so no ambiguity); FMEA spec §5.3 amended to
"profiled occurrence units mixed per hour over their outage states" and
§3.1(3)'s v1-error list cited as the reason netting was rejected; MC spec
→ v1.4 (`CoptUnit.profile`, the broadcast, the nameplate rule, the zero-peak
exclusion); 12a plan superseded note.

## 4. Gates the anchors cannot provide

The four anchors build `MCInputs` by hand and bypass `fleet_and_residual`
(`test_adequacy_benchmarks.py:285-288`, "deliberately NOT"), so they pin
`build_copt` / `hourly_adequacy` / `sample_capacity` only. Two pins are
added so the membership changes are checked, not assumed:

- **M1 — membership pin.** `fleet_and_residual` on one fixture carrying a
  must-take farm, a thermal unit with static `p_max_pu = 0.9`, a thermal
  unit with an all-ones column, and a farm with a varying series and outage
  data: a hash of `(name, capacity_mw, q, profile-hash-or-None)` per unit
  plus the residual, computed on `1bce9da` for the three rows that must NOT
  change, asserted equal; the varying row asserted to carry its profile.
- **M2 — scalar-path pin (v1's A2, specified).** `sample_capacity` on the
  RBTS fleet, H = 8736, draws = 64, seed = 20260828 hashes to
  `aa4b3c0f25c70b6fc0bb094a071c96c28704c9c9a149e1d6a9143c148cdf2394`
  (numpy 2.4.6); skipped with the version named when the numpy major
  differs, since stream stability across majors is NEP 19 best-effort.

## 5. Acceptance (each ★ with a bite; restores by hash)

★ **A1 — MC uses the profile.** One 100 MW unit, q = 0.5, profile
`[1,0,1,0]`, residual 60 MW: hours 1, 3 short with probability 1; hours 0,
2 with ≈ 0.5. *Bite: ignore the profile → hours 1, 3 short with 0.5.*

★ **A3′ — the COPT mixes exactly.** On the RTS-79 fleet plus the 500 MW
q = 0.05 mild-profile unit, COPT LOLE equals the hand-computed mixture
`Σ_s P[s]·(1 − S(r_h − s·a_h))` to 1e-12, and is **3.97 h** — not v1's
1.28 h and not today's 3.88 h. *Bite: net at expected output → 1.28 h.*
Its EUE equals the MC's within the MC's CI at 1500 draws (a soft check,
printed, not the gate).

★ **A4′ — a constant series and a static column are left alone.** The
static-0.9 thermal unit and the all-ones-column unit are two-state at
`cap`, `profile is None`. *Bite: fold the static column in.* (M1 pins the
same thing by hash.)

★ **A5′ — expectation, pooled.** q = 0.05, D = 2000: the mean over hours of
`(sampled mean availability − (1−q)·a_h)` is within its pooled standard
error, and no hour exceeds its Bonferroni bound at α = 0.01/H. *Bite: apply
the profile without the state → +5 MW every hour.*

**A6′ (pin, not a bite)** — the margin's `derate` for the varying farm
equals `mean((1−q)·profile over the gross window)` on the fixture; the
margin is not refactored.

★ **A7 — attribution is continuous across the threshold and exact.** The
500 MW unit at a profile of `1 − ε` (constant, two-state at `cap`) and at
`1 − ε` with one hour at `1 − 2ε` (varying, mixture): ΔEUE within 0.5 % of
each other, and the varying row equals `EUE(mixture) − EUE(s_i ≡ 1)`.
*Bite: net at expectation → the varying row drops 14×.*

**A8 (pin)** — ELCC nameplate = `max_h(a_{i,h})`; a zero-peak profile is
not a candidate.

★ **A9** — the four anchors at their pinned tolerances (existing tests),
plus M1 and M2.

★ **A10 — the re-scoped warnings.** Preflight on 12a's fixture: the
series farm gets `profile_and_outage_modelled` (`warning`), the static case
gets the re-worded static warning, and the old `outage_shadows_profile`
code is absent. *Bite: leave the old series branch in.* Also on a network
whose outage data is entirely carrier-default. *Bite: keep the
outage-column trigger.*

★ **A11′ — the 4× disagreement is closed, live (S24), against the exact
value.** 12a's two-farm fixture: COPT LOLE with the shadowed farm equals the
mixture value the test computes independently (**4.76 h** on that fixture;
the must-take farm alone is 4.40, today's shadowed value is 0.80), the MC's
`profile_units` names it, preflight carries the disclosure. *Bitten live:
with the profile dropped, the COPT reverts to 0.80.*

## 6. Gates

The four anchors; M1, M2; full backend tree identical to master minus the
re-scoped 12a tests; frontend; S15–S20, S21 (updated), S22–S24 live on one
port-verified server; every ★ bitten, restores by hash.

## 7. Open questions for the review

1. **K_EXACT = 8, and what happens beyond it.** Net the remainder at
   expected output (disclosed), or convolve the remainder per hour
   (`O(H·(k−K)·C/Δ)`, exact, not vectorisable)? On a clustered network how
   many profiled occurrence units are there in practice?
2. **The mixture over `2^k` states** costs `2^k · H` evaluations of the
   survival function. At K = 8 and H = 8760 that is 2.2 M evaluations per
   metric — is the route still "milliseconds", and if not, does the
   `/copt` route's synchronous contract survive it?
3. **The static-column finding against the margin.** Adjudicate as its own
   item, or fold into this phase? My lean: its own item, because the
   resolution (a per-asset flag) is a data-model change.
4. **Disclosure as a `warning`.** Every hydro-with-series project will see
   it. Is that the right weight, or should the disclosure be a fidelity
   line on the payload only, with no preflight issue at all?
