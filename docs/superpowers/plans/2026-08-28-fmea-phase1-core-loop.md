# FMEA Phase 1 — The Core Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the target-first core loop of the adequacy feature (spec v4 §11 Phase 1): the user states a reliability target, gets a least-cost plan that meets it, and sees achieved-vs-target including shed-hours. Concretely: the system ENS cap and per-zone ceilings as first-class LP constraints, the two-tier slack (`demand_response` / `load_shedding`), binding detection with the coherence warnings, a minimal `AdequacyReport` endpoint, and the Reliability settings UI + readout.

**Architecture:** All constraint work follows the `_wrap_with_capex_budget` pattern (`services/solver_service.py:2664`) — a composed `extra_functionality` callback calling `n.model.add_constraints(...)`; never `PYPSA_GUI_ALLOW_USER_CODE`. Occurrence/metric primitives from Phase 0 (`services/adequacy/`) are consumed, not modified, except where a task names them. Phase 0's `SLACK_CARRIERS` centralisation is the load-bearing prerequisite for Task 4 — the source-guard test keeps every consumer honest when the second tier appears.

**Tech Stack:** unchanged (FastAPI / PyPSA 1.x / linopy / HiGHS; React + TS; pixi).

## Global Constraints

Same as Phase 0's plan (branch `claude/solution-fmea-integration-0mx5lc`; explicit staging; `test` pixi env; test-first with a demonstrated red; the two container-environmental compare failures are baseline). Additionally:

- **Live-solve tests are the standard here.** Phase 0 proved HiGHS mini-solves work in-suite (`tests/test_adequacy_metrics.py`); every constraint task must show the constraint *binding* in a real LP, not just the wrapper composing.
- **PR #4 gates still stand:** the two strict-xfails flip to XPASS when its merge lands — whoever is working this plan when that happens deletes the markers as part of the merge commit.

---

### Task 1: the system ENS cap

**Files:**
- Modify: `services/solver_service.py` (SolverConfig fields + `_wrap_with_ens_cap` + composition at ~:748), `models/schemas.py` (config schema), `services/validation_service.py` (coherence preflight)
- Test: `tests/test_adequacy_ens_cap.py` (new)

