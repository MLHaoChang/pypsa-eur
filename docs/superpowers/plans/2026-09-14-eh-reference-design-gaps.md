# Energy Hub Reference Design — Gap Closure Plan

> **For agentic workers:** Implement phase-by-phase. Prefer extending `pypsa-gui/backend/services/adequacy/` and existing frontier/loop runners over new parallel stacks.
>
> **Review revision (2026-09-14):** Updated after independent reviews — modelling, product/architecture, gap-coverage. See § Review deltas.
>
> **Final gate (2026-09-14):** Assessor verdict **`GO WITH BINDING CONDITIONS`**. Binding conditions are in the companion spec (§2 decisions 6/16–18, §4 completeness enum, §5 report ownership, §6 import overlays). Implementation starts at **P0 only**, then P1 → P1.5 → P5 (MVP-A).

**Goal.** Package today’s solution-FMEA / cost–availability stack into a PGGI **Energy Hub reference design**: archetype packs, orchestrated study pipeline, redundancy and DtC as real levers, and a `ReferenceDesignReport` linking availability and cost.

**Already shipped (do not rebuild).**
- ENS-capped expansion, VoLL soft dual, ε-constraint frontier, MC coupling + margin loops
- COPT + sequential MC + ELCC/PRM
- FMEA A/B/C/D substrate: **Class-B Link path is shipped** (`p_max_pu`/`p_min_pu`→0); Class C **parametric** shipped, `profiles` / climate packs open
- Electricity-only adequacy reports; plain SCR in gridspine (report bands today — EH warn/block is an EH product rule, pinned in P9)

**Honest co-opt scope (v1).** Sizing (ENS-cap) + discrete redundancy (P3) + import/storage scenario levers are co-optimized iteratively. **Dynamic behaviors are a feasibility gate (SCR), not a co-opt lever.** EMT is a recommendation flag only.

**Tech stack.** Unchanged: FastAPI / PyPSA / linopy; React + TS; pixi `gui-tests`.

**Dependency order (revised).**

```
P0 contracts
 └─ P1 archetype packs (solver/network only; no SCR)
      └─ P1.5 EHStudyRunner (orchestrator + budget + completeness)
           ├─ P2 residual Class-B only (optional, parallel, non-blocking)
           ├─ P3a redundancy scenarios ──┐
           ├─ P4a DtC stress ────────────┼─→ P5 ReferenceDesignReport (+ TEA)
           └─ P3c import/storage levers ─┘
                ├─ P3b redundancy outer-loop select (co-opt complete)
                ├─ P4b DtC planning (gated on slack/attribution design)
                ├─ P6 multi-energy ENS/FMEA
                ├─ P7 RAM v1 library (planned-outage MC deferred)
                ├─ P8 Class-C profiles + synthetic years (real climate deferred)
                └─ P9 SCR gate / EMT flag (weak pack warn-only may land earlier as thin preflight)
```

**MVP split**
- **MVP-A (strong_grid):** P0 → P1 → P1.5 → P5 with sizing + frontier + cost@target + report (redundancy/dtc/gates may be empty/`not_established`).
- **MVP-B (three archetypes):** MVP-A + P3a + P4a + P3c + filled report fields for weak/off-grid.

---

## Phase QA gate + TDD protocol (mandatory)

Every phase follows this loop. **Do not start Phase N+1 until Phase N’s gate is cleared.**

1. **TDD — red:** Write failing tests that encode the phase acceptance criteria first. Demonstrate red (or skip-document if env blocks a live solve, with a unit-level red still shown).
2. **TDD — green:** Implement the minimum code to pass. No drive-by refactors.
3. **TDD — verify:** Re-run the phase test file(s); all must pass.
4. **QA gate assessor (independent):** Run an adversarial gate with verdict `GO` | `GO WITH BINDING CONDITIONS` | `NO-GO` against that phase’s acceptance + modelling honesty.
5. **Proceed rule:**
   - `GO` → commit, then start next phase at step 1.
   - `GO WITH BINDING CONDITIONS` → satisfy conditions (doc and/or code) in the same phase commit set, re-verify tests, then proceed.
   - `NO-GO` → **stop**; fix blockers; re-gate; do not touch the next phase.
