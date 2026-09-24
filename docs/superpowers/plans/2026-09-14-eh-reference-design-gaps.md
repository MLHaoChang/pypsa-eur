# Energy Hub Reference Design — Gap Closure Plan

> **For agentic workers:** Implement phase-by-phase. Prefer extending `pypsa-gui/backend/services/adequacy/` and existing frontier/loop runners over new parallel stacks.
>
> **Review revision (2026-09-14):** Updated after independent reviews — modelling, product/architecture, gap-coverage. See § Review deltas.
>
> **Final gate (2026-09-14):** Assessor verdict **`GO WITH BINDING CONDITIONS`**. Binding conditions are in the companion spec (§2 decisions 6/16–18, §4 completeness enum, §5 report ownership, §6 import overlays). Implementation starts at **P0 only**, then P1 → P1.5 → P5 (MVP-A).

> **Integration (2026-09-24).** Complete stack (#48) + **P6(a) merged** ([#49](https://github.com/MLHaoChang/pypsa-eur/pull/49) → `master`). E2E GO — [`2026-09-23-eh-p6a-complete-stack-e2e.md`](../findings/2026-09-23-eh-p6a-complete-stack-e2e.md) (BE 161 + FE 107). **P6(b) spike:** [`2026-09-24-eh-p6b-multislack-spike.md`](../findings/2026-09-24-eh-p6b-multislack-spike.md) — recommend per-Load slacks (option C); implementation gated on product confirm. Other deferrals unchanged (climate P8b, spare-lead, planned-outage MC, chat tools, Class-C authoring UI).

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

**P1.5 sync MVP-A slice + HTTP:** `run_eh_study` implements
`apply_pack` → `ens_solve` → `assemble`. Other default stages are **skipped**
(not left `pending`). `POST /results/eh_study` (+ status + abort) landed as
the P1.5 HTTP follow-up — [HTTP re-gate](bc-b3498f66-fa5e-5466-8e30-478f4d3d4f3a): **GO**.

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
- [x] `POST /results/eh_study` (+ status + abort) — [HTTP re-gate GO](bc-b3498f66-fa5e-5466-8e30-478f4d3d4f3a).

**Acceptance**
- [x] Sync study materializes a report for `strong_grid` after required stages.
- [x] Abort leaves pipeline.aborted and restores pack overlay.
- [x] **QA gate cleared** — [P1.5 re-gate](bc-d8211c3f-f8fa-51d5-8194-9ede0cd033b0): **GO** (after NO-GO fixes). HTTP follow-up: [HTTP re-gate](bc-b3498f66-fa5e-5466-8e30-478f4d3d4f3a) **GO**.

**TDD evidence:** ImportError red → `eh_study`/`eh_report` → study tests green (incl. live solves + abort/undo).
---

## Phase 2 — Class-B Link residuals (optional, non-blocking)

**Goal.** Close **remaining** Link/FMEA edge cases only. **Do not re-implement Class B.**

**Candidate residuals (inventory first)**
- Multi-carrier Link filters — **fixed** (`in_metric_scope=False` for conversion/P2X)
- Time-varying `links_t.p_min_pu` / restore semantics — **fixed** (parity with DtC)
- Document or merge Line/Transformer SCLOPF rows into FMEA top-N — **document Link-primary; merge N/A**

**Acceptance**
- [x] Written inventory of residuals vs shipped tests — [inventory](docs/superpowers/findings/2026-09-16-eh-p2-class-b-residuals-inventory.md).
- [x] Only ship fixes for confirmed gaps; else close phase as N/A (SCLOPF↔FMEA merge closed N/A).
- [x] **QA gate cleared** — [P2 gate](bc-6181d9c2-c3fa-5016-a894-707a2babb1fd): **GO**.

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
- [x] **QA gate cleared** — Re-gate #3 [bc-55271fc9-feaa-5908-9674-1300466bfe8a](bc-55271fc9-feaa-5908-9674-1300466bfe8a): **GO WITH BINDING CONDITIONS** (B1–B3); B1–B3 closed in `99ef3cc9` (shipped with P3c); covered by P3c re-gate GO [bc-4e1a7ff3-1fb9-5f4c-80ab-1eea7a1d7919](bc-4e1a7ff3-1fb9-5f4c-80ab-1eea7a1d7919).

**TDD evidence:** ImportError red → `redundancy.py` → 17 tests green (finite n1_conversion headroom, role selection, private sink, provenance hashes, claim wipe).

### 3b — Discrete outer-loop selection (`v1-nice` / co-opt complete)
- [x] Small integer domains per asset class; pin max trains + **MC certify cadence** (every candidate vs finalists only).
- [x] Select least-cost option meeting target; reuse coupling-loop **control-flow** only (not continuous bisection math).
- [x] Out of scope: joint MILP with UC + redundancy.

### 3c — Import cap + storage duration scenario levers (`v1-blocker` for off-grid honesty)
- [x] Scenario enum over `import_cap` and `storage_duration` (e.g. hours of autonomy), especially for `off_grid` / `weak_flexible`.
- [x] Pin: import = planning limit (not certified interconnector adequacy) unless outages modelled.
- [x] Note: annual ENS/LOLE ≠ multi-day autonomy sizing; report autonomy scenarios explicitly.
- [x] API: `GET /results/eh_levers` (+ persist `eh_lever_comparison`); wired into `run_eh_study` when stage `levers` / pack levers enabled.

**Acceptance**
- [x] 3a: ≥2 redundancy options with costs at fixed target (P3a; B1–B3 closed in `99ef3cc9`).
- [x] 3c: ≥2 storage-duration (or import) options affecting cost@target on off-grid/weak fixtures (`test_energy_hub_levers.py`).
- [x] 3b: selected option meets target; losers fail same metric (`test_energy_hub_redundancy_select.py`).
- [x] **QA gate cleared** — [P3b gate](bc-8e4a860d-384c-58eb-8829-1936023baad6): **GO**.
- [x] **QA gate cleared** — [P3c re-gate](bc-4e1a7ff3-1fb9-5f4c-80ab-1eea7a1d7919): **GO** (after binding-condition fix `7527dd05`; prior GO WITH BINDING CONDITIONS [bc-661df1e3-bf66-59d4-89b5-dc06fe6384fe](bc-661df1e3-bf66-59d4-89b5-dc06fe6384fe)).

---

## Phase 4 — DtC modelling contract

### 4a — Stress mode (required for `weak_flexible` MVP-B)
- [x] Sidecar `dtc_config.json`: `critical_load_ids` / bus tags, `islanding_contingencies`, targets (`DtcConfig`).
- [x] Fixed-plan islanding re-dispatch; report unmet critical load (`services/adequacy/dtc.py`).
- [x] **Do not claim per-load shed attribution** — bus-aggregate only (`attribution=bus_aggregate_not_per_load`).
- [x] Wire as default stress for `weak_flexible` pack (`dtc_stress_default=True`; stage in `run_eh_study`).
- [x] **QA gate cleared** — [P4a re-gate](bc-1242dced-b02a-56fd-b508-6652bf5804b0): **GO** (after binding-condition fix `cf31c7b0`).

### 4b — Planning mode (gated)
- [x] Design spike first: slack/attribution mechanism OR islanded topology + system ENS under retained critical demand.
- [x] Only then: expansion under DtC planning overlay.

**Acceptance**
- [x] 4a: grid disconnected → critical unmet metrics separate from non-critical; electrical-only projects unchanged when DtC off (`test_energy_hub_dtc.py`).
- [x] Weak-flexible orchestrated run includes DtC stress block or `not_established` with reason.
- [x] 4b: spike decision recorded (spec §10 — islanded + retained critical); planning overlay shipped (`test_energy_hub_dtc_planning.py`).
- [x] **QA gate cleared** — [P4b re-gate](bc-c6486462-cde4-551a-94ce-a66848500349): **GO** (after binding-condition fix `0a10957e`; prior GO WITH BINDING CONDITIONS [bc-6a40e238-d91c-5097-a372-298c3ac45c56](bc-6a40e238-d91c-5097-a372-298c3ac45c56)).
- [x] **QA gate cleared** — [P4a re-gate](bc-1242dced-b02a-56fd-b508-6652bf5804b0): **GO**.

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
- [x] Frontend: one “Reference design” summary panel (start/poll/abort + report) — [panel](docs/superpowers/specs/2026-09-15-eh-reference-design-panel.md); sibling tables + per-table CSV included. QA: [frontend gate GO](bc-6e89ba0d-78ad-50b3-a511-1e6422ecbc52). Tables+CSV: [gate GO](bc-505793f5-18cb-5cb4-9316-e9d60670273f). Dynamics gate strip (SCR/EMT from P9 report): [FE SCR gates QA GO](bc-889e4231-3989-5dbc-ae8a-eb935f4be7f5).
- [x] TEA: LCOE post-process from cost ÷ served energy — no second cost engine.
- [x] “Configurable outputs” for v1 = fixed schema (`EXPORT_KEYS`) + golden fixture.

**Acceptance**
- [x] MVP-A: orchestrated `strong_grid` study → report with cost@target + sizing + TEA; frontier `skipped`/`not_established`.
- [x] Empty `redundancy` / `dtc` / `gates` allowed with flags.
- [x] Golden-fixture snapshot tests for stable export shape.
- [x] MVP-B DoD: weak + off-grid packs produce filled dtc/lever sections per P3a/P4a/P3c (`test_energy_hub_mvp_b.py`; soft-skip inapplicable lever kinds).
- [x] **MVP-B DoD QA gate cleared** — [re-gate](bc-542e73c5-e8ce-50da-ab39-51df2aa3efee): **GO** (after binding-condition fix `57b11e05`; prior GO WITH BINDING CONDITIONS [bc-cea1d58c-c457-50e9-bab8-7414cc598c1a](bc-cea1d58c-c457-50e9-bab8-7414cc598c1a)).
- [x] **QA gate cleared** — [P5 re-gate](bc-6b83512d-30a2-531a-9fd6-b863eee782f9): **GO** (after NO-GO LCOE fix).

**TDD evidence:** P5 tests red → enrichment + ENS-honest LCOE → green (9 tests).

---

## Phase 6 — Multi-energy unserved energy (post–MVP-B)

**Goal.** Hub carriers beyond electricity in targets/FMEA — **after** electrical EH works.

**Prerequisite.** Slack/attribution redesign (per-bus load or per-carrier shed) — not “just extend metrics.”

**Spike (2026-09-19):** [`docs/superpowers/findings/2026-09-19-eh-p6-multi-energy-spike.md`](../findings/2026-09-19-eh-p6-multi-energy-spike.md) — full per-Load multi-slack redesign deferred; **P6(a)** ships dedicated-bus honesty (P4b pattern).

**Status.** P6(a) **merged** (#49): `multi_energy` report section with `ens_by_carrier_mwh` when buses are carrier-dedicated; shared-bus fail-closed; electrical default unchanged. P6(b) spike recommends per-Load slacks (option C); implementation still gated.

**Acceptance**
- [x] Sector-coupled fixture: unmet H₂ in report (`test_energy_hub_multi_energy.py`).
- [x] Electrical-only default path unchanged (`multi_energy=skipped`).
- [ ] P6(b): per-Load / multi-slack attribution for shared-bus models — spike done; **implement after product confirm** ([spike](../findings/2026-09-24-eh-p6b-multislack-spike.md)).

---

## Phase 7 — RAM v1 enrichment (not full RAM)

**Inventory:** [`docs/superpowers/findings/2026-09-18-eh-p7-ram-v1-inventory.md`](../findings/2026-09-18-eh-p7-ram-v1-inventory.md)

**Ship first**
- [x] Asset-class rate library + provenance (already in `CARRIER_DEFAULTS` / `asset_health`; P7 exposes on COPT/MC wire).
- [ ] Optional spare-lead-time as **documented** severity modifier — **DEFERRED** (not required for acceptance; no silent severity scale).
- [x] Detectability: **DROP** — worksheet remains mitigability-only (FMEA Phase 3); no IEC detectability schema/UI in v1.

**Defer**
- Planned-outage calendars in MC (large semantics change).
- Spare-lead-time severity modifier (optional plan line).

**Acceptance**
- [x] Library loads; MC/COPT show library vs override provenance (`rate_source` + `library_citation`; `test_energy_hub_ram_v1.py`).
- [x] No claim of full RAM/CMMS (`ram_note` on COPT/MC payloads).
- [x] **QA gate cleared** — [P7 assessor](bc-e90602ef-ed5b-5bea-8b44-7fa4761b4335): **GO** (provenance-only; 4/4 tests green).
- [x] Frontend disclosure: COPT/MC chips for `rate_source` counts + `ram_note` — [P7 RAM FE QA](bc-360b2266-9712-5f68-9693-6fc675592cd5): **GO** (79 tests; fail-closed pre-P7).

**TDD evidence:** KeyError red → wire `rate_source` / `units_provenance` / `library_citation` → 4 tests green. FE: `ramChipText` red → chips → adequacy+McPanel green.

---

## Phase 8 — Class-C completeness (split)

**(a) `kind=profiles` runner + synthetic multi-year fixtures** — required for testability.  
**(b) Real climate-year bundles** — data procurement gate; do not block (a).

**Shipped (a)**
- Inline `loads_p_set` / `generators_p_max_pu` (or `profile_pack`) swaps absolute series
- Occurrence basis `scenario:profiles`; incomplete → fail-closed `profiles_incomplete`
- Synthetic packs: `tests/fixtures/eh_class_c/synth_*.json`
- Abort/partial via shared contingency sweep (same as parametric)

**Acceptance**
- [x] Synthetic profiles run ranks Class-C modes with frequencies + abort/partial like other studies (`test_energy_hub_class_c_profiles.py`).
- [x] Real climate packs optional behind data availability (procurement deferred; incomplete stubs allowed in registry).
- [x] **QA gate cleared** — [P8 assessor](bc-520276e1-35ca-5e40-bdbb-fa300ef257fe): `GO WITH BINDING CONDITIONS`; conditions satisfied (membership test pin; snapshot-length row fail-closed).

**TDD evidence:** `profiles_not_supported_yet` / ImportError red → profiles mutate + packs → 8 tests green (+ stress/F1m2 regression).

---

## Phase 9 — Dynamics gate (SCR → EMT flag)

**Goal.** Weak-grid **feasibility gate**, not co-opt.

**Pinned product rule (EH, not gridspine):**
- SCR ≥ 3.0 → `gates.scr=pass`, `emt_recommended=False`
- SCR < 3.0 → `gates.scr=warn`, `emt_recommended=True` (includes SCR < 2; **`fail` reserved** — thin slice is warn-only)
- Band edges reuse `gridspine.static.strength.SCR_BANDS` (2, 3, 5); on-edge SCR=3 belongs to pass
- Proxy (no full 60909): `buses.eh_sk_mva` / (`buses.eh_ibr_mva` or installed IBR Generator MW-as-MVA) at `eh_poc` buses; gate uses **min** SCR
- Archetype scope: **`weak_flexible` only**; `strong_grid` / `off_grid` → gates `skipped`
- Partial PoC coverage is **fail-closed** (`not_established` if any `eh_poc` lacks `eh_sk_mva`/IBR)

**Steps**
- [x] Pin EH product rule: warn vs block thresholds (gridspine bands are report-only today).
- [x] Map POC buses + installed-MVA convention for PyPSA EH networks (not assume full gridspine study).
- [x] Wire `gridspine.static.strength` band edges + documented `eh_sk_mva` proxy; report `gates.scr`.
- [x] `emt_recommended` flag only — no in-tree EMT.
- [x] Thin warn-only preflight attachable to `weak_flexible` (`services/adequacy/scr_gate.py` + `run_eh_study`).

**Acceptance**
- [x] Weak-flexible: SCR below threshold → warn (thin slice); strong_grid does not require SCR pass (`test_energy_hub_scr_gate.py`).
- [x] Full dynamics↔adequacy co-simulation remains out of scope.
- [x] **QA gate cleared** — [P9 assessor](bc-52970cf8-ad19-57f3-bad7-bc1c059f5552): `GO WITH BINDING CONDITIONS`; conditions satisfied (SCR↔MC orthogonality test, partial-PoC fail-closed, TDD count corrected).
- [x] Frontend Dynamics gate strip — [FE SCR gates QA](bc-889e4231-3989-5dbc-ae8a-eb935f4be7f5): **GO**.

**TDD evidence:** ImportError/red → `scr_gate.py` + assemble `gates=` + weak_flexible wiring → 14 tests green (incl. orthogonality + fail-closed PoC).

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
