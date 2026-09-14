# Design — Energy Hub Reference Design (gap closure)

**Status:** design / planning companion to `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md`.  
**Revised:** 2026-09-14 after independent plan reviews + **final gate assessor** (`GO WITH BINDING CONDITIONS`).

**Goal.** Package the existing solution-FMEA and cost–availability stack into configurable **Energy Hub reference designs** for three archetypes, with outputs that link availability and cost. This doc pins product decisions; it does not replace the FMEA adequacy design (`2026-08-27-solution-fmea-adequacy-design.md`).

**Gate:** Binding conditions from the assessor are normative in §2 (decisions 6, 16–18), §4 (completeness enum), §5 (report ownership), and §6 (import overlays).

---

## 1. What already exists (reuse)

| Capability | Role in EH product |
|---|---|
| ENS-capped expansion + VoLL | Plan at an availability target |
| ε-constraint frontier | Cost vs availability curve |
| Coupling / margin loops + sequential MC | Certify LOLE; iterate plan |
| COPT + FMEA worksheet (A/B/C/D) | Residual risk ranking on a solution |
| Class-B Link sweep | **Shipped** (`p_*_pu`→0); residuals only |
| Class-C parametric stress | Shipped; profiles/climate packs open |
| PRM + ELCC | Firm-capacity view |
| Gridspine plain SCR | Weak-grid **gate** input (EH warn/block is product rule) |
| Campaign / abort patterns | Budget substrate for EHStudyRunner |

Electricity-only adequacy remains the default until the multi-energy phase.

---

## 2. Decisions (pinned)

| # | Decision |
|---|---|
| 1 | **Plan on ENS (LP); certify on MC LOLE** when loops are run. Always report both ENS and shed-hours. |
| 2 | **When both ENS and LOLE targets are set:** planning uses ENS; acceptance uses MC LOLE; report both; LOLE failure fails certification even if ENS is met. |
| 3 | **Cost axis** = total system cost excluding shed (`excludes_shed_cost: true`), same as frontier. State period basis on every cost field. |
| 4 | **Archetypes** = packs (config + network overlays), not new solvers: `strong_grid`, `weak_flexible`, `off_grid`. |
| 5 | **Redundancy** = discrete options / train counts (scenario enum first, then outer-loop selection). Not continuous FOR derating. |
| 6 | **Import caps** = planning overlays per **§6** (normative selection + mutation). **Not** certified interconnector adequacy unless outages are modelled. |
| 7 | **Storage duration** = scenario enum first (hours of autonomy / `max_hours` options). Continuous expansion later. Annual ENS/LOLE alone does not claim multi-day autonomy. |
| 8 | **DtC** = critical-load tags + islanding contingencies; **stress-on-fixed-plan first**. Planning mode blocked until slack/attribution spike. No per-load shed attribution on today’s one-slack-per-bus model. |
| 9 | **TEA** = post-process wrap (LCOE / optional LCOH) — no second cost engine. |
| 10 | **Dynamics** = **feasibility gate, not co-opt lever** (v1). EMT = escalation flag behind SCR only. |
| 11 | **RAM v1** = rate library + provenance (+ optional spare-lead-time modifier). Not full CMMS; planned-outage MC deferred. |
| 12 | **Report name** = `ReferenceDesignReport` only. Sections may be `not_established` / `skipped`. |
| 13 | **“Configurable outputs” (v1)** = fixed report schema + optional section inclusion / export columns — not arbitrary metrics. |
| 14 | **Class-B residual risk in report:** default Link-primary; document if AC Line/Transformer SCLOPF rows are merged or omitted. |
| 15 | **DSR in `weak_flexible`:** opt-in with double-count preflight (FMEA §4.4); never silently global. |
| 16 | **Report ownership:** `EHStudyRunner` (P1.5) orchestrates stages and emits `ReferenceDesignReport` **only** via the P5 assembler (`assemble_reference_design_report`). No second report builder. |
| 17 | **EH study budget default:** `DEFAULT_EH_BUDGET_SOLVES = 30` (same ceiling philosophy as `campaign.DEFAULT_BUDGET_SOLVES`); override allowed up to `MAX_EH_BUDGET_SOLVES = 120`. |
| 18 | **Default pipeline stages (ordered):** `apply_pack` → `ens_solve` → `frontier` → `mc_certify` → `fmea_top` → `redundancy` → `levers` → `dtc_stress` → `assemble`. Stages after `ens_solve` may be skipped per pack/request; skipped → completeness `skipped`; required but missing → `not_established`. |

---

## 3. Archetype packs (normative intent)

### `strong_grid`
- Grid import treated as available economic resource (loose limits).
- Primary deliverable: frontier + least-cost plan at stated ENS; MC certify optional for MVP-A, recommended for published studies.
- SCR gate optional / informational.

