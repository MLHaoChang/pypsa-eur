# Energy Hub Reference Design — Gap Closure Plan

> **For agentic workers:** Implement phase-by-phase. Prefer extending `pypsa-gui/backend/services/adequacy/` and existing frontier/loop runners over new parallel stacks.
>
> **Review revision (2026-09-14):** Updated after independent reviews — [Adequacy modelling](bc-7d546fbf-01ad-5eae-aa2e-01c8940b6283), [Product/architecture](bc-97591163-b6f7-5e4d-9cf1-0b2dd23bf334), [FMEA/gap-coverage](bc-ebad9414-b1ef-5ab4-b355-748248270b9b). See § Review deltas.

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

**Deliverables**
- Spec: `docs/superpowers/specs/2026-09-14-eh-reference-design.md` (keep in sync)
- Pydantic models / JSON fixtures for:
  - `EnergyHubArchetype`: `strong_grid` | `weak_flexible` | `off_grid`
  - `AvailabilityTarget`: ENS ‱ and/or LOLE h/yr + **precedence rule** when both set
  - `OptimizationLevers`: `sizing` (on), `redundancy` (off→P3), `import_cap`, `storage_duration`
  - `ReferenceDesignReport` fields per spec §4, including `completeness` / `not_established` flags per section
  - `EHStudyPipeline` stage list + solve budget

**Decisions pinned in the spec (do not re-litigate)** — all eight in spec §2, plus:
- Dynamics = gate, not co-opt lever (v1)
- ENS vs LOLE precedence when both configured
- Import representation + firmness by archetype
- Storage duration = scenario enum first (continuous `max_hours` later)
- DtC v1 = stress-first; planning gated
- Class-B residual risk scope (Link-only vs Link+SCLOPF merged) stated on the report
- `ReferenceDesignReport` naming lock

**Acceptance**
- [ ] Spec lists all pinned decisions explicitly (not a subset of five).
- [ ] Skeleton API types / JSON fixtures match `ReferenceDesignReport` (+ `gates`, `tea`, `dtc`, `pack_hash`, completeness flags).
- [ ] No UI required.

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
| `weak_flexible` | Tight power/energy import caps via **documented Link/GlobalConstraint overlay**; DSR **opt-in with double-count preflight** (FMEA §4.4) |
| `off_grid` | Import capacity = 0 overlay; islanded electrical balance; storage/fuel as primary adequacy resources |

**Steps**
- [ ] Implement pack apply/undo with pinned import overlay semantics (which Links, power vs energy, restore).
- [ ] DSR double-count preflight for `weak_flexible`.
- [ ] Three fixture overlays; **at least one path binds ENS** (not smoke-only).
- [ ] Document which existing endpoints the pack feeds (no duplicate engines).

**Acceptance**
- [ ] `pixi run gui-tests`: pack apply/undo + one live mini-solve per archetype.
- [ ] `strong_grid`: ENS binds; adequacy report returns.
- [ ] No SCR/gridspine dependency in P1.

---

## Phase 1.5 — EH study orchestrator

**Goal.** One job runs the reference-design pipeline; P5 only assembles.

**Pipeline (default stages)**
1. Apply archetype pack  
2. ENS-cap (or target) solve  
3. Optional frontier (budget-capped)  
4. Optional MC certify / coupling loop (required vs optional **per archetype** — pin in pack: recommend **required** for `off_grid` / `weak_flexible`)  
5. FMEA top-N (best-effort)  
6. Later stages (P3a/P4a) when enabled  
7. Emit `ReferenceDesignReport` with completeness flags  

**Steps**
- [ ] `EHStudyRunner` (+ abort/partial), reuse frontier/MC/campaign budget patterns.
- [ ] `POST /results/eh_study` (or project-scoped equivalent) + status + abort.
- [ ] Completeness: missing MC/frontier/redundancy/dtc → `not_established`, not silent omission.

