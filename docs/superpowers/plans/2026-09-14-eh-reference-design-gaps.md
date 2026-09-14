# Energy Hub Reference Design — Gap Closure Plan

> **For agentic workers:** Implement phase-by-phase. Do not start Phase *N+1* until Phase *N* acceptance criteria pass. Prefer extending `pypsa-gui/backend/services/adequacy/` and existing frontier/loop runners over new parallel stacks.

**Goal.** Close the gaps between today’s solution-FMEA / cost–availability stack and a PGGI **Energy Hub reference design** product: archetype packs, redundancy and DtC as real levers, configurable availability↔cost outputs, then multi-energy / RAM / dynamics extensions.

**Already shipped (do not rebuild).** ENS-capped expansion, VoLL soft dual, ε-constraint frontier, MC coupling + margin loops, COPT + sequential MC + ELCC/PRM, FMEA classes A/B/C/D (B Link incomplete; C partial), electricity-only adequacy reports, plain SCR in gridspine.

**Tech stack.** Unchanged: FastAPI / PyPSA / linopy; React + TS; pixi `gui-tests`.

**Dependency order (hard).**

```
P0 contracts
 └─ P1 archetype packs ──┬─ P2 Class-B Link fix (can parallel with P1)
                         ├─ P3 redundancy lever
                         ├─ P4 DtC contract
                         └─ P5 EH output pack + TEA wrap
                              ├─ P6 multi-energy ENS/FMEA
                              ├─ P7 RAM enrichment
                              ├─ P8 Class-C completeness
                              └─ P9 dynamics / SCR gate / EMT hook
```

---

## Phase 0 — Freeze the decision space (contracts only)

**Goal.** One schema everyone implements against. No solver changes yet.

**Deliverables**
- Spec: `docs/superpowers/specs/2026-09-14-eh-reference-design.md`
- Pydantic models (or draft JSON Schema) for:
  - `EnergyHubArchetype`: `strong_grid` | `weak_flexible` | `off_grid`
  - `AvailabilityTarget`: ENS ‱ and/or LOLE h/yr
  - `OptimizationLevers`: sizing (default on), redundancy (off→on in P3), import_cap, storage_duration
  - `ReferenceDesignOutput`: sizing table, cost@target, frontier points, residual FMEA top-N, archetype id

**Decisions to pin in the spec (do not re-litigate later)**
1. Certification metric: plan on ENS (LP); accept on MC LOLE when loops are run.
2. Cost axis: total system cost excluding shed (same as frontier today).
3. Electricity-only until Phase 6.
4. Redundancy = discrete spare/train counts, not continuous derating.
5. DtC = critical-load subset + islanding contingency set (Phase 4).

**Acceptance**
- [ ] Spec merged with the five decisions above explicit.
- [ ] Empty/skeleton API types or JSON fixtures committed; no UI required.

---

## Phase 1 — Archetype packs on the existing pipeline

**Goal.** Each archetype is a **parameter + constraint pack** that drives today’s ENS-cap → frontier → MC loop without new co-opt math.

**Files (expected)**
- New: `backend/services/adequacy/archetypes.py` (pack builders)
- New: fixtures under `backend/tests/fixtures/eh_archetypes/`
- Wire: solver config defaults + optional `POST /results/eh_study` or project template apply
- UI (thin): archetype picker that applies pack to Solver settings / network assumptions

**Pack definitions**

| Archetype | Pack contents |
|---|---|
| `strong_grid` | Soft/no import limit; economic ENS ladder; frontier primary |
| `weak_flexible` | Tight `p_max` / energy import caps; SCR preflight warn; flexible load/DSR defaults |
| `off_grid` | Import capacity = 0; islanded electrical balance; storage/fuel as primary adequacy |

**Steps**
- [ ] Implement `build_archetype_pack(archetype) -> SolverConfig patch + network assumption hooks`.
- [ ] Three minimal reference networks (or one network with three overlays) runnable in tests.
- [ ] For each archetype: one ENS-cap solve + optional short frontier (≤3 points) + optional MC smoke.
- [ ] Document which existing endpoints the pack calls (no duplicate engines).