6. Record the gate verdict in this plan under the phase (checkbox + one-line note).

**Gate rubric (per phase):** acceptance criteria met; no rebuild of shipped work; no scope leak into later phases; TDD evidence (red→green) present; tests fail-closed without climate/EMT data.

---

## Review deltas (must read before implementing)

| Change | Why |
|---|---|
| Demote P2 | Link Class-B already shipped; only residuals remain |
| Add P1.5 orchestrator | P5 assembler without a pipeline is not a product |
| Add P3c import/storage levers | Listed in P0 then orphaned; critical for off-grid/weak |
| Split P3a/P3b, P4a/P4b | Stress/comparison ship first; planning/select need extra design |
| Strip SCR from P1 | Belongs in P9; P1 is packs only |
| Soften P5 empty-field contract | Report valid with `not_established` sections |
| Narrow P7/P8 | Avoid MC planned-outage and real climate megaprojects in v1 |
| Pin naming | `ReferenceDesignReport` only (drop `ReferenceDesignOutput`) |
| Pin dynamics language | Gate, not lever |

---

## Phase 0 — Freeze the decision space (contracts only)

**Goal.** One schema everyone implements against. No solver changes yet.

**Gate prerequisites (done in spec before/with this phase)**
- Import overlay contracts pinned (spec §6)
- Completeness enum `ok | not_established | skipped` normative (spec §4)
- Report ownership: P1.5 runner → P5 assembler only (spec §5 / decision 16)
- Pipeline stages + `DEFAULT_EH_BUDGET_SOLVES = 30` / `MAX = 120` (decisions 17–18)
- Decision table is the full set (1–18), not a stale “five/eight”

**Deliverables**
- Spec: `docs/superpowers/specs/2026-09-14-eh-reference-design.md`
- Pydantic models in `pypsa-gui/backend/models/energy_hub.py`
- JSON fixtures under `pypsa-gui/backend/tests/fixtures/eh_archetypes/`
- Contract tests: `tests/test_energy_hub_contract.py`

**Models**
- `EnergyHubArchetype`, `AvailabilityTarget` (+ precedence), `OptimizationLevers`
- `SectionStatus`, `ReferenceDesignReport` (+ completeness map, pipeline)
- `EHStudyPipeline` stage literals + budget constants
- `ImportOverlayContract` fields mirroring spec §6 (`import_carriers`, `import_p_nom_mw`, …)

**Acceptance**
- [x] Spec lists decisions 1–18 and §6 import overlays.
- [x] Skeleton models + fixtures round-trip; completeness enum enforced.
- [x] Default pipeline stages and budget constants exported and tested.
- [x] No solver / UI / orchestrator behaviour yet.

---

## Phase 1 — Archetype packs (solver/network only)

**Goal.** Each archetype is a **parameter + network overlay pack** that configures today’s ENS-cap / frontier / MC stack. **No SCR in this phase.**

**Files (expected)**
- New: `backend/services/adequacy/archetypes.py` — `apply_archetype_pack(network, cfg) -> undo`
- New: fixtures under `backend/tests/fixtures/eh_archetypes/` (one network × three overlays, or three minis)
- Wire: SolverConfig patch + network assumption hooks (import Links, DSR opt-in)

**Pack definitions**

| Archetype | Pack contents |
|---|---|
| `strong_grid` | Loose/no import limit; economic ENS ladder; frontier primary |
| `weak_flexible` | Tight **power** import caps via Link `p_nom` overlay (spec §6); `import_energy_mwh_per_year` reserved (warn-only in P1); DSR **opt-in with double-count preflight** (FMEA §4.4) |
| `off_grid` | Import capacity = 0 overlay; islanded electrical balance; storage/fuel as primary adequacy resources |

**Steps**
- [x] Implement pack apply/undo with pinned import overlay semantics (which Links, power vs energy, restore).
- [x] DSR double-count preflight for `weak_flexible`.
- [x] Three fixture overlays; **at least one path binds ENS** (not smoke-only).
- [x] Document which existing endpoints the pack feeds (no duplicate engines).

