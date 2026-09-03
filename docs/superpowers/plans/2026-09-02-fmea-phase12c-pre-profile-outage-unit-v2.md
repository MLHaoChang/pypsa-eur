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

---

## v2 REVIEW OUTCOME — accept with changes (2026-09-02)

An adversarial reviewer took the v2 plan with the attack surfaces named at
the end of v1's outcome. Verdict: **accept with changes**. Every finding
was re-run against the code and its probes before being recorded here
(`scratchpad/v2_p{A..J}*.py`, `v2_common.py`); all ten reproduce. What
the review could not break: the `2^k` mixture equals an independently
built per-hour table to 1e-14; the survival function's grid, negative and
beyond-table edges are handled; the MC broadcast is exact and the scalar
path byte-identical (M2 re-derives, 0.57 s, no `slow` mark needed); the
ELCC dominance tripwire holds pathwise in 12/12 trials with and without
storage; the deconvolve path equals the mixture path at a profile ≡ 1 to
5e-12; every line reference is correct; A1, A4′, A10 bites bite.

### Findings, verified

1. **SERIOUS — §1.1 row 3 is a cliff, and A7 was built where it is
   invisible.** A *constant* series at level L is left two-state at `cap`
   (level ignored); the same series with one hour 1e-8 lower is "varying"
   and enters the mixture at `L·cap`. Measured on RTS-79 minus one 400 MW
   unit plus the 500 MW q = 0.05 unit (`v2_pD_A7.py`):

   | level | constant series → at cap, LOLE | one hour 1e-8 lower → mixture, LOLE | ratio |
   |---|---|---|---|
   | 0.9 | 3.877 | 4.446 | ×1.15 |
   | 0.5 | 3.877 | 11.770 | ×3.04 |
   | 0.25 | 3.877 | 26.654 | ×6.87 |

   At level `1 − ε` the two paths agree to 0.000 % for every ε, so A7 as
   written passes and proves nothing. Worse, the reason v2 gave for
   leaving a constant series alone — §1.3's "the CF already contains
   outages" — is about the **static column** (`add_electricity.py:668-690`
   writes `nuclear_p_max_pu.csv` via `n.generators.update`, never a
   series); and the margin already honours a constant series' level
   (`solver_service.py:3525-3527` takes `mean(profile over the window)`
   for any series column; `profile_kind="constant"` only governs stashing
   and netting). Row 3 therefore makes the engines and the margin disagree
   by construction — the opposite of §1.1's intent.
2. **SERIOUS — A5′'s pooled gate fails on a correct engine.** With the
   pooled standard error taken as hours-independent (`sqrt(Σ SE_h²)/H`),
   the correct `sample_capacity` fails 3 of 4 seeds (z = +11.7, +11.3,
   +1.9, +13.6; `v2_pF2_pooled.py`, q = 0.05, D = 2000, H = 8760): hours
   within a draw are autocorrelated over ≈ MTTR, and the true SE from the
   D per-draw horizon means is 6.6× larger (z = +1.76, +1.75, +0.28,
   +2.08 — note the last still fails a 1.96 gate). The engine is unbiased
   (`v2_pJ_mcbias.py`, D = 20000: up-fraction 0.95003 ± 0.00023). The
   Bonferroni half is fine: 0/8760 exceedances on the correct engine, and
   the +5 MW bite is 10.3 SE so every hour fails it.
3. **SERIOUS — the re-worded static warning's two remedies both fail
   under v2's own rule** (`v2_pH_network.py`, 1000 MW nuclear, static CF
   0.8): "set q = 0" → `_is_set(0.0)` is True (`occurrence.py:89-96`),
   `source="asset"`, and the unit becomes a perfectly firm 1000 MW — worse
   than today's 980 MW expected; "enter it as a time series" → a constant
   series is not varying under row 3 → at cap, q = 0.02, CF ignored.
   Also, the static branch lives in `_check_shadowed_profiles`, called
   only from `_check_outage_params`, which `continue`s when the component
   has no `outage_rate_value` column (`validation_service.py:1795-1798`);
   an import has no outage columns (probe: `outage cols present=False`),
   so the warning **cannot reach the PyPSA-Eur nuclear import it is
   written for**.
