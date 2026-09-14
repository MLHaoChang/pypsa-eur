# Design — Energy Hub Reference Design (gap closure)

**Status:** design / planning companion to `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md`.  
**Revised:** 2026-09-14 after independent plan reviews (modelling, product/architecture, gap-coverage).

**Goal.** Package the existing solution-FMEA and cost–availability stack into configurable **Energy Hub reference designs** for three archetypes, with outputs that link availability and cost. This doc pins product decisions; it does not replace the FMEA adequacy design (`2026-08-27-solution-fmea-adequacy-design.md`).

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
| 6 | **Import caps** = planning overlays (Link `p_nom` / GlobalConstraint / bus tags — pin per pack). **Not** certified interconnector adequacy unless outages are modelled. |
| 7 | **Storage duration** = scenario enum first (hours of autonomy / `max_hours` options). Continuous expansion later. Annual ENS/LOLE alone does not claim multi-day autonomy. |
| 8 | **DtC** = critical-load tags + islanding contingencies; **stress-on-fixed-plan first**. Planning mode blocked until slack/attribution spike. No per-load shed attribution on today’s one-slack-per-bus model. |
| 9 | **TEA** = post-process wrap (LCOE / optional LCOH) — no second cost engine. |
| 10 | **Dynamics** = **feasibility gate, not co-opt lever** (v1). EMT = escalation flag behind SCR only. |
| 11 | **RAM v1** = rate library + provenance (+ optional spare-lead-time modifier). Not full CMMS; planned-outage MC deferred. |
| 12 | **Report name** = `ReferenceDesignReport` only. Sections may be `not_established`. |
| 13 | **“Configurable outputs” (v1)** = fixed report schema + optional section inclusion / export columns — not arbitrary metrics. |
| 14 | **Class-B residual risk in report:** default Link-primary; document if AC Line/Transformer SCLOPF rows are merged or omitted. |
| 15 | **DSR in `weak_flexible`:** opt-in with double-count preflight (FMEA §4.4); never silently global. |

---

## 3. Archetype packs (normative intent)

### `strong_grid`
- Grid import treated as available economic resource (loose limits).
- Primary deliverable: frontier + least-cost plan at stated ENS; MC certify optional for MVP-A, recommended for published studies.
- SCR gate optional / informational.

### `weak_flexible`
- Tight power and/or energy import limits via pack overlay; DSR opt-in with preflight.
- DtC **stress** enabled by default (critical loads/buses under islanding).
- MC LOLE certify **required** for MVP-B.
- SCR preflight warn/block per P9 product rule (not part of P1 pack apply).

### `off_grid`
- Import capacity forced to zero for the study horizon.
- Storage and fuel/backup are primary adequacy resources; **storage-duration scenarios required** for honest co-opt.
- MC LOLE certify **required** for MVP-B.
- Report autonomy scenarios explicitly (do not equate annual ENS with multi-day sufficiency).

---

## 4. Reference design output (normative fields)

`ReferenceDesignReport` must be assemblable from existing study fragments:

- `archetype`, `pack_hash`, `assumptions_hash`
- `target` / `achieved` (ENS ‱, shed-hours, optional MC LOLE)
- `cost_at_target_eur` (+ `period_basis`)
- `frontier`: list of `{target, cost, achieved}` or `not_established`
- `sizing`: new capacity by carrier / technology
- `redundancy`: selected option id + alternatives table or `not_established`
- `levers`: import_cap / storage_duration scenario results or `not_established`
- `dtc`: critical unmet metrics under islanding or `not_established`
- `fmea_top`: top-N modes by €/yr criticality (+ residual-risk scope note)
- `tea`: `{lcoe_eur_per_mwh?, lcoh_eur_per_kg?, notes}` post-processed
- `gates`: `{scr?: pass/warn/fail, emt_recommended?: bool}` or `not_established`
- `completeness`: per-section status map
- `pipeline`: stages run, aborted, budgets consumed

---

## 5. Study pipeline (normative)

`EHStudyRunner` owns the product loop (packs alone are not enough):

1. Apply archetype pack (undo-safe)  
2. Target solve (ENS-cap)  
3. Frontier (optional, budget-capped)  
4. MC certify / coupling (per-archetype required flag)  
5. FMEA top-N (best-effort)  
6. Redundancy / import / storage scenarios when enabled  
7. DtC stress when enabled  
8. Assemble `ReferenceDesignReport`

Abort/partial behaviour matches frontier/MC patterns; partial reports must set completeness flags.

---

## 6. MVP definitions

| Slice | Includes | Archetypes |
|---|---|---|
| **MVP-A** | P0, P1, P1.5, P5 | `strong_grid` usable; other sections may be `not_established` |
| **MVP-B** | + P3a, P3c, P4a | All three archetypes with redundancy compare, import/storage scenarios, DtC stress |

P3b (auto-select redundancy) and P4b (DtC planning) complete co-opt but are post–MVP-B.

---

## 7. Non-goals (v1)

- Joint MILP of unit commitment + redundancy integers in one solve
- In-tree EMT simulation / dynamics↔adequacy co-simulation
- Statutory PRAS/Antares replacement
- Full maintainability / spares logistics program
- Treating import caps as firm interconnection adequacy without outage modelling
- Per-load DtC attribution on the current single slack-per-bus geometry
- Replacing the existing FMEA worksheet UX
- Rebuilding shipped Class-B Link sweep

---

## 8. Relationship to FMEA design

Solution FMEA remains the **diagnostic** under a single plan. The EH reference design is the **product wrapper**: archetype → orchestrated levers → existing co-opt/diagnostic engines → `ReferenceDesignReport`. Every point on an EH frontier still gets its own FMEA ranking (same principle as solution FMEA).