**Feeds (no new engines):** SolverConfig `ens_cap_permyriad` (+ optional DSR fields via preflight helper) → existing `run_simulation` / `/results/adequacy` / frontier / MC when later orchestrated.

**Acceptance**
- [x] Pack apply/undo + live mini-solve per archetype (`test_energy_hub_archetypes.py`).
- [x] `strong_grid`: ENS binds (`binding == system_cap`).
- [x] No SCR/gridspine dependency in P1.
- [x] **QA gate cleared** — [P1 assessor](bc-37a2dcd6-5f89-5073-a006-7922bf6e71f7): `GO WITH BINDING CONDITIONS`; conditions satisfied (power-only energy field, off_grid `import_p_nom_mw=None`, energy-warn + `p_nom>0` tests).

**TDD evidence:** collection ImportError (red) → implement `archetypes.py` → archetype+contract tests green (incl. live solves).
---

## Phase 1.5 — EH study orchestrator

**Goal.** One job runs the reference-design pipeline; P5 only assembles.

**P1.5 sync MVP-A slice (HTTP deferred):** `run_eh_study` implements
`apply_pack` → `ens_solve` → `assemble`. Other default stages are **skipped**
(not left `pending`). `POST /results/eh_study` is deferred to a follow-up
within P1.5 or early P5 — gate must re-clear if HTTP lands later.

**Pipeline (default stages)**
1. Apply archetype pack  
2. ENS-cap (or target) solve  
3. Optional frontier (budget-capped) — skipped until implemented  
4. Optional MC certify — skipped; if `mc_certify_required` → gates `not_established`  
5. FMEA top-N — skipped until implemented  
6. Later stages (P3a/P4a) when enabled  
7. Emit `ReferenceDesignReport` via `assemble_reference_design_report` only  

**Steps**
- [x] `run_eh_study` sync driver (+ abort/undo), reuse solve + pack apply.
- [x] Completeness: missing MC/frontier/redundancy/dtc → `skipped` / `not_established`.
- [ ] `POST /results/eh_study` (+ status + abort) — deferred (plan amendment).

**Acceptance**
- [x] Sync study materializes a report for `strong_grid` after required stages.
- [x] Abort leaves pipeline.aborted and restores pack overlay.
- [x] **QA gate cleared** — [P1.5 re-gate](bc-d8211c3f-f8fa-51d5-8194-9ede0cd033b0): **GO** (after NO-GO fixes). HTTP still deferred.

**TDD evidence:** ImportError red → `eh_study`/`eh_report` → study tests green (incl. live solves + abort/undo).
---

## Phase 2 — Class-B Link residuals (optional, non-blocking)

**Goal.** Close **remaining** Link/FMEA edge cases only. **Do not re-implement Class B.**

**Candidate residuals (inventory first)**
- Multi-carrier Link filters
- Time-varying `links_t.p_min_pu` / restore semantics
- Document or merge Line/Transformer SCLOPF rows into FMEA top-N (or label report “Link-primary residual risk”)

**Acceptance**
- [ ] Written inventory of residuals vs shipped tests.
- [ ] Only ship fixes for confirmed gaps; else close phase as N/A.

---

## Phase 3 — Redundancy + import/storage levers

### 3a — Redundancy scenario enumeration (`v1-core`)
- [x] Scenarios: `base`, `n1_generation`, `n1_conversion`, `parallel_storage` (extensible).
- [x] Runner: fixed availability target → cost vs achieved ENS per scenario (`compare_redundancy_scenarios`).
- [x] Provenance: binding metric `ens` for all rows (same certify method).
- [x] API: `GET /results/eh_redundancy` (+ persist `eh_redundancy_comparison`); UI panel deferred.
- [x] Wired into `run_eh_study` when stage `redundancy` requested / default pipeline.

**Acceptance (3a)**
- [x] ≥2 redundancy options with costs at fixed ENS target.
- [ ] **QA gate cleared** (NO-GO N1–N4 addressed; awaiting re-gate)

**TDD evidence:** ImportError red → `redundancy.py` → 17 tests green (finite n1_conversion headroom, role selection, private sink, provenance hashes, claim wipe).