**Acceptance**
- [ ] `pixi run gui-tests` covers pack application + one live mini-solve per archetype.
- [ ] Manual/API path: apply pack → solve → `/results/adequacy` + `/results/frontier` return data.

---

## Phase 2 — Close Class-B Link outage gap

**Goal.** HVDC / Link contingencies behave like Line/Transformer outages in the FMEA sweep.

**Files**
- `services/adequacy/sweep.py` (or contingency driver)
- `services/solver_service.py` / SCLOPF helpers as needed
- Tests: extend FMEA sweep tests with a Link

**Steps**
- [ ] Define Link outage as `p_nom → 0` (or remove from dispatch) without breaking multi-carrier links.
- [ ] Include Links in class-B candidate set with clear carrier/bus filters.
- [ ] Infeasible contingencies → distinct outcome (same rule as transit-bus shed gap).

**Acceptance**
- [ ] Sweep ranks at least one Link failure mode on a fixture network.
- [ ] No regression on Line/Transformer SCLOPF path.

---

## Phase 3 — Redundancy as a co-optimization lever

**Goal.** Redundancy stops being mitigability text and becomes something the study can optimize.

**Approach (two sub-phases; ship 3a before 3b)**

### 3a — Scenario enumeration (required)
- [ ] Define redundancy scenarios: `base`, `n1_generation`, `n1_conversion`, `parallel_storage` (extensible list).
- [ ] Runner: for fixed availability target, solve each scenario; table of cost vs achieved ENS/LOLE.
- [ ] API + Adequacy tab panel: “Redundancy comparison”.

### 3b — Discrete outer loop (required for “co-optimize”)
- [ ] Decision variables: spare units / train count per asset class (integers, small domains).
- [ ] Outer loop or ε-constraint over redundancy options: minimize cost s.t. availability target (reuse coupling-loop controller pattern).
- [ ] Provenance: which redundancy choice was selected and why (binding LOLE or ENS).

**Out of scope for Phase 3:** joint MILP with UC + redundancy integers in one shot.

**Acceptance**
- [ ] At fixed LOLE/ENS target, study returns ≥2 redundancy options with costs.
- [ ] 3b selects a least-cost option that meets the target (tested on mini network).

---

## Phase 4 — DtC modelling contract

**Goal.** “Power and energy for DtC operation” is an executable constraint/stress pack, not a label.

**Contract**
- Inputs: `critical_load_ids` (or bus tags), `islanding_contingencies` (grid disconnect / Link outages), `dtc_lole_h` or `dtc_ens_cap`.
- Evaluation modes:
  1. **Stress (fixed plan):** Class-C-style re-dispatch under islanding; report unmet critical load.
  2. **Planning:** ENS/LOLE target applied only to critical load under islanded topology (expansion may add assets).

**Steps**
- [ ] Sidecar schema `dtc_config.json` (mirror worksheet/stress sidecars).
- [ ] Stress evaluator reusing sweep/MC substrate where possible.
- [ ] Optional planning mode: temporary network overlay (import→0) + critical-load-only shed metric.
- [ ] Wire into `weak_flexible` archetype pack as default DtC stress.

**Acceptance**
- [ ] Fixture: with grid disconnected, critical load LOLE/ENS reported separately from non-critical.
- [ ] Weak-flexible archetype run includes DtC stress block in study output.

---

## Phase 5 — Configurable EH outputs + TEA wrap

**Goal.** One reference-design artifact that links availability and cost.

**Output pack (`ReferenceDesignReport`)**
- Archetype id + pack hash
- Target vs achieved (ENS, shed-hours, MC LOLE if run)
- Cost@target (ex-shed) + frontier points
- Selected sizing summary (new capacity by carrier)
- Selected redundancy option (from P3)
- Top FMEA modes
- TEA wrap: LCOE (and optional LCOH if H₂ present) derived from existing cost + energy results — **post-process only**

**Steps**
- [ ] Backend assembler from existing report fragments (adequacy, frontier, FMEA, redundancy, DtC).
- [ ] `GET /results/eh_reference_design` (+ project persist).
- [ ] Frontend: single “Reference design” summary view / export (JSON + CSV).
- [ ] TEA helpers: reuse economics tabs’ cost aggregates; do not build a second cost engine.

