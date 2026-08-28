# FMEA Phase 6 — Sequential Monte Carlo Adequacy Engine (in-house route B)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** The engine that can honestly answer the question the COPT structurally cannot:
**what is a battery worth in firm MW?** A chronological (sequential) Monte Carlo
simulation — persistent two-state outages, non-anticipative storage dispatch, hourly
shortfall counting — plus **ELCC** (effective load-carrying capability) by bisection at
constant LOLE. Fills the `sequential_mc` fidelity tier and the `confidence_interval` /
`n_samples` fields the contract has carried empty since Phase 1.

**Why neither existing engine suffices (recorded so the scope survives review):**
- The **COPT** is a distribution over available capacity *in one hour* — no memory. A
  battery is nothing *but* memory; forcing one in asserts it can deliver its power in
  every hour of an event, so a 4 h battery would "cover" a 12 h Dunkelflaute. Wrong in
  the dangerous direction (overstates reliability), like the `time_basis` bug.
- The **LP proxy** is storage-aware but has **perfect foresight**: it saves energy on
  Monday for Thursday's Dunkelflaute because it has seen Thursday. Real operators
  haven't. Also optimistic, by a different mechanism.

**Honesty constraints (v1 scope, carried into every label and warning):**
- **Single-area (copper plate), electrical-only** — same scope as the COPT; the two
  numbers must be comparable, and multi-area MC with transfer limits is PRAS's whole
  project, not a task in ours.
- **One weather realisation** — the modelled horizon's own profiles. Dunkelflaute
  *frequency* therefore comes from a sample of one; the tail is understated. Ships with
  a standing warning string (the `non_convexity_warning` pattern), not a footnote.
  Multiple climate years remain the recorded procurement follow-up.
- `engine="mc"`, `fidelity="sequential_mc"`, `time_basis` **derived** via the shared
  `horizon_years`/`resolve_time_basis` helpers. Never comparable to a statutory
  standard; the CI is part of the number, not decoration.
- **No per-mode € ranking from MC in v1** — the COPT keeps the class-A worksheet rows.
  MC contributes system LOLE/EUE (with CI) and per-asset ELCC.

**Architecture decision (the one that keeps the engines coherent):** the MC consumes
`fleet_and_residual(n)` **verbatim** — the same `CoptUnit` list (which already carries
`mttr_hours`), the same must-take-netted residual, the same weights. Membership,
electrical scope, slack exclusion and VRE netting are then *provably* identical across
engines; only storage extraction is new. Any divergence between MC and COPT on a
thermal-only system is therefore a bug in the sampling, which is exactly what test T2
below exploits.

**Validation policy — two gates, neither substitutes for the other.** The internal
cross-check (T2) validates the *sampling machinery* against exact convolution. It is
structurally blind to shared-substrate errors: both engines consume the same fleet
extraction, the same FOR interpretation, the same residual netting — get any of that
wrong and both engines are wrong identically while the cross-check passes. Only an
**external published benchmark** validates the whole chain (data interpretation →
fleet construction → metric definition → number). Task 7 is therefore a required
shipping gate, not best-effort: no MC number reaches the UI until the benchmark suite
passes.

## Global Constraints

Phase 0–5 constraints apply (branch `claude/solution-fmea-integration-0mx5lc`, explicit
staging, test-first with demonstrated red, the pip-vs-pixi env caveat). Additional here:
- **Every stochastic assertion is seeded** and sized so the test is stable; "flaky
  because random" is not an acceptable failure mode.
- **Regression tests checked to bite**: each key property test must fail against a
  deliberately broken variant (documented in the test) before it counts.
- **float64 accumulators** for EUE even if state arrays are float32.
- RNG: `numpy.random.Generator` with `spawn()` per chunk — reproducible and
  parallel-safe; a bare global seed is not acceptable.

### Task 1: the sampler core (`services/adequacy/mc.py`)

Markov outage sampling with **persistence** — the classic error this task exists to
avoid is independent per-hour Bernoulli draws, under which outages vanish after an hour
and reliability is flattered badly.