### 3b — Discrete outer-loop selection (`v1-nice` / co-opt complete)
- [ ] Small integer domains per asset class; pin max trains + **MC certify cadence** (every candidate vs finalists only).
- [ ] Select least-cost option meeting target; reuse coupling-loop **control-flow** only (not continuous bisection math).
- [ ] Out of scope: joint MILP with UC + redundancy.

### 3c — Import cap + storage duration scenario levers (`v1-blocker` for off-grid honesty)
- [x] Scenario enum over `import_cap` and `storage_duration` (e.g. hours of autonomy), especially for `off_grid` / `weak_flexible`.
- [x] Pin: import = planning limit (not certified interconnector adequacy) unless outages modelled.
- [x] Note: annual ENS/LOLE ≠ multi-day autonomy sizing; report autonomy scenarios explicitly.
- [x] API: `GET /results/eh_levers` (+ persist `eh_lever_comparison`); wired into `run_eh_study` when stage `levers` / pack levers enabled.

**Acceptance**
- [x] 3a: ≥2 redundancy options with costs at fixed target (P3a; B1–B3 closed in `99ef3cc9`).
- [x] 3c: ≥2 storage-duration (or import) options affecting cost@target on off-grid/weak fixtures (`test_energy_hub_levers.py`).
- [ ] 3b (when shipped): selected option meets target; losers fail same metric.
- [x] **QA gate cleared** — [P3c re-gate](bc-4e1a7ff3-1fb9-5f4c-80ab-1eea7a1d7919): **GO** (after binding-condition fix `7527dd05`; prior GO WITH BINDING CONDITIONS [bc-661df1e3-bf66-59d4-89b5-dc06fe6384fe](bc-661df1e3-bf66-59d4-89b5-dc06fe6384fe)).

---

## Phase 4 — DtC modelling contract

### 4a — Stress mode (required for `weak_flexible` MVP-B)
- [x] Sidecar `dtc_config.json`: `critical_load_ids` / bus tags, `islanding_contingencies`, targets (`DtcConfig`).
- [x] Fixed-plan islanding re-dispatch; report unmet critical load (`services/adequacy/dtc.py`).
- [x] **Do not claim per-load shed attribution** — bus-aggregate only (`attribution=bus_aggregate_not_per_load`).
- [x] Wire as default stress for `weak_flexible` pack (`dtc_stress_default=True`; stage in `run_eh_study`).
- [ ] **QA gate cleared** (assessor pending)

### 4b — Planning mode (gated)
- [ ] Design spike first: slack/attribution mechanism OR islanded topology + system ENS under retained critical demand.
- [ ] Only then: expansion under DtC planning overlay.

**Acceptance**
- [x] 4a: grid disconnected → critical unmet metrics separate from non-critical; electrical-only projects unchanged when DtC off (`test_energy_hub_dtc.py`).
- [x] Weak-flexible orchestrated run includes DtC stress block or `not_established` with reason.
- [ ] 4b: no implementation until spike decision recorded in spec.
- [ ] **QA gate cleared** (assessor pending)

---

## Phase 5 — `ReferenceDesignReport` + TEA wrap

**Goal.** One artifact linking availability and cost. Assembler only — orchestration is P1.5.

**Report contents** — per spec §4, plus:
- `completeness` map per section (`ok` | `not_established` | `skipped`)
- Cost period-basis AC (multi-period weighting consistent with frontier)
- `pack_hash` / `assumptions_hash` definition documented

**Steps**
- [x] Backend assembler enrichment: sizing + TEA/LCOE from existing cost/energy.
- [x] `GET /results/eh_reference_design` (+ persist key `eh_reference_design_report`).
- [ ] Frontend: one “Reference design” summary + export — deferred (backend GET/export ready).
- [x] TEA: LCOE post-process from cost ÷ served energy — no second cost engine.
- [x] “Configurable outputs” for v1 = fixed schema (`EXPORT_KEYS`) + golden fixture.