**Acceptance**
- [ ] One API call returns the full pack after a standard archetype study pipeline.
- [ ] Export stable enough for golden-fixture snapshot tests.

---

## Phase 6 — Multi-energy unserved energy (only if EH needs it)

**Goal.** Hub carriers beyond electricity can appear in targets and FMEA severity.

**Steps**
- [ ] Extend demand/shed metrics to tagged critical carriers (e.g. H₂, heat) without breaking electrical ENS.
- [ ] Per-carrier caps or a weighted multi-carrier target (pin choice in a mini-spec).
- [ ] FMEA severity can attribute non-electrical shortfall.

**Acceptance**
- [ ] Off-grid (or sector-coupled) fixture with H₂ load: unmet H₂ appears in report and ranking.
- [ ] Electrical-only projects unchanged (default path).

---

## Phase 7 — RAM enrichment

**Goal.** Move from FOR/MTTR inputs toward a usable maintainability layer — still not a full CMMS.

**Steps**
- [ ] Asset-class RAM library (defaults + provenance) extending `occurrence.CARRIER_DEFAULTS` / `asset_health`.
- [ ] Optional planned-outage calendars excluded from FOR but applied in MC (or stress).
- [ ] Worksheet fields: detectability / mitigability stay expert; add “spare lead time” as optional severity modifier (documented, not silent).

**Acceptance**
- [ ] Library loads in UI; MC/COPT show which rates came from library vs override.
- [ ] One planned-outage fixture changes LOLE vs FOR-only baseline.

---

## Phase 8 — Class-C completeness

**Goal.** Finish the stress/climate-year path the FMEA design already scoped.

**Steps**
- [ ] Bundle reference climate / weather years as Class-C scenarios.
- [ ] Runner + worksheet integration; provenance in study report.
- [ ] Budget caps consistent with frontier/MC abort patterns.

**Acceptance**
- [ ] Multi-year stress run produces ranked Class-C modes with frequencies.
- [ ] Abort + partial results behave like other adequacy studies.

---

## Phase 9 — Dynamics gate (SCR → optional EMT hook)

**Goal.** Weak-grid archetype feasibility without making EMT a co-opt objective.

**Steps**
- [ ] Archetype preflight: run plain SCR (existing gridspine) on POC buses; band → warn/block.
- [ ] Study report section: grid-strength gate pass/fail.
- [ ] Hook only: if band ∈ {very_weak, …}, flag “EMT recommended” (exporter/placeholder) — no EMT solver in-tree required.

**Acceptance**
- [ ] Weak-flexible pack fails or warns closed-loop when SCR below threshold.
- [ ] Strong-grid pack does not require SCR pass.

---

## Suggested build sequence (summary)

| Step | Phase | Implements gap |
|---|---|---|
| 1 | P0 | Contracts for archetypes, levers, outputs |
| 2 | P1 | EH archetypes (strong / weak / off-grid) |
| 3 | P2 | Class-B Link outages |
| 4 | P3 | Redundancy co-optimization |
| 5 | P4 | DtC contract |
| 6 | P5 | Configurable availability↔cost (+ TEA wrap) |
| 7 | P6 | Multi-energy FMEA/ENS |
| 8 | P7 | RAM enrichment |
| 9 | P8 | Class-C completeness |
| 10 | P9 | Dynamics / SCR gate / EMT hook |

**Definition of done for the product slice (P0–P5):**  
User picks an EH archetype → runs target study → gets a reference-design pack with cost@availability, sizing, redundancy choice, FMEA residual risk, and DtC stress where applicable — all on top of the existing FMEA/frontier/MC stack.

**Explicitly deferred after P5:** full EMT co-simulation, statutory-grade PRAS/Antares exporters, full maintainability programs, joint MILP redundancy+UC.

---

## Per-phase PR discipline

- One phase per PR (P2 may merge beside P1).
- Tests first for new engines/runners; live mini-solves for anything that claims a target binds.
- No new parallel adequacy stack; extend `services/adequacy/*` and Results tabs.
- Update this plan’s checkboxes when a phase lands; add a short “notes” subsection if decisions change.