- [ ] **Failing tests first** (`tests/test_adequacy_mc.py`):
  - Transition math exact: from `q` and `MTTR`, `MTTF = MTTR·(1−q)/q`; per-hour
    `p_fail = 1/MTTF`, `p_repair = 1/MTTR`, both clamped to (0, 1]; a `q=0` unit never
    fails; degenerate MTTR falls back through the occurrence chain, never crashes.
  - **T6 stationary start:** initial state drawn from the stationary distribution
    (up w.p. `1−q`) — mean availability over draws ≈ `1−q` at hour 0 (CI), i.e. no
    burn-in transient.
  - **T3 persistence (checked to bite):** mean sampled outage run length ≈ MTTR within
    CI; the test must FAIL when the sampler is swapped for iid Bernoulli at the same
    stationary `q` (run length would collapse toward `1/(1−q)`).
- [ ] Implement: availability sampling as `(draws_chunk × hours × units)` state
  evolution — hour loop with vectorised `(draws_chunk × units)` transitions (the hour
  loop is unavoidable anyway: storage needs it), chunked over draws
  (`DRAWS_CHUNK ≈ 256`; memory note in code: 256 × 8760 × f32 ≈ 9 MB per array).
- [ ] Commit: `feat(gui): MC outage sampler — persistent two-state Markov, stationary start`.

### Task 2: non-anticipative storage dispatch

- [ ] **Failing tests first:**
  - **T1 determinism:** with `q=0` everywhere the simulation is deterministic — EUE and
    shed hours equal a hand-computed residual-arithmetic case *exactly* (no
    `approx` beyond float tolerance), including efficiency losses both directions.
  - A 4 h / 100 MW battery against a constructed 8 h × 100 MW deficit: exactly
    4 h·100 MW·η_dispatch covered, EUE = the rest, to the MWh.
  - **T4 limits:** effectively infinite-duration storage ≡ `+p_nom` firm capacity
    (equal EUE to the same fleet with a perfect unit added); zero-energy storage ≡
    absent (identical draws → identical results).
  - Policy pin: dispatch order is part of the contract — test it explicitly.
- [ ] Implement: per-hour, per-draw greedy policy, **no lookahead**: surplus → charge
  (respect `p_nom`, `efficiency_store`, energy cap `max_hours·p_nom`); deficit →
  discharge (respect `p_nom`, `efficiency_dispatch`, SoC); residual deficit = unserved
  that hour. **Pinned policy choices, documented as policy not optimum:** discharge
  order descending remaining energy, charge order ascending; initial SoC = 100% with a
  code comment bounding the optimism (≤ one discharge cycle over the horizon).
  Storage membership: electrical buses, non-slack `StorageUnit`s; `Store`s excluded in
  v1 (no dispatch power rating) with a loud code comment.
- [ ] Commit: `feat(gui): non-anticipative greedy storage dispatch in the MC engine`.

### Task 3: metrics, convergence, and the decisive cross-engine test

- [ ] **Failing tests first:**
  - **T2 cross-engine (the anchor, fully self-contained):** thermal-only stochastic
    fleet → MC LOLE within the 99% CI of the **COPT's exact convolution** value, seeded,
    draws sized for stability. Checked to bite via the T3 iid swap (persistence changes
    LOLE on a multi-hour-event fixture) — this makes exact arithmetic the ground truth
    for the whole sampling machinery without any external data.
  - **Storage-only-helps invariant:** MC(with storage) LOLE ≤ MC(without) on identical
    draws (CRN), and equal when the battery has zero energy.
  - CI honesty: with every draw shortfall-free, LOLE = 0 is reported with a
    "resolution floor" note (`< 1/(draws·nyears)` h), never as a bare confident zero.
  - `time_basis`/`horizon_years` from the shared helpers (both weightings of the same
    week labelled differently — the Phase 5½ regression, re-asserted here).
- [ ] Implement: per-draw LOLE/EUE; mean + 95% CI across draws; per-period split via the
  shared weights; batch-until-converged loop (target CoV on LOLE, default 5%) under
  `MAX_DRAWS = 2000` and a wall-clock soft cap; `warning` string for the single-weather-
  realisation caveat, always present in v1.