**Acceptance**
- [x] MVP-A: orchestrated `strong_grid` study → report with cost@target + sizing + TEA; frontier `skipped`/`not_established`.
- [x] Empty `redundancy` / `dtc` / `gates` allowed with flags.
- [x] Golden-fixture snapshot tests for stable export shape.
- [ ] MVP-B DoD: weak + off-grid packs produce filled dtc/lever sections per P3a/P4a/P3c.
- [x] **QA gate cleared** — [P5 re-gate](bc-6b83512d-30a2-531a-9fd6-b863eee782f9): **GO** (after NO-GO LCOE fix).

**TDD evidence:** P5 tests red → enrichment + ENS-honest LCOE → green (9 tests).

---

## Phase 6 — Multi-energy unserved energy (post–MVP-B)

**Goal.** Hub carriers beyond electricity in targets/FMEA — **after** electrical EH works.

**Prerequisite.** Slack/attribution redesign (per-bus load or per-carrier shed) — not “just extend metrics.”

**Acceptance**
- [ ] Sector-coupled fixture: unmet H₂ (or heat) in report/ranking.
- [ ] Electrical-only default path unchanged.

---

## Phase 7 — RAM v1 enrichment (not full RAM)

**Ship first**
- [ ] Asset-class rate library + provenance (extend `CARRIER_DEFAULTS` / `asset_health`).
- [ ] Optional spare-lead-time as **documented** severity modifier.
- [ ] Detectability: **add schema+UI or drop** — worksheet today has mitigability only.

**Defer**
- Planned-outage calendars in MC (large semantics change).

**Acceptance**
- [ ] Library loads; MC/COPT show library vs override provenance.
- [ ] No claim of full RAM/CMMS.

---

## Phase 8 — Class-C completeness (split)

**(a) `kind=profiles` runner + synthetic multi-year fixtures** — required for testability.  
**(b) Real climate-year bundles** — data procurement gate; do not block (a).

**Acceptance**
- [ ] Synthetic profiles run ranks Class-C modes with frequencies + abort/partial like other studies.
- [ ] Real climate packs optional behind data availability.

---

## Phase 9 — Dynamics gate (SCR → EMT flag)

**Goal.** Weak-grid **feasibility gate**, not co-opt.

**Steps**
- [ ] Pin EH product rule: warn vs block thresholds (gridspine bands are report-only today).
- [ ] Map POC buses + installed-MVA convention for PyPSA EH networks (not assume full gridspine study).
- [ ] Wire `gridspine.static.strength` (or documented proxy); report `gates.scr`.
- [ ] `emt_recommended` flag only — no in-tree EMT.
- [ ] Optional: thin warn-only preflight attachable to `weak_flexible` earlier without blocking P1.

**Acceptance**
- [ ] Weak-flexible: SCR below threshold → warn or block per pin; strong_grid does not require SCR pass.
- [ ] Full dynamics↔adequacy co-simulation remains out of scope.

---

## Suggested build sequence

| Step | Phase | Role |
|---|---|---|
| 1 | P0 | Contracts, completeness flags, naming |
| 2 | P1 | Archetype packs (no SCR) |
| 3 | P1.5 | Orchestrator + budget |
| 4 | P5 (MVP-A) | Report + TEA for strong_grid |
| 5 | P3a ‖ P4a ‖ P3c | Redundancy compare, DtC stress, import/storage levers |
| 6 | P5 (MVP-B) | Fill weak/off-grid sections |
| 7 | P2 | Residuals only (anytime parallel) |
| 8 | P3b / P4b | Co-opt select + DtC planning |
| 9 | P6–P9 | Multi-energy, RAM v1, Class-C profiles, SCR gate |

**Definition of done**
- **MVP-A:** archetype → orchestrated study → report with cost↔availability for `strong_grid`.
- **MVP-B:** same for all three archetypes with redundancy comparison, DtC stress (weak), import/storage scenarios (off-grid/weak), completeness flags everywhere.

**Explicitly deferred:** joint MILP redundancy+UC; in-tree EMT; statutory PRAS/Antares; full RAM/CMMS; real climate procurement; DtC per-load attribution without slack redesign; treating import caps as certified interconnection adequacy.

---

## Per-phase PR discipline

- One phase per PR (P3a ‖ P4a ‖ P3c may parallel after P1.5).
- Tests first; live mini-solves must **bind** targets where claimed.
- No parallel adequacy stack.
- Update checkboxes + “notes” when decisions change.