**Acceptance**
- [ ] One API/job materializes a report for `strong_grid` after required stages.
- [ ] Abort leaves partial report with flags; does not corrupt project state.

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
- [ ] Scenarios: `base`, `n1_generation`, `n1_conversion`, `parallel_storage` (extensible).
- [ ] Runner: fixed availability target → cost vs achieved ENS/LOLE per scenario.
- [ ] Provenance: binding metric (ENS vs LOLE); rejected options must fail the **same** certify method.
- [ ] API + thin UI panel.

### 3b — Discrete outer-loop selection (`v1-nice` / co-opt complete)
- [ ] Small integer domains per asset class; pin max trains + **MC certify cadence** (every candidate vs finalists only).
- [ ] Select least-cost option meeting target; reuse coupling-loop **control-flow** only (not continuous bisection math).
- [ ] Out of scope: joint MILP with UC + redundancy.

### 3c — Import cap + storage duration scenario levers (`v1-blocker` for off-grid honesty)
- [ ] Scenario enum over `import_cap` and `storage_duration` (e.g. hours of autonomy), especially for `off_grid` / `weak_flexible`.
- [ ] Pin: import = planning limit (not certified interconnector adequacy) unless outages modelled.
- [ ] Note: annual ENS/LOLE ≠ multi-day autonomy sizing; report autonomy scenarios explicitly.

**Acceptance**
- [ ] 3a: ≥2 redundancy options with costs at fixed target.
- [ ] 3c: ≥2 storage-duration (or import) options affecting cost@target on off-grid fixture.
- [ ] 3b (when shipped): selected option meets target; losers fail same metric.

---

## Phase 4 — DtC modelling contract

### 4a — Stress mode (required for `weak_flexible` MVP-B)
- [ ] Sidecar `dtc_config.json`: `critical_load_ids` / bus tags, `islanding_contingencies`, targets.
- [ ] Fixed-plan islanding re-dispatch; report unmet critical load.
- [ ] **Do not claim per-load shed attribution** unless slack model changes (today: one slack per bus — FMEA §6.3). Prefer critical **buses** or islanded system ENS with non-critical demand shedable via explicit tier/tags.
- [ ] Wire as default stress for `weak_flexible` pack.

### 4b — Planning mode (gated)
- [ ] Design spike first: slack/attribution mechanism OR islanded topology + system ENS under retained critical demand.
- [ ] Only then: expansion under DtC planning overlay.

**Acceptance**
- [ ] 4a: grid disconnected → critical unmet metrics separate from non-critical; electrical-only projects unchanged when DtC off.
- [ ] Weak-flexible orchestrated run includes DtC stress block or `not_established` with reason.
- [ ] 4b: no implementation until spike decision recorded in spec.

---

## Phase 5 — `ReferenceDesignReport` + TEA wrap

**Goal.** One artifact linking availability and cost. Assembler only — orchestration is P1.5.

**Report contents** — per spec §4, plus:
- `completeness` map per section (`ok` | `not_established` | `skipped`)
- Cost period-basis AC (multi-period weighting consistent with frontier)
- `pack_hash` / `assumptions_hash` definition documented

**Steps**
- [ ] Backend assembler from adequacy, frontier, FMEA, redundancy, DtC, gates.
- [ ] `GET /results/eh_reference_design` (+ persist) populated by P1.5 job.
- [ ] Frontend: one “Reference design” summary + export (JSON/CSV) — not three disconnected tabs only.
- [ ] TEA: LCOE (optional LCOH) post-process from existing economics helpers — no second cost engine.
- [ ] “Configurable outputs” for v1 = fixed schema + optional section inclusion / export columns (or drop the word; pin in spec).

**Acceptance**
- [ ] MVP-A: orchestrated `strong_grid` study → one GET returns report with cost@target + frontier or explicit `not_established`.
- [ ] Empty `redundancy` / `dtc` / `gates` allowed with flags (does not fail MVP-A).
- [ ] Golden-fixture snapshot tests for stable export shape.
- [ ] MVP-B DoD: weak + off-grid packs produce filled dtc/lever sections per P3a/P4a/P3c.

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