4. **MODERATE — beyond `K_EXACT`, "bounded and disclosed" holds for LOLE
   but reproduces v1's Jensen defect on the netted units' own rows**
   (`v2_pC_cap.py`, 12 profiled units, 4 netted): LOLE bias 0.1–1.4 % on
   seasonal and wind fixtures, but a netted unit's ΔEUE with a *mild*
   profile is understated 6.8–13.3× (on near-zero absolute values in those
   fixtures — the ratio is the point, the MWh are not). Per-hour
   convolution of the remainder, grouped by distinct integer offset tuple,
   is exact and vectorisable — §7 Q1's "not vectorisable" was false — but
   it is cheap only when the tuples are few: 0.4–2.8 s at 16–155 groups,
   **99 s** at 6311 groups (RBTS + 12 × 20 MW wind). Not a uniform
   answer.
5. **MODERATE — the mixture is "milliseconds" only with a vectorised
   `S`/`ES`; `hourly_adequacy` is a Python `map`** (`copt.py:136-137`,
   ≈ 200 ms per call). `v2_pB_cost.py`, N = 300, C/Δ = 5447, H = 8760:
   vectorised mixture 1 / 6 / 89 ms at k = 1 / 4 / 8; through today's
   scalar path 0.5 / 3.7 / **51 s**. `CapacityDistribution` has no
   vectorised API; the plan asserted one. And Q2 counted one metric: the
   route computes N + 1 (attribution for every unit under the same
   mixture) — at k = 8 that is ≈ 30 s for 300 units, on top of today's
   ≈ 32 s (`deconvolve` is a Python double loop over C/Δ). The
   synchronous `/copt` contract is a **pre-existing** problem the FMEA
   spec's "milliseconds" already misstates; v2 makes it ≈ 2× worse at
   k = 8 and negligibly worse at k ≤ 4.
6. **MINOR — A3′'s fixture is misstated.** "RTS-79 plus the 500 MW unit"
   gives 0.588 h; the 3.97 h needs RTS-79 **minus one 400 MW unit**
   (`v2_pE_A3.py`: drop U400-1 or U400-2 → 3.9746; full fleet → 0.5882).
   "To 1e-12" holds (1.5e-14 across three orderings). v1's 1.28 and
   today's 3.88 re-confirmed.
