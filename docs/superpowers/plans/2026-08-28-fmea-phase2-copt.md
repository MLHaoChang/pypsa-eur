# FMEA Phase 2 — COPT Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** The analytic adequacy engine (spec v4 §§3.1, 3.3, 5.3): a Capacity Outage Probability Table over the dispatchable thermal fleet, hourly screening LOLP/LOLE/EUE against the exogenous residual load, and the **leave-one-out outage-attribution criticality** that produces the class-A FMECA ranking with **zero LP solves**. Surfaced side-by-side with the LP proxy — their divergence is the product (a large gap means storage/network carry the adequacy, which is exactly when the classical number misleads).

**Honesty constraints carried from the review (F1):** thermal-only, **storage-excluded, import/link-excluded, network-free** — a screening number, `fidelity="analytic_convolution"`, never comparable to a statutory standard. The residual load nets ONLY exogenous must-take generation at its given availability (profiles), never LP dispatch decisions. Sector scope: electrical, same classifier as everything else.

**Fleet membership rule (data-driven, documented in code):** an electrical, non-slack generator with resolvable occurrence params (`resolve_outage_params.source != "missing"`) is a **two-state COPT unit** at its firm capacity (`p_nom_opt` when solved else `p_nom`); one **without** occurrence data is **must-take** and is netted from load at its available output (`p_max_pu_t × capacity`, static fallback). VRE therefore nets via its hourly profiles (variance captured hour-by-hour; its mechanical FOR stays excluded, consistent with `occurrence.py`'s no-VRE-defaults decision). StorageUnits/Stores/Links never enter — storage-blind by design.

## Global Constraints

Phase 0/1 constraints apply (branch, staging, test-first with demonstrated red, pixi env note, the two environmental compare failures, the two PR #4 xfail gates). Hand-checkable arithmetic is the test standard here: two-unit systems with exact closed-form LOLP/EUE, not statistical assertions.

### Task 1: the COPT core (`services/adequacy/copt.py`)

- [x] **Failing tests first** (`tests/test_adequacy_copt.py`): exact two-unit case (60 MW q=0.1, 40 MW q=0.2, load 70 → LOLP 0.28, EUE 5.6 MWh/h; weighted over snapshots); rounding increment Δ apportions a 2.5 MW unit to adjacent states preserving the mean; must-take netting reduces residual load exactly by profile × capacity; empty fleet → LOLP 1 wherever load > 0; per-period split under a MultiIndex.
- [x] Implement: `build_copt(units, delta_mw)` — recursive convolution over `(capacity, q)` pairs, probabilistic apportioning to adjacent rounded states (`O(N·C/Δ)`, the spec's complexity note); `survival(copt)` (P[available ≥ x]); `hourly_adequacy(copt, residual_load, weights)` → LOLP_t, LOLE, EUE, per period; `fleet_and_residual(n)` applying the membership rule + electrical demand walk (reuse the ENS-cap walk's semantics).
- [x] Commit: `feat(gui): COPT core — convolution + hourly screening adequacy`.

### Task 2: outage-attribution criticality

Definition (spec §3.3, corrected semantics): unit *i*'s attributed risk is `ΔEUE_i = EUE(fleet as-is) − EUE(fleet with unit i perfectly available)` — deconvolve *i* out, convolve back a **deterministic** capacity of the same size. This measures the cost of the unit's *outages* over the full multi-outage state space (N-2 and beyond included), not the value of its capacity.

- [x] **Failing tests first:** deconvolution round-trips (conv → deconv returns the original distribution within 1e-9); a q=0 unit attributes exactly 0; attribution increases in q and in capacity; criticality € = ΔEUE × VoLL; `occurrence_per_year = 8760·rate/mttr` (cycle frequency, matching `occurrence.py`); rows are valid `FailureModeResult`s (engine `copt`, fidelity `analytic_convolution`, class `A`, `in_metric_scope=True`, severity = criticality/occurrence, all ≥ 0).
- [x] Implement `deconvolve(copt, capacity, q, delta)` (the stable forward recursion `g(c) = (f(c) − q·g(c−cap))/(1−q)`; fall back to rebuild-without-*i* when q ≥ 0.5 or the recursion loses mass) and `attribute_criticality(n, copt_inputs, voll)` → ranked rows.
- [x] Commit: `feat(gui): analytic leave-one-out criticality (class A, zero solves)`.

### Task 3: the endpoint + report integration

- [x] **Failing tests first:** `GET /results/copt` 204 on a network with no occurrence-bearing electrical generators; 200 with `{metrics: {lole_hours, eue_mwh, lolp_max, time_basis}, per_mode: [...], fleet: {units, must_take, delta_mw}, engine, fidelity}` on a fixture network (VoLL from the live solver config; € fields zero-with-note when VoLL is 0); results-range guard classification.
- [x] Implement in `routers/results.py` (compute on demand from the current network — no solve required; `p_nom_opt` when fresh dispatch exists); add to the range-guard `aggregates`.
- [x] Commit: `feat(gui): /results/copt — screening adequacy + FMECA ranking on demand`.

### Task 4: side-by-side surfacing

- [x] **Vitest first:** a COPT chip row renders screening LOLE/EUE with the analytic-convolution fidelity tooltip; a divergence note appears when both LP-proxy ENS and COPT EUE exist ("storage/network carry the adequacy" when COPT ≫ proxy); absent payload renders nothing.
- [x] Extend `pages/results/adequacy.tsx` (`CoptChips`), `api/simulation.ts` (`getCopt`), LostLoadTab render; `tsc -b` + vitest.
- [x] Commit: `feat(gui): COPT screening chips beside the LP proxy`.

## Done criteria

Ranked class-A FMECA rows and screening LOLE/EUE from pure arithmetic on the current network, exact against hand-computed cases, visible beside the LP proxy with both fidelities labelled; zero additional LP solves anywhere in the phase.