### `weak_flexible`
- Tight power and/or energy import limits via pack overlay (§6); DSR opt-in with preflight.
- DtC **stress** enabled by default (critical loads/buses under islanding).
- MC LOLE certify **required** for MVP-B.
- SCR preflight warn/block per P9 product rule (not part of P1 pack apply).

### `off_grid`
- Import capacity forced to zero for the study horizon (§6).
- Storage and fuel/backup are primary adequacy resources; **storage-duration scenarios required** for honest co-opt.
- MC LOLE certify **required** for MVP-B.
- Report autonomy scenarios explicitly (do not equate annual ENS with multi-day sufficiency).

---

## 4. Reference design output (normative fields)

**Completeness enum (normative):** each report section carries  
`status: "ok" | "not_established" | "skipped"`.

- `ok` — section populated from a completed stage  
- `not_established` — stage was required or expected but did not produce evidence  
- `skipped` — stage intentionally not requested for this run  

`ReferenceDesignReport` fields:

- `archetype`, `pack_hash`, `assumptions_hash`
- `target` / `achieved` (ENS ‱, shed-hours, optional MC LOLE)
- `cost_at_target_eur` (+ `period_basis`)
- `frontier`, `sizing`, `redundancy`, `levers`, `dtc`, `fmea_top`, `tea`, `gates` — each a payload **or** empty with section status ≠ `ok`
- `completeness`: map of section name → status
- `pipeline`: stages run / skipped / aborted, budgets consumed

---

## 5. Study pipeline (normative)

`EHStudyRunner` owns the product loop (packs alone are not enough). Default stage order is decision 18. **Report emission:** runner calls `assemble_reference_design_report(...)` only — it must not construct a parallel JSON shape.

Abort/partial behaviour matches frontier/MC patterns; partial reports must set completeness flags.

---

## 6. Import overlay contracts (normative — gate binding condition)

**Selecting import Links** (first match wins, documented on pack apply):

1. Link attribute / custom attr `eh_role == "grid_import"`, else  
2. Either endpoint bus has `eh_poc == true` (or bus attr `eh_poc`), else  
3. Link `carrier` ∈ pack field `import_carriers` (default `["AC", "DC", "electricity"]` intersected with present carriers).

If none match: `strong_grid` is a no-op; `weak_flexible` / `off_grid` **preflight-error** (cannot apply island/import pack without identifiable import Links).

**Mutations (power)**

| Archetype | Mutation | Undo |
|---|---|---|
| `strong_grid` | **No-op** (no Link/`p_nom` change) | n/a |
| `weak_flexible` | Set each selected Link `p_nom` (and `p_nom_max` if present) to pack `import_p_nom_mw` (per-link equal split if one number); optional static `p_max_pu` left unchanged | restore saved `p_nom` / `p_nom_max` |
| `off_grid` | Force selected Links **out of service via `p_max_pu`/`p_min_pu` → 0** (keep `p_nom` > 0 so preflight `link_p_nom_invalid` does not fire — same discipline as Class-B Link outages). Optionally also clamp `p_nom_max` if present | restore saved `p_max_pu` / `p_min_pu` / `p_nom_max` |

**Energy (optional field on pack):** `import_energy_mwh_per_year` is **reserved**. Phase 1 apply is **power-only**; if the field is set, `apply_archetype_pack` MUST emit a warning and must NOT add a GlobalConstraint. Energy import caps land in P3c (or a later overlay phase), not P1.

**Firmness:** overlays are **planning limits**, not adequacy of the external grid. Report `levers.import_firmness = "planning_limit_only"` unless Link outages are modelled in the same study.

---

## 7. MVP definitions

| Slice | Includes | Archetypes |
|---|---|---|
| **MVP-A** | P0, P1, P1.5, P5 | `strong_grid` usable; other sections may be `not_established` |
| **MVP-B** | + P3a, P3c, P4a | All three archetypes with redundancy compare, import/storage scenarios, DtC stress |

P3b (auto-select redundancy) and P4b (DtC planning) complete co-opt but are post–MVP-B.

---

## 8. Non-goals (v1)

- Joint MILP of unit commitment + redundancy integers in one solve
- In-tree EMT simulation / dynamics↔adequacy co-simulation
- Statutory PRAS/Antares replacement
- Full maintainability / spares logistics program
- Treating import caps as firm interconnection adequacy without outage modelling
- Per-load DtC attribution on the current single slack-per-bus geometry
- Replacing the existing FMEA worksheet UX
- Rebuilding shipped Class-B Link sweep

---

## 9. Relationship to FMEA design

Solution FMEA remains the **diagnostic** under a single plan. The EH reference design is the **product wrapper**: archetype → orchestrated levers → existing co-opt/diagnostic engines → `ReferenceDesignReport`. Every point on an EH frontier still gets its own FMEA ranking (same principle as solution FMEA).