7. **MINOR — S24 does not exist.** `qa_e2e.py` has `suite_S15`…`S23`;
   "S22–S24" was carried from v1. A11′ is implementable (`/api/results/copt`
   is live, S15 uses it; S21's fixture builds over HTTP) but must say
   "new S24". Its values re-confirmed: 4.76 / 4.40 / 0.80.
8. **MINOR — the `/copt` `must_take` rationale is stale.** Under v2
   profiled units stay in `units`, so `n_elec_gens − len(units)` does not
   miscount them; it miscounts **zero-capacity** generators
   (`v2_pI_musttake.py`: route 2, walk 1). The fix is right; the M12
   citation is not the reason.
9. **MINOR — §1.3 inverts the magnitudes.** The margin's double-count is
   real (`avail_static × (1 − 0.02)`, carrier-default q on an import with
   no outage columns — verified) but is 2 %; the engines' treatment of the
   same unit (CF 0.8 ignored → 980 MW expected vs the margin's 784) is a
   25 % error, and it is that which is being deferred. Record both.
10. **MINOR — Q4.** Warnings never gate (`solver_service.py:819-828`
    counts; `IssuesPanel.tsx:79` lists). A `warning` on every
    hydro-with-series project is safe but is noise; the fidelity line
    already discloses.

**Q1's empirical half, answered here:** `CARRIER_DEFAULTS` has no wind or
solar entry (deliberately), and PyPSA-Eur's run-of-river generators are
carrier `ror` (`add_electricity.py:288, 766`), which is not in the library
either (`hydro` there is a StorageUnit). So on an import the profiled
occurrence set is **empty** unless a user enters outage data by hand;
k > 8 needs hand-entered outage data on more than eight profiled farms.
`K_EXACT = 8` stands.

### v2.1 amendments (the changes required, in the reviewer's order)

**C1 — §1.1 row 3 replaced; the rule is "informative series".** A
profile is attached whenever the generator has a `p_max_pu` *series* column
that is not identically 1 over its finite values (`|v − 1| > 1e-9`
anywhere): constant-below-1, varying, or both. A constant series at level
L is then mixed at `a_{i,h} = L·cap`, which is exactly what the margin's
window mean credits it, so the cliff is gone and the engines and the
margin agree on the level by construction. NaN hours are availability 0 at
attachment (`np.nan_to_num(…, nan=0.0)`, one explicit line — Phase 12b's
rule 1, for the same reason: the margin nets a NaN hour as 0). The static
column stays deferred (§1.3). Phase 12b's *varying* predicate is untouched
— it governs stashing and netting for the window, a different question.
**A4′** becomes: the static-0.9 thermal unit and the all-ones-column unit
are two-state at `cap`, `profile is None`; a **constant-0.8 series** unit
carries a constant profile (bite: drop it to the all-ones class → the M1
hash for that row changes and its LOLE reverts to today's). **A7**
retargeted to level **0.5**: constant series at 0.5 vs 0.5 with one hour
at `0.5 − 1e-8`; LOLE within 0.1 % and ΔEUE within 0.5 % of each other
(both now mixtures), the varying row equal to `EUE(mixture) − EUE(s_i ≡
1)`. *Bite: leave the constant series two-state at cap → LOLE 3.877 vs
11.770.* M1's fixture adds the constant-0.8 row and pins it as attached.

**C2 — the static warning: no "q = 0" remedy; emitted from the walk.**
Code `static_p_max_pu_not_applied` (`warning`), emitted from the
membership walk in preflight so it reaches carrier-default-only networks
(the PyPSA-Eur nuclear import), for any occurrence unit whose static
`p_max_pu < 1 − 1e-9`. Message: *"a static `p_max_pu < 1` on a unit with
outage data is not applied by the COPT or the sequential MC, which model
the unit at nameplate × (1 − q); the reserve margin applies both. If it is
an availability, enter it as a time series (a constant series is
honoured). If it is a capacity factor that already includes outages, the
margin double-counts it and neither engine sees it — recorded as an open
item (§1.3)."* The old `_shadowed` static arm and the `outage_rate_value`
trigger go with the series arm.

**C3 — A5′ re-specified.** Pooled SE from the D per-draw horizon means
(`std(draw_means, ddof=1)/sqrt(D)`); gate at 3σ; seed pinned
(`seed=20260902`), so the gate is deterministic and the 3σ states the
intent. Bonferroni half unchanged.

**C4 — §1.2 costed and specified.** `CapacityDistribution` gains
`survival_vec(x: ndarray) → ndarray` and `expected_shortfall_vec(x)` on
cumsum tables: `j = clip(ceil(x/Δ − 1e-12), 0, n)`, `S = _surv[j]` with
`x ≤ 0 → 1`, `ES = x·F[j] − Δ·G[j]` with `F[j] = Σ_{k<j} p_k`,
`G[j] = Σ_{k<j} k·p_k`, `x ≤ 0 → 0`. **Pin A12:** on the RBTS table the
vectorised pair equals the scalar methods to 1e-12 on a grid that includes
exact multiples of Δ, negatives and beyond-table loads (the reviewer's
evaluator measured 0 / 7e-15). `hourly_adequacy` switches to the
vectorised pair (the anchors pin its values; **A12** also asserts the
switch equals the old `map` to 1e-12 on the RBTS residual). The mixture
uses them; **cost pin A13:** LOLE + EUE over 256 states, H = 8760, on the
300-unit / 5447-state table under 1 s (measured 89 ms). The attribution
loop is `N · 2^k` evaluations and is **recorded, not gated**: ≈ +30 s at
k = 8 for 300 units on top of today's ≈ 32 s. The `/copt` route's
synchronous contract at that size is a pre-existing defect: added to the
hardening backlog beside the abort routes, and the FMEA spec's
"milliseconds" is corrected to what is measured. Beyond `K_EXACT`: the
remainder is **netted at expectation** (per-hour convolution rejected on
finding 4's 99 s case), and the payload says so on the rows: the
`netted_beyond_cap` names carry a per-row `note` that their ΔEUE is
understated by netting (v1's measured 14×), and `fidelity` repeats it.

**C5 — wording.** A3′: "RTS-79 minus one 400 MW unit plus the 500 MW
q = 0.05 unit → 3.97 h". A11′: "new S24". `/copt` `must_take`: computed
from the walk because the subtraction miscounts zero-capacity generators
(finding 8), not M12.

**Q4 decided (finding 10).** `profile_and_outage_modelled` is a preflight
`warning` only when the unit's outage `source == "asset"` — the user typed
the data and deserves to be told how it is used (12a's premise). For
carrier-default sources (a hydro carrier with an inflow series) there is
no preflight issue; the `/copt` and `/mc` payloads' `profile_units` and
`fidelity` carry the disclosure. **A10** becomes: 12a's fixture (asset
source) gets the warning; the carrier-default-only network gets **no**
preflight issue and its `/copt` `profile_units` names the unit (bite:
emit for carrier-default too; bite: emit from the outage-column trigger
instead of the walk → the asset case on a network without the column
goes silent). The static warning fires for either source.

**Finding 9 recorded** in §1.3 as amended: the deferred item is two
errors of different size on the same unit — the engines' 25 % and the
margin's 2 % — and its adjudication (a per-asset "this CF includes
outages" flag) is a data-model change outside this phase.

Implementation proceeds on v2 + these amendments; the shipped code gets
its own review, as Phase 12b's did.

---

## SHIPPED — v2.1 as implemented (2026-09-02)

### What shipped

- `services/adequacy/copt.py`: `CoptUnit.profile` (`field(default=None,
  compare=False, hash=False, repr=False)`); `K_EXACT = 8`;
  `series_is_informative` (not identically 1 over finite values);
  `_occurrence_profile` attaches the series in `fleet_and_residual` with
  NaN → 0 in one line; `occurrence_units(n)` (the walk, for preflight);
  `CapacityDistribution.survival_vec` / `expected_shortfall_vec` on cumsum
  tables (`ES = x·F[j] − Δ·G[j]`); `mixture_hourly` (the `2^k` mixture,
  vectorised over H, `fixed_up` for attribution); `hourly_adequacy` on the
  vectorised pair with `mixed=`; `split_fleet` / `FleetSplit` /
  `netted_expectation`; `attribute_criticality(..., mixed=, netted=)` with
  `note` on netted rows; `fidelity_note`; `screening_analysis` (the one
  call the route makes); `build_copt` **refuses** a profiled unit.
- `services/adequacy/mc.py`: `sample_capacity` broadcasts `(H, 1)
  = profile × cap`; wrong length is a `ValueError`.
- `services/adequacy/elcc.py`: `unit_nameplate_mw` (best hour); candidates
  exclude a zero-peak profiled unit; `_resolve` uses it.
- `routers/results.py`: `/copt` via `screening_analysis`; `must_take` from
  `must_take_generators` (the walk); `fleet.profile_units`,
  `fleet.netted_beyond_cap`, `fleet.k_exact`, `fidelity_note`; per-mode
  `note`. `/mc` result: `profile_units`.
- `services/validation_service.py`: `_check_profiled_occurrence_units`
  from the walk, called from `validate_for_run` unconditionally;
  `profile_and_outage_modelled` (asset source only),
  `static_p_max_pu_not_applied` (either source, no "q = 0" remedy);
  `_check_shadowed_profiles`, `_profile_is_informative` and
  `outage_shadows_profile` removed.
- Tests: `tests/test_adequacy_profiled_units.py` (17); the five 12a tests
  re-scoped to six (25 in the file); the endpoint test extended.
- Live: S21 re-scoped; **new S24**. Frontend: `CoptPayload.fleet` optional
  fields, `fidelity_note`, a chip (`copt-fidelity-note`) with the sentence
  as tooltip; `McResult.profile_units?`. Specs: FMEA §5.3 amended, MC spec
  v1.4, 12a plan superseded note, QA plan S21/S24.

### Deviations from v2.1, recorded

1. **`fidelity` stays the enum; the sentence is `fidelity_note`.** The
   plan said `fidelity` names the netted units. `fidelity` is
   `"analytic_convolution"` on the payload and on every per-mode row, and
   the frontend comparison table keys on it; turning it into prose would
   break that contract for a sentence. So the sentence is a sibling
   field and `fidelity` is untouched (pinned by the endpoint test).
2. **A11′'s numbers are for the two-farm fixture, not the reviewer's
   single-farm variants.** The review's 4.76 / 4.40 / 0.80 were measured
   on the shadowed farm ALONE. S24 builds 12a's two-farm fixture (both
   farms) and computes the mixture in-suite from the fixture's numbers:
   **2.78 h** mixed against **0.44 h** flat. Both are hand-checkable in
   eight lines (the table is gas1 alone).
3. **M2's bite.** The plan's implied broken variant — force every unit
   through the `(H, 1)` path with a profile of ones — does NOT change the
   bytes. That is the broadcast claim itself, confirmed by the attempt,
   so it is not a broken variant. M2 was bitten instead by accumulating
   in float64 (a genuine scalar-path change): bites.
4. **A12's fixture had to carry mass.** The first bite of the `− 1e-12`
   grid rule did not bite: at 1 or 40 MW the RBTS table has no state, so
   a wrong index reads an identical value. The fixture now includes
   `200 + 5e-13` (one 40 MW unit down, P ≈ 0.02): bites.
5. **Per-hour convolution of the remainder is rejected** (finding 4's
   99 s case), and the netted rows say they understate rather than
   pretend otherwise.

### Bites (each ★ against its named broken variant; restores verified by hash)

| ★ | broken variant | result |
|---|---|---|
| A3′ | net the mixed units at expected output | bites (3.97 → 1.28) |
| M1/A4′ | fold a static 0.9 into units without a column | bites (gas_static hash) |
| M1/A4′ | attach only VARYING series | bites (hydro_const hash = old) |
| A7 | attach only VARYING series (the 3× cliff) | bites (3.877 vs 11.770) |
| A12 | drop the `− 1e-12` grid rule | bites, once the fixture carried mass (see 4) |
| split | net the largest instead of the smallest | bites |
| A1 | ignore the profile in the MC | bites |
| A5′ | apply the profile without the state | bites |
| M2 | accumulate in float64 | bites (see 3) |
| A10 | leave the old series branch in | bites |
| A10 | emit for carrier-default too | bites |
| A10 | gate on the outage column | bites |
| A10 | drop the static arm | bites |
| A10 | test only for the column's presence | bites |
| route | drop `profile_units` | bites |
| S24 (live) | drop the series at attachment | bites — recorded below after the tree gate |

### Gates

- `tests/test_adequacy_profiled_units.py` 17 passed; `test_adequacy_occurrence.py`
  25; COPT / MC / ELCC / endpoint / benchmark anchors 77 (the anchors pin
  the vectorised switch at their tolerances).
- Frontend: 876 passed / 92 files; `tsc --noEmit` clean.
- Live, one port-verified server: S15 15/15, S16 6/6, S17 5/5 + the
  pre-existing S17.6 skip, S18 5/5, S19 6/6, S20 3/3, S21 2/2 (re-scoped),
  S22 2/2, S23 2/2, **S24 2/2**.
- Full backend tree: recorded below.

### Gates, completed after the code commit (`fb6548f`)

- **Full backend tree** (from `pypsa-gui/backend`, summary line refused
  if empty): **2760 passed, 43 failed, 19 skipped** in 31 min. The 43
  `FAILED` ids diffed against master `07b32c2`'s 43 (`base_fails_sorted`):
  branch minus master **empty**, master minus branch **empty**.
- **S24 bitten live.** With `_occurrence_profile` returning None (the
  series dropped at attachment) on a restarted, port-verified server:
  S24.1 FAIL — `LOLE=0.440000`, the flat two-state value, `profile_units=[]`,
  the note absent; S24.2 FAIL — the MC's `profile_units=[]`. Restore
  verified by hash (`4f326702b0c75bdd`), server restarted, S24 2/2 again.

---

## SHIPPED-CODE REVIEW (2026-09-02, on `149aab0..7e8b646`) — accept with fixes

The pass that has found real defects every time it has run here ran on
12c-pre as shipped. Verdict: **accept with fixes** — no blocker, two
moderate, seven minor. Every finding below was re-run against the code
before being recorded (`scratchpad/sc_*.py`); the arithmetic the record
claims — the mixture's exactness (5e-16 / 2e-14 vs an independent per-hour
table at k = 1, 2, 3), the k = 0 path equal to the old scalar map, the
attribution rows equal to brute force (≤ 2.3e-13), M2 byte-identity,
ELCC dominance 24/24 with storage, the A5′ statistics (two-sided z for
α = 0.01/8760 is 4.866), A13 not flaky (0.37–0.44 s ×3), the docs'
numbers — all checked out.

### Findings, verified, and what was done

1. **MODERATE — a ±inf hour in a `p_max_pu` series crashed `/copt` and
   `/mc/elcc_candidates`, reachable through the public timeseries PUT.**
   `np.nan_to_num(nan=0.0)` kept ±inf; `profile × cap` overflowed; the
   mixture's DOWN state gave `0 × inf = NaN`; `_grid_index` cast NaN to
   `int64` → `IndexError` out of the app; the nameplate was `inf`, which
   Starlette refuses to serialise. JSON accepts `Infinity`, and the PUT has
   no finiteness check, so a user can reach it. Pre-phase the same column
   on a must-take unit survived (residual −inf, LOLE 0.70) — a robustness
   regression, not a pre-existing crash. **Fixed:** attachment drops every
   non-finite hour to 0 (`np.where(np.isfinite(vals), vals, 0.0)`), and
   `unit_nameplate_mw` guards the product. ★ test
   `test_a_NON_FINITE_hour_in_the_series_is_availability_zero_and_serves`;
   bite: back to `nan_to_num` — bit.
2. **MODERATE — the "NaN hour is the reserve margin's rule" claim was false
   for the derate, so "the three agree" was false on NaN hours.** Rule 1 is
   the net-load *window*'s `fillna(0.0)`; the margin's *derate* takes a
   pandas mean over the window, which skips NaN. Measured: two-hour window,
   one NaN hour → derate 0.45, engines' expectation 0.225. **Done:** the
   docstring and the preflight message now say exactly that (the engines
   count 0, the margin's mean skips it). **Open item:** whether the derate
   should adopt `fillna(0)` is a margin change with its own tests (the 12b
   B10 fixture carries a NaN) and is adjudicated separately, not here.
3. **MINOR — the static warning was a false positive on a unit that ALSO
   carries a series**, and its text was false there: PyPSA reads the
   series, the margin's derate reads the series (`avail_static` only when
   there is no profile), so do the engines — the static value is
   superseded, not "not applied". **Fixed:** the static arm skips a unit
   with an informative series. ★ test
   `test_a_static_value_BESIDE_a_series_is_superseded_not_flagged`; bite:
   drop the guard — bit.
4. **MINOR — the disclosure fired for a typed q = 0 and said "outages are
   sampled".** The engines were right (the mixture collapses at q = 0); the
   sentence was not. **Fixed:** no disclosure at q ≤ 0. ★ test
   `test_a_typed_q_of_ZERO_gets_no_sampled_outages_disclosure`; bite: drop
   the `q > 0` test — bit.
5. **MINOR — the coupling- and margin-loop `_hash` no longer hashed
   "exactly what the MC reads"**: the sampler now reads `u.profile` too.
   No reachable wrong reuse was found (within one loop `p_max_pu` is
   invariant and a newly built profiled unit changes `capacity_mw`), but
   the docstring was stale. **Fixed:** both hashes include the profile
   bytes (`b""` for none). No ★ — the loop suites pin the hash's behaviour
   on unprofiled fleets and pass unchanged.
6. **MINOR — `split_fleet` ranks by `mean(a)` and ignores `q`**, though
   the Jensen error of netting scales with `q(1−q)·a²` (a q = 0.5 unit at
   20 MW mean is netted before a q = 0.001 unit at 21 MW). Only bites at
   k > 8, which Q1's answer says is practically empty. **Recorded as a
   design note**; the rank key would be `q(1−q)·mean(a²)` if it ever
   matters.
7. **MINOR — a hand-built `CoptUnit` with a NaN profile gives
   `IndexError` in the COPT and silent NaN in the MC.** Unreachable via
   `fleet_and_residual` after fix 1. Recorded; `_grid_index` left as is.
8. **MINOR — one vacuous assertion** (`delta_eue_mwh >= 0` on rows the
   code clamps at 0). **Fixed:** the test now checks the *unclamped*
   counterfactual (perfect availability lowers EUE) directly. Also noted:
   `attribute_criticality` on RTS-79 emits 65 `RuntimeWarning`s from
   `deconvolve` — 22 of 31 units fall back to the rebuild path; rows equal
   brute force; pre-existing and noisy, not this phase's.
9. **MINOR (pre-existing) — ELCC dominance holds in exact arithmetic
   only**: a cap of 100.049 MW rounds up by 3.6e-6 in float32, above
   `SHORTFALL_TOL`, and a contrived residual makes `not_bracketed`
   reachable — identically on the flat two-state path. The profile adds
   nothing new. Recorded.

Gates after the fixes: the three new ★ tests bitten (3/3, restores by
hash); targeted suites (profiled, occurrence, endpoint, ELCC, coupling and
margin loops) 155 passed; live S21, S24, S15 on a port-verified server;
adequacy suites and the full tree recorded in the commit message.