- [ ] Commit: `feat(gui): MC adequacy metrics with CI, converged against the COPT`.

### Task 4: ELCC (`services/adequacy/elcc.py`)

Definition (last-in credit of a named existing asset): remove the asset, then bisect the
firm-MW block `Δ` (perfect, `q=0`) that restores the *system's* baseline LOLE. Report
`elcc_mw`, `elcc_share = elcc/nameplate`, and the baseline LOLE ± CI it was measured at.

- [ ] **Failing tests first:**
  - **CRN is load-bearing:** the bisection evaluates every candidate `Δ` on the *same*
    outage draws (same spawned seeds) — test that a perfect (`q=0`) unit's ELCC equals
    its capacity to within the bisection tolerance, which only holds under common
    random numbers; checked to bite by re-seeding per evaluation (result becomes noise).
  - Declining credit: the second identical wind tranche's ELCC < the first's.
  - A 4 h battery on a long-event fixture: ELCC strictly < `p_nom` (duration bites) and
    > 0; the same battery on a short-event fixture: ELCC ≈ `p_nom`.
  - **Honest refusals:** baseline LOLE ≈ 0 → status `"unidentifiable"` with reason
    (nothing to restore — ELCC undefined on a system that never sheds), no number;
    unknown asset → 404 semantics at the route.
  - Non-additivity is documentation + UI copy, not a computation — but assert the
    portfolio ≠ sum on one two-asset fixture so the claim in the copy is itself tested.
- [ ] Implement: `elcc_for_asset(n, kind, name, *, seed, draws, tol_mw)` for
  generator / storage-unit / must-take-VRE (removal semantics per kind: unit out of the
  fleet, storage out of dispatch, profile out of the netting); bisection bracket
  `[0, nameplate]` with the standard ELCC-may-exceed-nameplate note explicitly rejected
  in v1 (cap and say so); `MAX_ELCC_ASSETS = 10` per request (each is a bisection ×
  draws).
- [ ] Commit: `feat(gui): ELCC by constant-LOLE bisection under common random numbers`.

### Task 5: endpoints + contract

