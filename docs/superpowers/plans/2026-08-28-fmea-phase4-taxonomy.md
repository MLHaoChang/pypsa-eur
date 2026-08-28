# FMEA Phase 4 — Taxonomy Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** Fill the worksheet's remaining computed classes (spec v4 §4.1): **class B** — Link forced outages via fixed-capacity operational re-solves (the one place an LP re-solve is genuinely needed: deliverability, not supply); **class C** — correlated stress scenarios via whole-scenario re-solves. One shared contingency driver powers both; one aggregator endpoint feeds the worksheet all computed classes.

**Class C data honesty.** The v4 decision was "fund the data — bundle reference climate years". Real coincident ERA5-derived year sets cannot be procured from this environment (size, sources). This phase ships the *machinery*: a per-project scenario registry accepting parametric stress definitions (load/availability multipliers — loudly labelled `parametric`) and, forward-compatibly, uploaded profile sets. **Bundling the reference climate years is recorded as an open data-procurement follow-up**, not silently dropped: a real climate year later becomes just another scenario entry with real profiles.

**The driver's contract (spec §§7.2, plan-1 7d):**
- Runs **in-process on the network under the PyPSA lock**, never over HTTP (undo-snapshot + results-wipe per mutation otherwise).
- **Fixed-capacity operational re-solves:** every `*_nom_extendable` is transiently forced off with `p_nom = p_nom_opt` (where present) so severity is a dispatch question, not an investment one — the single biggest cost saving (spec §6-v1, kept).
- Each contingency: apply mutation (Link `p_nom → 0`; scenario: profile/load transforms) → `run_simulation` with a **private state sink** (the foreground `_state` is never touched) → read the capture's weighted EUE → undo.
- The sweep ends with one **base re-solve** so the network's dispatch tables are left in the base state, not the last contingency's.
- **Budget guard:** at most 20 class-B links / 10 scenarios per run; refuse beyond with a clear message. Runs in a worker thread with a `fmea_sweep` status state key (running/done/failed + results), solver-lifecycle style — never a long-blocking request.

**Criticality semantics (documented in code, spec §5.4):** the re-solve holds the outage for the whole horizon, so ΔEUE is the annual damage of full unavailability; expected annual criticality € = `unavailability × ΔEUE_full × VoLL` (first-order, no timing placement — the horizon covers all hours). occurrence = cycle frequency `8760·q/MTTR`; severity = criticality/occurrence. Class B rows: engine `lp_proxy`, fidelity `deterministic_scenario`, class `B`. Class C rows: same engine/fidelity, class `C`, occurrence = the scenario's `frequency_per_year`.

## Global Constraints

Phases 0–3 constraints apply. Live-solve tests remain the standard; sweeps in tests stay tiny (2–3 contingencies).

### Task 1: the shared contingency driver

- [x] **Failing tests first** (`tests/test_adequacy_sweep.py`): a 2-bus network where a Link is the only path to a load — base EUE 0; the link-out re-solve sheds exactly the stranded load (exact arithmetic); capacities frozen (an extendable generator does NOT grow during the contingency solve); the final base re-solve restores base dispatch (network EUE state = base); the foreground `_state` is untouched (private sink); budget guard refuses > caps.
- [x] Implement `services/adequacy/sweep.py`: `freeze_capacities(n) -> undo`, `run_contingency_sweep(network, lock, cfg, contingencies, state_update)` where a contingency is `(id, mutate(n) -> undo, meta)`; returns per-contingency `{delta_eue_mwh, capture}` + the base.
- [x] Commit: `feat(gui): fixed-capacity contingency sweep driver`.

### Task 2: class B — Link outages

- [x] **Failing tests first:** links with occurrence data become contingencies (Line/Transformer stay with SCLOPF — not this driver); rows validate (class B, lp_proxy/deterministic_scenario); criticality = q × ΔEUE_full × VoLL; a link with no occurrence data is skipped.
- [x] Implement `class_b_contingencies(n)` (in sweep.py) + the worker-thread runner and state key (`fmea_sweep`), routes `POST /results/fmea_sweep` (start; 409 while running) and `GET /results/fmea_sweep` (status + rows; 204 never-run) in `routers/results.py`; range-guard classification.
- [x] Commit: `feat(gui): class-B link-outage rows via the contingency sweep`.

### Task 3: class C — stress scenarios

- [x] **Failing tests first:** scenario registry sidecar round-trips (worksheet-service pattern: JSON, atomic, caps, validation — `frequency_per_year > 0`, multipliers in sane ranges, `kind: "parametric" | "profiles"`); a parametric cold-snap scenario (electrical load ×1.3, renewable availability ×0.5) re-solves into a class-C row whose ΔEUE matches the hand-computed shortfall; parametric rows carry a `parametric` marker in their mode_id/basis so the UI can label them.
- [x] Implement `services/adequacy/stress.py` (registry: `GET/PUT /api/projects/{name}/stress_scenarios`) + `class_c_contingencies(n, scenarios)` (load multiplier via `loads_t/p_set` transform, availability multiplier via `p_max_pu` transform, undo-safe) wired into the same sweep runner.
- [x] Commit: `feat(gui): class-C stress-scenario rows + per-project scenario registry`.

### Task 4: the aggregator + worksheet integration

- [ ] **Tests first:** `GET /results/fmea_modes` concatenates copt class-A rows + the last sweep's B/C rows (204 only when all empty); frontend merge consumes it unchanged (fmea.ts is already class-agnostic — assert a B row and a parametric C row interleave and badge correctly, incl. the parametric label).
- [ ] Implement the aggregator endpoint; switch `FmeaTab` from `getCopt` to `getFmeaModes` for rows (CoptChips keeps `getCopt`); a small "Run class B/C sweep" button on the tab (POST + poll status).
- [ ] `tsc -b` + vitest + backend sweep; commit: `feat(gui): all computed classes on one worksheet`.

## Done criteria

The worksheet shows classes A (analytic), B (link contingencies) and C (stress scenarios) plus expert D rows, on one ranking with per-class provenance; sweeps are budget-guarded, leave the network in its base state, and never touch the foreground solver state; the class-C data gap is documented, not hidden.
