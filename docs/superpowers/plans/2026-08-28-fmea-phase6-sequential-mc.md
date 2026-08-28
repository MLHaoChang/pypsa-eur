# FMEA Phase 6 — Sequential Monte Carlo Adequacy Engine (in-house route B), v2

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** The engine that can honestly answer the question the COPT structurally cannot:
**what is a battery worth in firm MW?** A chronological (sequential) Monte Carlo
simulation — persistent two-state outages, non-anticipative storage dispatch, hourly
shortfall counting — plus **ELCC** (effective load-carrying capability) by bisection at
constant LOLE. Fills the `sequential_mc` fidelity tier and the CI/`n_samples` fields the
contract has carried empty since Phase 1.

**Why neither existing engine suffices (recorded so the scope survives review):**
- The **COPT** is a distribution over available capacity *in one hour* — no memory. A
  battery is nothing *but* memory; forcing one in asserts it can deliver its power in
  every hour of an event, so a 4 h battery would "cover" a 12 h Dunkelflaute. Wrong in
  the dangerous direction (overstates reliability), like the `time_basis` bug.
- The **LP proxy** is storage-aware but has **perfect foresight**: it saves energy on
  Monday for Thursday's Dunkelflaute because it has seen Thursday. Real operators
  haven't. Also optimistic, by a different mechanism.

## Review round (recorded, v1 → v2)

An adversarial review of the v1 plan overturned or corrected the following; they are
recorded here rather than silently fixed, per the spec-§3 discipline:

1. **v1's T2 bite-check claim was mathematically false.** For a thermal-only fleet,
   LOLE = Σ_t w_t·P(C_t < L_t) depends only on the *marginal* per-hour availability
   distribution. An iid Bernoulli sampler at the same stationary `q` has an identical
   marginal → **identical expected LOLE/EUE**, however multi-hour the events. Temporal
   persistence changes the across-draw *variance* (CI width), never the thermal-only
   mean. Persistence becomes observable in a *metric* only through **storage**:
   persistent outages drain a battery; iid ones let it recover. The test ladder below
   is restructured around this fact — which is also the cleanest statement of why this
   engine exists at all: *persistence is precisely the thing that makes batteries worth
   less than firm capacity.*
2. **v1's benchmark sourcing was wrong.** RTS-GMLC *replaced* the 1979 load model with
   real zonal traces and updated the fleet; it cannot reproduce the published ≈9.4 h/yr.
   The load artifact is the 1979 paper's percentage tables (see Task 7).
3. The (0,1] transition-probability clamp silently broke `q = MTTR/(MTTR+MTTF)`
   (stationarity) for sub-hour MTTR/MTTF; replaced by an explicit floor + rejection.
4. `MetricsBlock.confidence_interval` is one ambiguous tuple; Task 6 renders two CIs.
   Contract work moved into Task 5 as a decision, not an accident.
5. Initial SoC=100% is **not** "bounded optimism" on 168 h fixtures — one free cycle
   can be 100% of a small battery's contribution there. Tests restructured (Task 2).
6. Diagnostic-only v1, the frontier/MC target collision, the three-engine UX, and the
   single-hour-outage fine print were all undocumented decisions. Now explicit below.

A second, end-to-end trace audit (UX → API → sampler → dispatch → metrics → ELCC →
benchmarks) added findings 7–13, folded into the tasks below and marked **[e2e]**:
the CRN stream-keying trap, per-period re-initialisation, the storage capacity basis,
DSR's absence from the MC, weights-vs-dynamics, comparison-table metric alignment,
and the no-mutation locking model.