- [ ] **Failing tests first** (handler + the authenticated HTTP layer, per the Phase 4
  lesson — the missing-import bug lived exactly where handler tests don't look):
  `GET /results/mc` 204 before any run; `POST /results/mc` 422 with no occurrence-
  bearing units and VoLL-independent (MC needs no VoLL); 409 against a running solve /
  sweep / frontier / mc (and registered in *their* guards in return); worker thread with
  the `_state` pattern; `elcc_assets: [...]` in the request body, results inline in the
  payload; golden-coverage `ROUTE_SURFACES` entries (the Phase 1–4 regression).
- [ ] Contract: `Engine` literal += `"mc"` (models + frontend types + badge tooltip
  copy); response mirrors the COPT endpoint shape `{engine, fidelity, metrics{...,
  confidence_interval, n_samples, time_basis, horizon_years}, elcc: [...], warning}`.
- [ ] Commit: `feat(gui): /results/mc — sequential MC adequacy + ELCC on demand`.

### Task 6: the panel

- [ ] **Vitest first:** an `McPanel` (FrontierPanel pattern: run button, poll, 409-aware)
  renders LOLE ± CI and EUE ± CI with `basisSuffix`, `n_samples`, the single-weather
  warning; an ELCC table (asset, nameplate MW, ELCC MW, %, status) where **storage rows
  carry numbers while the COPT chips stay storage-silent** — that contrast is the
  feature; `"unidentifiable"` rows render the reason, not a dash; CSV export;
  non-additivity note in the copy ("credits do not sum — portfolio ELCC ≠ Σ asset
  ELCC"). Literal hex chart/badge colours (the Phase 5 SVG lesson).
- [ ] Mount on the Lost load tab beside the COPT chips and FrontierPanel; `tsc -b`.
- [ ] Commit: `feat(gui): MC adequacy panel — CI-bearing metrics and per-asset ELCC`.

### Task 7: the benchmark gate (required — the shipping criterion)

Published test systems with published LOLE results, reproduced within a *tight* CI.
The non-vacuity lesson from S15.7 applies directly: a wide CI makes any benchmark
"pass", so the acceptance criterion caps the CI width as well as requiring coverage.

- [ ] **Data sourcing with provenance (its own step, not an afterthought):** fetch the
  IEEE RTS-79 generator set (32 units, FOR + MTTR) and its weekly/daily/hourly load
  model, and the RBTS (Billinton's 6-bus educational system — small enough that large
  draw counts are cheap), from published sources reachable in this environment
  (RTS-GMLC repository, PRAS test data, literature reproductions). Commit as versioned
  fixtures with a provenance header: source URL, retrieval date, and the figures
  **cross-checked against a second independent source** before pinning — a from-memory
  number is not a citation. Fixture units carry `basis="FOR"` explicitly, so the
  FOR-vs-EFORd interpretation is pinned where a benchmark would catch it.
- [ ] **Failing tests first** (`tests/test_adequacy_benchmarks.py`, `@pytest.mark.slow`):
  - **COPT vs RTS-79:** the *analytic* engine must reproduce the published hourly LOLE
    (≈9.4 h/yr, cited value per the sourcing step) first. If the COPT misses it, the
    shared substrate is wrong and the MC inherits the miss — cheapest place to find out.
  - **MC vs RTS-79 and MC vs RBTS:** published LOLE inside the MC 95% CI **and** CI
    half-width ≤ 5% of the published value (the anti-vacuity cap; sized draws, seeded).
  - After first green: pin the seeded result exactly as a regression anchor, so future
    refactors diff against both the published value and the pinned draw.
- [ ] **Storage, stated honestly:** no canonical published storage-ELCC test number
  exists — RTS/RBTS are thermal-hydro systems and published ELCC studies report
  method-dependent ranges, not reproducible fixtures. The storage gate therefore stays
  the analytic ladder (T1/T4: exact deterministic cases, duration limits) **plus** a
  recorded stretch goal: cross-tool comparison against PRAS on one identical small
  system (needs a Julia runtime — documented manual step, not CI). This limitation goes
  in the PR body and the panel tooltip, not a code comment.
- [ ] Commit: `test(gui): benchmark gate — RTS-79/RBTS reproduced within capped CI`.

### Task 8: end-to-end + QA round

- [ ] Extend `qa_e2e.py` **S15**: MC steps over HTTP — run on the S15 network (which has
  a battery from the storage-unit case), assert CI fields present, 409 concurrency,
  `ELCC(battery)` non-null and < `p_nom`, and the invariant **MC LOLE ≤ COPT LOLE** on
  the same network (storage can only help; COPT can't see it). Update `QA_E2E_PLAN.md`.
- [ ] Live QA round (the discipline that found all nine defects so far): boot backend,
  drive the 3-zone system with battery, render the panel in Chromium; record runtimes
  (target: 500 draws × 168 h in seconds, 1000 × 8760 in low tens of seconds; if missed,
  that's a finding, not a footnote); budget-guard trip test.
- [ ] Commit: `test(gui): S15 MC steps` and the QA-round fixes it forces.

## Open decisions (defaults chosen; flag to the user, don't block on them)

1. Default draws **500** / CoV target **5%** / seed exposed in the request body.
2. Initial SoC **100%** (bounded optimism, documented) — alternative 50% if challenged.
3. ELCC list: **explicit asset list only** in v1, no auto-include of all storage.
4. `Store` components excluded from dispatch v1 (no power rating); revisit with H2.

## Done criteria

A seeded, converged MC LOLE/EUE with CI that (a) matches the COPT's exact convolution on
thermal-only systems, (b) is provably persistence-aware and non-anticipative under
tests that bite, (c) **reproduces the published RTS-79 and RBTS LOLE within a
CI whose width is itself capped** — the external gate no internal cross-check can
replace, (d) prices a battery's ELCC where the COPT stays honestly silent, and
(e) ships every number with engine/fidelity/CI/time-basis labels and the
single-weather-realisation warning. Estimated ~400–600 lines engine, ~150 ELCC, ~150
routes, ~250 panel, ~800 tests.