**Config:** `ens_cap_permyriad: float | None = None` — the target in **parts per ten thousand of served-period electrical demand** (spec §5.1's unit decision; `None` = feature off). JSON-safe optional float in the pydantic schema; dataclass default `None`.

**Semantics (the decisions an implementer must not re-litigate):**

- **Cap per investment period**, not one horizon cap: `Σ_t∈P w_gen[t] · Σ_b∈elec shed[b,t] ≤ (‱/1e4) · D_P` where `D_P` is period P's weighted electrical demand. One horizon cap would let a single period absorb everything — the same concentration argument as zones, applied in time. Single-period networks get one constraint.
- **Electrical scope** via `services/adequacy/metrics.electrical_columns` semantics: slack generators on buses whose carrier classifies electrical (`_canonical_load_carrier_key == "electrical"`). Slack membership via `services/adequacy/slack.slack_generator_mask` — never name literals.
- **Demand denominator computed inside the callback** from `n.loads` / `n.loads_t.p_set` *at optimize time* (post-assumptions, so load scalers are already applied), restricted to loads on electrical buses, weighted on the "generators" column. The cap must be a fraction of the demand the LP actually serves.
- **Variable selection:** slack dispatch lives in `n.model.variables["Generator-p"]`; select the slack names present at optimize time (the wrapper resolves lazily inside the callback — composition order vs slack creation is then irrelevant). Weight per snapshot and sum; `add_constraints(expr <= rhs, name=f"ens_cap_{period}")`.
- **Coherence preflight** (warnings, in `_check_lopf`): cap set with `voll <= 0` → the cap is meaningless (no shed variables exist; the LP either serves or is infeasible). Cap set with a suspiciously large value (> 100 ‱ = 1 %) → the "99 % trap" warning from spec §5.1, citing where real standards sit.

**Steps:**

- [x] **Failing tests first** (live solves, mini-network from `test_adequacy_metrics.py`'s pattern — load 100 MW, gen 60 MW, weights 3):
  1. *Cap binds:* voll=3000, cap generous enough to be feasible but tighter than what pure VoLL economics would shed (add a second, expensive generator, e.g. 200 €/MWh with p_nom 40, so the LP *can* serve load at a cost; without the cap it prefers shedding at implied cost < VoLL… pick numbers so unshackled shed > cap). Assert achieved weighted ENS ≈ cap within 1e-3 relative.
  2. *Cap loose:* cap far above the VoLL-optimal shed → achieved ENS equals the un-capped optimum (VoLL binds, not the cap).
  3. *Off by default:* `ens_cap_permyriad=None` adds no constraint (assert `ens_cap_` absent from `n.model.constraints` via a probe callback, or objective unchanged vs baseline).
  4. *Electrical scope:* an H2 bus + load shedding freely must not consume the electrical cap.
  5. *Preflight:* cap without voll warns; 150 ‱ warns.
- [x] Add config fields (dataclass + pydantic schema — keep names identical; `SolverConfig(**merged)` does the mapping).
- [x] Implement `_wrap_with_ens_cap` on the `_wrap_with_capex_budget` template, `[ENS]` log lines stating cap MWh, demand denominator, and slack count per period; compose at ~:748.
- [x] Preflight warnings in `validation_service`.
- [x] Commit: `feat(gui): system ENS cap as a first-class LP constraint`.

---

### Task 2: per-zone ceilings

**Files:** same as Task 1 + `tests/test_adequacy_ens_cap.py` extended.

**Config:** `ens_zone_cap_multiple: float | None = None` — per-zone ceiling as a multiple of the system target, applied to **that zone's own** electrical demand (`Ē_zone = multiple × ‱/1e4 × D_{P,zone}`). `None` = no zone ceilings; requires `ens_cap_permyriad` set (preflight-warn if set alone). Default surfaced in the UI as 3× (spec §5.1) but the backend takes what it is given.

**Semantics:**

- Zone = bus `country`, grouping electrical load-bearing buses; per (zone, period) constraint `ens_zone_{zone}_{period}`.
- **Empty-`country` degeneracy:** when every electrical bus has `country == ""`, the "zone" is one unnamed group and the ceiling collapses into a second system cap. Preflight **warning** (not error) naming it; the solver also logs it. `zone_field_populated` (Task 3) carries it to the report.
- Buses with blank `country` while others are named: blank forms its own `""` zone — log its membership so the user can see the stragglers.
- **Zone-named infeasibility, Phase 1 scope:** full elastic-relaxation diagnosis is out of scope. Instead: (a) `[ENS]` log lines list every zone cap with its demand share *before* optimize, so an infeasible run's log shows the candidate culprits; (b) preflight errors on the one decidable case — a zone whose ceiling is ≤ 0 while it has demand.

**Steps:**

- [x] **Failing tests first:** two-zone network (countries "AA"/"BB"), system cap loose, zone ceiling tight on AA → AA's achieved ENS ≈ its ceiling while BB sheds freely under the system cap; empty-country network → warning issued and constraint count == system-cap-only + the degenerate zone (assert the collapse is logged); multiple-without-cap preflight warning.
- [x] Implement inside `_wrap_with_ens_cap` (one wrapper owns both layers — they share the demand walk).
- [x] Commit: `feat(gui): per-zone ENS ceilings`.

---

### Task 3: binding detection, the report, and the endpoint

**Files:**
- Modify: `services/solver_service.py` (post-solve target evaluation, `_emit_state(adequacy_report=...)`, persistence via `results_state` like `last_lost_load`), `services/project_context.py` (state key registration — mirror `last_lost_load` at `:219`), `routers/results.py` (new `GET /results/adequacy`), `routers/projects.py` (persist/restore walk if keys are enumerated), `models/adequacy.py` (only if a field proves unbuildable — prefer not)
- Test: `tests/test_adequacy_report.py` (new)

**Semantics:**

- After a solve with the cap on, build a **minimal `AdequacyReport`**: `engine="lp_proxy"`, `fidelity="deterministic_scenario"`; `target` from config + achieved (capture totals + `metrics.shed_hours` on electrical columns); `metrics` (`ens_mwh`, `shed_hours`, `time_basis="hours_per_year"`); `cost.total_system_cost_eur = n.objective − lost_load_cost_eur` (**the Literal[True] exclusion, made true by construction**) with `period_basis` from `multi_investment_periods`; `inputs` (weather_years `["modelled"]` placeholder, voll, `assumptions_hash` = stable hash of the solver-config dict, `outage_rate_bases` from `resolve_outage_params` counts); `energy` (`involuntary_mwh` = capture total, `demand_response_mwh=0.0` until Task 4).
- **Binding classification:** compare per-period achieved vs caps at 1e-6 relative tolerance — any zone at its ceiling → `zone_cap`; else system cap hit → `system_cap`; else → `voll`. Emit the **coherence log line** naming the winner ("the ENS cap is set but VoLL is the effective standard" when `voll` wins with the cap on).
- `zone_field_populated` = any electrical load-bearing bus has non-blank `country`.
- Endpoint returns 204 when no report (cap off / unsolved), the serialized report otherwise — same convention as `/results/lost_load`.

**Steps:**

- [x] **Failing tests first:** live solve with binding cap → report's `binding == "system_cap"`, `ens_mwh ≈ cap`, cost excludes shed (assert `total_system_cost_eur ≈ objective − shed_cost`); loose cap → `binding == "voll"`; endpoint 204 before solve, 200 after; round-trips through save/restore (results_state pickle).
- [x] Implement; wire persistence exactly like `last_lost_load` (grep its five lifecycle sites from the Phase 0 audit: `project_context.py:219/262`, `solve_queue.py:407`, `simulation.py:581`, `projects.py` restore, `snapshots.py:546` — **every one of the six needs the new key or restore silently drops the report**).
- [x] Commit: `feat(gui): adequacy target evaluation + /results/adequacy`.

---

### Task 4: the two-tier slack (`demand_response`)

**Files:**
- Modify: `services/adequacy/slack.py` (the tier), `services/solver_service.py` (creation + capture + decomposition split), `models/schemas.py` + config plumbing, `services/validation_service.py` (double-count warning)
- Test: `tests/test_adequacy_dsr.py` (new); `tests/test_adequacy_slack.py` extended

**Config:** `dsr_price_eur_per_mwh: float = 0.0` (0 = tier off), `dsr_share_of_load: float = 0.0` (per-bus DSR capacity as a fraction of that bus's peak load — the volume cap that makes DSR a bounded resource, not a second unbounded slack), `dsr_buses: list[str] = []` (**opt-in**; empty + price>0 = warn-and-off, never silently global — spec §4.4's double-count hazard).

**Semantics:**

- New carrier `demand_response`, name prefix `__dsr_`, **added to `SLACK_CARRIERS` / `SLACK_NAME_PREFIXES`** — the Phase 0 source-guard then automatically drags every consumer through membership semantics. The two sites that must *distinguish* tiers rather than lump them:
  - the cost decomposition at the `is_slack_carrier` site (`solver_service.py:~2452`) — DSR cost goes to a new `dsr_cost` bucket, **not** `voll_shed_*` (the Phase 0 comment marks the spot);
  - the capture — `lost_load_t` stays involuntary-only; a parallel `dsr_t` frame + `dsr_total_mwh` join the capture dict; `EnergyBlock.demand_response_mwh` fills from it.
- **The ENS cap sums involuntary slack only** (Task 1's mask must exclude the DSR tier — assert it).
- **Double-count preflight warning** when a `dsr_buses` bus already hosts a flexible asset (a StorageUnit, or a Link whose bus0/bus1 is the bus): "DSR tier on a bus with modelled flexibility counts it twice".
- Slack sizing: `dsr p_nom = dsr_share_of_load × peak load at that bus`; `marginal_cost = dsr_price`. Same transient lifecycle as the VOLL slacks (mark/create/capture/remove) — reuse the machinery, don't fork it.

**Steps:**

- [x] **Failing tests first:** live solve where `dsr_price < gen marginal cost of the expensive unit < voll` → the LP uses DSR up to its volume cap before involuntary shedding; capture separates the tiers; ENS cap counts only the involuntary tier (a run whose DSR covers the gap reports ENS 0 and `binding="voll"`); decomposition's `voll_shed_mwh` excludes DSR; opt-in warning fires on empty `dsr_buses` with price set; double-count warning fires next to a StorageUnit.
- [x] Extend `slack.py` (tier constants + tier-aware helpers, e.g. `involuntary_mask` vs `slack_generator_mask`), then the creation/capture/decomposition sites.
- [x] Commit: `feat(gui): opt-in demand-response slack tier, split from unserved energy`.

---

### Task 5: the Reliability UI

**Files:**
- Modify: `frontend/src/api/types.ts` (SimulationConfig fields), `frontend/src/store/simulationStore.ts` (defaults), `frontend/src/pages/SolverSettings.tsx` (extend the existing `ReliabilityAssumptions` section at ~:1651 — it already owns VOLL), `frontend/src/pages/results/LostLoadTab.tsx` (achieved-vs-target readout), `frontend/src/api/simulation.ts` (`getAdequacy`)
- Test: colocated `.test.tsx` per repo convention

**Scope (deliberately thin — the worksheet is Phase 3):**

- Reliability section grows: ENS target input **in ‱ with the warning band** (static helper text placing real standards at 0.1–1 ‱ and GB's 3 h/yr; live warning styling when > 100 ‱ — the 99 % trap); zone-ceiling multiple (disabled until a target is set; helper notes the empty-`country` degeneracy); DSR price/share/buses (buses as a multi-select of load-bearing buses).
- LostLoadTab header: achieved ENS vs target chip, shed-hours chip, and the **binding badge** ("standard: ENS cap" / "standard: VoLL" / "standard: zone ceiling AA") from `/results/adequacy`; renders nothing when the endpoint 204s.
- Every number rendered from the report carries the fidelity tag ("LP proxy — not comparable to a statutory standard") per spec §7 — a tooltip, not a footnote.

**Steps:**

- [ ] **Vitest first** for: the ‱ warning trips at >100; the binding badge renders each variant; 204 → no chips.
- [ ] Types + store defaults + section + readout.
- [ ] `tsc -b` and full vitest; commit: `feat(gui): reliability target settings + achieved-vs-target readout`.

---

## Done criteria for the phase

- A user can: set VoLL + an ENS target (+ optional zone multiple, + optional DSR tier) in Solver Settings → Run → see achieved ENS, shed-hours and which standard actually bound, on a live solve, with every number provenance-tagged.
- All live-solve tests green; the ENS cap demonstrably binds and demonstrably ignores DSR and non-electrical shedding.
- `pixi run gui-tests` + vitest + `tsc -b` at baseline or better; the Phase 0 source-guard still passes with the second tier present (the proof the Task 1 refactor paid off).
- No number is presented without engine/fidelity; the report round-trips save/restore.