**Honesty constraints (v1 scope, carried into every label and warning):**
- **Single-area (copper plate), electrical-only** — same scope as the COPT.
- **One weather realisation** (the modelled horizon's profiles) **and independent unit
  outages** (no common-mode / cold-snap-correlated derating — exactly the class-C
  motivation, which independent two-state chains cannot produce). Both ship in ONE
  standing warning string (the `non_convexity_warning` pattern), always present in v1.
- `engine="mc"`, `fidelity="sequential_mc"`, `time_basis` derived via the shared
  `horizon_years`/`resolve_time_basis` helpers. CI is part of the number.
- **No per-mode € ranking from MC in v1** — the COPT keeps the class-A worksheet rows.

## The optimization layer — decision record (this section is normative)

Reliability enters the LP in exactly one place today: `_wrap_with_ens_cap`
(solver_service.py:2890), the ε-constraint on involuntary VOLL slacks, which the
Phase-5 frontier sweeps. **In v1 the MC never feeds back into what the LP builds** — it
evaluates the solved plan (`p_nom_opt` via `fleet_and_residual`), full stop. Rationale:
every coupling mechanism is either an outer iteration whose cost and failure modes
deserve their own phase, or wrong for this tool. Recorded verdicts:

- **(i) Outer loop — solve LP → run MC → retune `ens_cap_permyriad` → re-solve until
  MC-LOLE meets the user's target: the Phase 7 candidate, not now.** Known failure
  modes to carry into that phase's plan: the map cap→MC-LOLE is piecewise-constant
  (plans change only at LP breakpoints) and noisy, so plain bisection stalls or
  oscillates unless CRN is held across outer iterations and the stopping rule accepts a
  band; and the target may be **unreachable by tightening the proxy cap at all** when
  the LP meets tighter caps with foresight-dependent storage the MC discounts.
- **(ii) ELCC-weighted planning-reserve-margin constraint (ReEDS-style): later phase at
  most.** ELCC depends on the mix (a fixed point on a non-convex quantity), and v1
  computes ELCC only for named *existing* assets — marginal candidate-tech ELCC is a
  different computation.
- **(iii) Availability-derating `p_nom` in the LP: never as a default.** It perturbs
  energy, dispatch and cost everywhere, and answers no question the ε-constraint
  doesn't answer better.
- **(iv) Benders-style reliability cuts from MC shortfall samples: never for this
  tool.** The recourse (a greedy non-anticipative simulation) is not an LP; the "cuts"
  have no valid duals. Recorded so the omission reads as a choice, not an oversight.
- **Frontier reconciliation (v1 obligation):** a frontier point "meeting 1‱" is a claim
  in LP-proxy currency that the MC may contradict. The McPanel states which solved plan
  it evaluated and surfaces MC-LOLE-vs-the-cap divergence the way CoptChips surfaces
  screening≫proxy (Task 6). `TargetBlock.basis` is the seam a Phase-7 MC-LOLE target
  would extend — reserve with a comment (Task 5), nothing more.

**Architecture (unchanged, review-confirmed):** the MC consumes `fleet_and_residual(n)`
verbatim — same `CoptUnit` list (carries `mttr_hours`), same must-take-netted residual,
same weights — so membership/scope/slack/VRE-netting are provably identical across
engines; only storage extraction is new.

**Validation policy — two gates, neither substitutes for the other.** The internal
cross-check (T2) validates *marginal occupancy* against exact convolution — and is, by
finding 1 above, **persistence-blind and shared-substrate-blind**: both engines consume
the same fleet extraction and FOR interpretation, so an error there passes T2 while
both engines are wrong identically. Persistence is pinned by T3 (run lengths) and T2b
(storage metric). The whole chain — data interpretation → fleet → metric definition →
number — is validated only by the **external published benchmark**: Task 7 is a
required shipping gate; no MC number reaches the UI until it passes.

## Global Constraints

Phase 0–5 constraints apply (branch, explicit staging, test-first with demonstrated
red, the pip-vs-pixi caveat). Additional here: every stochastic assertion is seeded and
sized for stability; regression tests checked to bite against a documented broken
variant; float64 accumulators for EUE; `numpy.random.Generator` with `spawn()` per
chunk.

### Task 1: the sampler core (`services/adequacy/mc.py`)

- [ ] **Failing tests first** (`tests/test_adequacy_mc.py`):
  - Transition math exact: `MTTF = MTTR·(1−q)/q`; per-hour `p_fail = 1/MTTF`,
    `p_repair = 1/MTTR`. **No silent clamp:** MTTR is floored at 1 h (one timestep)
    with a logged warning; a unit whose implied MTTF < 1 h is rejected as inconsistent
    (the occurrence validator already flags such pairs). Test at MTTR = 1 h that
    stationary availability still equals `1−q` exactly.
  - **T6 stationary start:** initial state from the stationary distribution (up w.p.
    `1−q`) — mean availability over draws ≈ `1−q` at hour 0 (CI); no burn-in.
  - **T3 persistence (two assertions, checked to bite):** mean sampled run length ≈
    MTTR within CI **and** observed `P(run = 1) ≈ 1/MTTR` within CI — the second pins
    the geometric *family*, so a fixed-duration sampler fails too. Must FAIL under an
    iid Bernoulli swap at the same stationary `q`.
  - **Recorded non-goal (the "no single-hour outages" question):** sojourns are
    geometric (PRAS-standard). `1/MTTR` of outages last a single hour — persistence
    means the **mean** run is MTTR, not a floor on run length. A minimum-duration /
    semi-Markov repair model would preserve stationary availability (renewal-reward)
    but break the memoryless stationary start T6 depends on (it requires equilibrium
    residual-life initialisation) — recorded as a non-goal with this reason.
- [ ] **[e2e] Locking model:** the MC never mutates the network — unlike the sweep it
  needs no undo machinery. Snapshot every input (units, residual, weights, storage
  frame) into plain arrays **under the PyPSAService lock once**, release, and compute
  lock-free. State this in the module docstring; it is the property that makes the MC
  safe to run beside an editing user.
- [ ] **[e2e] Weights scale accounting, not dynamics:** shortfall hours/energy are
  weighted in the sums (as the COPT does); MTTR and sojourns live in **modelled
  hours** — a 52×-weighted representative week is one week of chronology standing for
  52, not a stretched year. One sentence in the docstring, one test.
- [ ] Implement: hour loop over vectorised `(draws_chunk × units)` state transitions,
  **reduced on the fly into a `(draws_chunk × hours)` available-capacity array** —
  never materialise the `(draws × hours × units)` cube (×N_units memory: ~287 MB for
  RTS-79's 32 units at 256 draws). `DRAWS_CHUNK ≈ 256`.
- [ ] Commit: `feat(gui): MC outage sampler — persistent two-state Markov, stationary start`.

### Task 2: non-anticipative storage dispatch

- [ ] **Failing tests first:**
  - **T1 determinism:** `q=0` everywhere → EUE and shed hours equal hand-computed
    residual arithmetic *exactly*, including efficiency losses both directions.
  - **T1b charge path (finding 5):** start **empty**, surplus hours precede the
    deficit; assert stored energy = surplus·η_store, bounded by `p_nom` and the energy
    cap — so the charging path is validated independently of any free initial cycle.
  - 4 h/100 MW battery vs a constructed 8 h×100 MW deficit: exactly 4·100·η_dispatch
    covered, EUE = the rest, to the MWh.
  - **T4 limits:** ∞-duration storage ≡ `+p_nom` firm; zero-energy ≡ absent
    (identical draws → identical results). Dispatch-order policy pinned by test.
- [ ] Implement: per-hour, per-draw greedy, **no lookahead**: surplus → charge
  (`p_nom`, `efficiency_store`, cap `max_hours·p_nom`); deficit → discharge (`p_nom`,
  `efficiency_dispatch`, SoC); residual deficit = unserved. Pinned policy (documented
  as policy, not optimum): discharge descending remaining energy, charge ascending.
  Initial SoC = 100% **on full-horizon runs** (optimism ≤ one cycle at 8760 h); short-
  horizon fixtures set SoC explicitly per test. **[e2e] Capacity basis mirrors
  `CoptUnit`:** `p_nom_opt` when a solve exists else `p_nom` (an extendable battery the
  LP built must be simulated at its built size — stated for generators via
  `fleet_and_residual`, and the new storage extraction must say it too).
  **[e2e] Per-period re-initialisation:** on a MultiIndex horizon, hour N of period P
  is NOT followed by hour 0 of period P+1 — SoC **and** outage states re-initialise at
  every investment-period boundary (stationary start per block); a battery must not
  carry charge across a ten-year gap. Test: two-period fixture where carrying SoC
  across the boundary would change EUE. Storage membership: electrical buses,
  `slack.py`'s carrier/name tests applied to the **storage frame** (no storage slack
  mask exists today — DSR slacks are Generators, already excluded via
  `fleet_and_residual`); `Store`s excluded v1 (no power rating), loud comment.
- [ ] Commit: `feat(gui): non-anticipative greedy storage dispatch in the MC engine`.

### Task 3: metrics, convergence, and the cross-checks

- [ ] **Failing tests first:**
  - **T2 cross-engine (marginal-occupancy anchor):** thermal-only stochastic fleet →
    MC LOLE within the 99% CI of the COPT's exact convolution, seeded. **Stated in the
    test docstring: T2 is persistence-blind** (identical under an iid sampler — that is
    a theorem, not a gap). Checked to bite via a *broken-transition* variant (e.g.
    `p_fail` computed from `q` directly, or an off-by-one stationary start).
  - **T2b persistence-in-a-metric (the test v1 wrongly claimed T2 was):** a
    Dunkelflaute fixture with a battery — EUE under the persistent sampler differs
    from the iid sampler at the same stationary `q` (persistent outages drain the
    battery; iid ones let it recover). This is the metric-level bite for persistence.
  - **Storage-only-helps invariant (CRN):** MC(with storage) LOLE ≤ MC(without) on
    identical draws; equal at zero energy.
  - CI honesty: all-draws-shortfall-free reports LOLE 0 **with a resolution floor**
    (`< 1/(draws·nyears)` h), never a bare confident zero.
  - `time_basis`/`horizon_years` via the shared helpers (both weightings of the same
    week labelled differently — re-asserted).
- [ ] Implement: per-draw LOLE/EUE; mean + 95% CI across draws **per metric** (LOLE and
  EUE each get an interval); per-period split via shared weights; batch-until-converged
  (CoV target default 5%) under `MAX_DRAWS = 2000` (product cap — the benchmark budget
  is separate, Task 7) and a wall-clock soft cap; the standing warning string — now
  three clauses: single weather realisation, independent outages (no common-mode),
  **[e2e] and DSR excluded as a resource** (DSR slacks are rightly excluded as slacks,
  but in the LP they SERVE demand — an MC that ignores them attributes part of the
  MC-vs-proxy divergence to foresight when it is actually a missing resource; the
  warning stops that misread).
- [ ] Commit: `feat(gui): MC adequacy metrics with per-metric CI, converged vs the COPT`.

### Task 4: ELCC (`services/adequacy/elcc.py`)

Definition (last-in credit of a named existing asset): remove the asset, then find the
firm-MW block restoring baseline LOLE. **Predicate (finding: exact equality is
ill-posed on finite draws — LOLE(Δ) is a monotone step function under CRN): the
smallest Δ with LOLE(Δ) ≤ baseline, bisection tolerance in MW.**

- [ ] **[e2e] CRN stream structure is a design constraint, not an option:** if a
  `(draws × units)` matrix is sampled jointly, changing the unit count shifts every
  other unit's draws and CRN silently dies exactly where it is load-bearing. Therefore:
  **always sample the FULL fleet's availability; "removal" is exclusion of that unit's
  column from the capacity aggregation** — draws identical across all ELCC evaluations
  by construction. Test: removing unit i leaves every other unit's sampled path
  bit-identical.
- [ ] **Failing tests first:**
  - **CRN is load-bearing:** every candidate Δ evaluated on the *same* spawned draws;
    a perfect (`q=0`) unit's ELCC equals its capacity within tolerance — bites when
    re-seeded per evaluation (result becomes noise). CRN remains unbiased and
    variance-reducing when the removed asset is storage (dispatch path changes, draws
    don't; shortfall outcomes stay positively correlated across Δ).
  - Declining credit: second identical wind tranche's ELCC < the first's.
  - 4 h battery, long-event fixture: `0 < ELCC < p_nom`; short-event fixture:
    ELCC ≈ `p_nom`, run at **initial SoC 50%** (or assert 100%/50% insensitivity), so
    the result is about the asset, not the free initial cycle.
  - **Honest refusals:** baseline LOLE ≈ 0 → status `"unidentifiable"` + reason, no
    number; unknown asset → 404 at the route.
  - Non-additivity asserted on one two-asset fixture (portfolio ≠ Σ), so the UI copy's
    claim is itself tested.
- [ ] Implement: `elcc_for_asset(n, kind, name, *, seed, draws, tol_mw)` for generator /
  storage-unit / must-take-VRE (removal semantics per kind); bracket `[0, nameplate]`,
  exceedance explicitly rejected v1 (cap and say so); `MAX_ELCC_ASSETS = 10`.
- [ ] Commit: `feat(gui): ELCC by constant-LOLE bisection under common random numbers`.

### Task 5: endpoints + contract (the contract work is a decision, not an accident)

- [ ] **Failing tests first** (handler + authenticated HTTP layer): `GET /results/mc`
  204 before any run; POST 422 with no occurrence-bearing units (VoLL-independent);
  mutual 409s with solve/sweep/frontier (registered in *their* guards in return);
  worker `_state` pattern; `elcc_assets: [...]` in the body; golden-coverage entries.
- [ ] **Contract decisions:**
  - `Engine` += `"mc"` (models + frontend types + badge copy).
  - `MetricsBlock`: add `lole_ci: tuple[float,float] | None` and
    `eue_ci: tuple[float,float] | None`; the legacy ambiguous `confidence_interval`
    documented as deprecated-alias-of-`lole_ci` (kept for wire compatibility).
  - `AdequacyReport` gains optional `elcc: list[...] | None` and `warning: str | None`
    blocks — OR the "one shape every engine fills" docstring is amended to name the
    sibling-payload reality; pick one, in the commit, with the reason.
  - One comment on `TargetBlock.basis` reserving the Phase-7 `"mc_lole"` extension.
- [ ] Commit: `feat(gui): /results/mc — sequential MC adequacy + ELCC on demand`.

### Task 6: the panel and the three-engine surface

- [ ] **Vitest first:**
  - `McPanel` (FrontierPanel pattern): run/poll/409-aware — and each study's button,
    when blocked, **names why** ("frontier study running"), never a raw 409 toast.
  - **CI rendering decided:** intervals render as ranges (`9.1–9.8 h`, `n=500` beside),
    never `±` (the interval may be asymmetric; ± near zero renders a negative bound);
    the resolution floor renders as the displayed value (`< 0.02 h`) when all draws
    are shortfall-free.
  - **The cross-engine comparison table (the three-engine answer):** one row per
    engine — lp_proxy / copt / mc — columns: metric, value (+CI where it exists),
    fidelity, storage-aware?, DSR-aware?, foresight, time-basis. **[e2e] Metric
    alignment stated in the header tooltips:** LP-proxy ENS ↔ COPT/MC EUE are the same
    quantity (unserved MWh) under three engines; LP shed-hours is the deterministic
    analogue of LOLE — the rows align on those two shared metrics, they are not
    apples-to-oranges. The storage-silent-COPT vs
    storage-priced-MC contrast is a **structural dash-vs-number in this table**, not
    prose. ELCC table below it (asset, nameplate, ELCC MW, %, status), with
    `"unidentifiable"` rows rendering the reason; non-additivity note in the copy.
  - Frontier reconciliation chip: when a target/cap exists, MC-LOLE-vs-cap divergence
    surfaces like the screening≫proxy chip.
- [ ] **Mounted in BOTH LostLoadTab branches** — the zero-lost-load early return is
  precisely where a reliable system lands and where the MC's CI-bearing zero and the
  ELCC refusals are the whole story (the Phase-QA chips lesson, applied in advance).
- [ ] **IA decision, recorded in the component header (not decided silently):** v1
  stays on the Lost load tab — the engines belong beside the lost-load evidence, and
  mid-phase Results.tsx re-wiring (5 coupled edits) buys no analysis. **Revisit
  condition recorded verbatim:** when the Phase-7 coupling loop or a fourth study
  lands, this tab has tipped and the adequacy surfaces split into a dedicated
  Results→Adequacy tab. (Flagged to the user as overridable now.)
- [ ] `tsc -b`; commit: `feat(gui): MC panel + cross-engine adequacy comparison table`.

### Task 7: the benchmark gate (required — the shipping criterion)

Published test systems reproduced within a *tight* CI. The S15.7 anti-vacuity lesson:
acceptance caps the CI width as well as requiring coverage.

- [ ] **Data sourcing with provenance (its own step):** the RTS-79 artifact is the
  **1979 paper's tables** (IEEE Trans. PAS-98, "IEEE Reliability Test System";
  reproduced in Billinton & Allan's appendix and countless theses/course notes — the
  named fallback if the paper is unreachable through the proxy): 32 units (FOR+MTTR)
  and the weekly/daily/hourly percentage load model (52×7×seasonal-hourly → 8736 h).
  **RTS-GMLC and PRAS data are usable ONLY to cross-check generator FOR/MTTR — never
  the load model** (RTS-GMLC replaced it with real traces; it cannot reproduce the
  published number). Commit the reconstruction script with the week-alignment
  convention pinned (Monday-start, week 1 = winter — reproductions are sensitive to
  this at the level the CI cap cares about), fixtures with provenance headers, figures
  cross-checked against a second independent source, `basis="FOR"` pinned. RBTS
  (Billinton's 6-bus system) as the cheap-draws second benchmark.
- [ ] **Failing tests first** (`tests/test_adequacy_benchmarks.py`, `@pytest.mark.slow`):
  - **COPT vs RTS-79 first** — if the analytic engine misses the published hourly LOLE,
    the shared substrate is wrong and the MC inherits it; cheapest place to find out.
  - **MC vs RTS-79 and MC vs RBTS:** published LOLE inside the MC 95% CI **and** CI
    half-width ≤ 5% of the published value. **The benchmark draw budget is a harness
    parameter independent of MAX_DRAWS** (start at 5000: rough sizing at RTS-79 — 2–3
    events/yr of geometric mean ~4–5 h gives per-draw CoV ≈ 0.85–1.1, so the 5% cap
    needs ≥1,200–1,900 draws and the product cap of 2000 is marginal). The run prints
    the empirical variance so "not enough draws" is a measured outcome, not a red CI.
  - After first green: pin the seeded result exactly as a regression anchor.
- [ ] **Storage, stated honestly:** no canonical published storage-ELCC fixture exists
  (RTS/RBTS are thermal-hydro; published ELCC studies give method-dependent ranges).
  The storage gate stays the analytic ladder (T1/T1b/T4) plus a recorded stretch goal:
  PRAS cross-tool comparison on one identical small system (Julia runtime — documented
  manual step, not CI). Disclosed in the PR body and the panel tooltip.
- [ ] Commit: `test(gui): benchmark gate — RTS-79/RBTS reproduced within capped CI`.

### Task 8: end-to-end + QA round

- [ ] Extend `qa_e2e.py` **S15**: MC over HTTP on the battery-bearing S15 network —
  CI fields present, 409 concurrency, `ELCC(battery)` non-null and < `p_nom`, and the
  storage-helps check **CI-aware** (assert COPT LOLE ≥ MC lower CI bound − ε, not a
  raw point comparison — MC and COPT are not CRN-coupled, so a point assertion is a
  latent flake in exactly the class the global constraints forbid). Update
  `QA_E2E_PLAN.md`.
- [ ] Live QA round (the discipline that found every defect so far): boot backend,
  drive the 3-zone battery system, render the panel and the comparison table in
  Chromium; record runtimes (target: 500×168 h in seconds, 1000×8760 in low tens of
  seconds; a miss is a finding); budget-guard trip test.
- [ ] Commit: `test(gui): S15 MC steps` and the QA-round fixes it forces.

## Open decisions (defaults chosen; flag to the user, don't block)

1. Default draws **500** / CoV target **5%** / seed in the request body.
2. Initial SoC **100% on full horizons**; short fixtures set it explicitly (review
   finding 5 folded in — the old blanket claim is retracted).
3. ELCC: **explicit asset list only**, no auto-include.
4. `Store`s excluded from dispatch v1; revisit with H2.
5. **IA:** v1 on the Lost load tab with the recorded split condition — a dedicated
   Adequacy tab now is a defensible override, say the word.

## Done criteria

A seeded, converged MC LOLE/EUE with per-metric CI that (a) matches the COPT's exact
convolution on thermal-only marginals, (b) is persistence-aware where persistence is
observable — run lengths (T3) and storage metrics (T2b) — under tests that bite,
(c) **reproduces published RTS-79 and RBTS LOLE within a CI whose width is itself
capped**, (d) prices a battery's ELCC where the COPT stays honestly silent, and
(e) ships every number with engine/fidelity/CI/time-basis labels, the standing
weather+independence warning, and the recorded diagnostic-only optimization stance.
Estimated ~400–600 lines engine, ~150 ELCC, ~150 routes, ~350 panel+table, ~1000 tests.
