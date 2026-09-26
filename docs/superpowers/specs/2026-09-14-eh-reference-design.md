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
| 8 | **DtC** = critical-load tags + islanding contingencies; **stress-on-fixed-plan first**, then **planning** per §10. No per-load shed attribution on today’s one-slack-per-bus model. **Amended 2026-09-26 (P16, Q5):** since P6(b) there is one VOLL slack per **Load**, so `per_load` attribution is available **opt-in** under a disclosed critical VOLL premium (§10 amendment); `bus_aggregate_not_per_load` stays the default. |
| 9 | **TEA** = post-process wrap (LCOE / optional LCOH) — no second cost engine. |
| 10 | **Dynamics** = **feasibility gate, not co-opt lever** (v1). EMT = escalation flag behind SCR only. |
| 11 | **RAM v1** = rate library + provenance (+ optional spare-lead-time modifier). Not full CMMS; planned-outage MC deferred. |
| 12 | **Report name** = `ReferenceDesignReport` only. Sections may be `not_established` / `skipped`. |
| 13 | **“Configurable outputs” (v1)** = fixed report schema + optional section inclusion / export columns — not arbitrary metrics. |
| 14 | **Class-B residual risk in report:** default Link-primary; document if AC Line/Transformer SCLOPF rows are merged or omitted. |
| 15 | **DSR in `weak_flexible`:** opt-in with double-count preflight (FMEA §4.4); never silently global. |
| 16 | **Report ownership:** `EHStudyRunner` (P1.5) orchestrates stages and emits `ReferenceDesignReport` **only** via the P5 assembler (`assemble_reference_design_report`). No second report builder. |
| 17 | **EH study budget default:** `DEFAULT_EH_BUDGET_SOLVES = 30` (same ceiling philosophy as `campaign.DEFAULT_BUDGET_SOLVES`); override allowed up to `MAX_EH_BUDGET_SOLVES = 120`. |
| 18 | **Default pipeline stages (ordered):** `apply_pack` → `ens_solve` → `frontier` → `mc_certify` → `fmea_top` → `redundancy` → `levers` → `dtc_stress` → `dtc_planning` → `assemble`. Stages after `ens_solve` may be skipped per pack/request; skipped → completeness `skipped`; required but missing → `not_established`. `dtc_planning` is opt-in (pack flag / explicit stages); default packs skip it. |

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

**Amendment (2026-09-25, P11 — MC certification; product decisions Q1/Q2/Q7 in [`plans/2026-09-25-eh-post-seal-implementation.md`](../plans/2026-09-25-eh-post-seal-implementation.md)):**

**New section and fields**
- `certification` joins the section list. Its payload is the MC LOLE evidence and a verdict.
- `certified: bool | None` is a new report field:
  - `True` only when the verdict is `pass`;
  - `False` on `fail` or `inconclusive`. This follows decision 2: a LOLE failure fails certification even when the ENS target is met;
  - `None` when no LOLE target is set or certification is not established.
- `mc_lole_h` is **per year** (`lole_hours / horizon_years`).
- `notes: list[str]` holds study-level disclosures that belong to no single section, such as the DSR preflight.

**Units and comparison**
- `AvailabilityTarget.target_lole_h` is **h/yr**.
- Certification compares the MC's per-horizon `lole_hours` and its 95% CI against `target_lole_h × horizon_years`, the coupling-loop convention.

**Verdict**
- `pass` iff CI upper ≤ target;
- `fail` iff CI lower > target;
- otherwise `inconclusive`.
- A target below the MC `resolution_floor_h` is `inconclusive`.
- The section is `not_established` in any of these cases:
  - `horizon_years ≤ 0`;
  - the modelled horizon is shorter than the largest unit MTTR;
  - the MC fleet is empty;
  - the run was aborted.

**Fleet boundary**
- The MC engine is copper-plate and network-free.
- Certification therefore samples a **hub-boundary copy**: every component on the far side of the selected import Links is removed.
- An import Link enters the MC fleet only when it carries its own outage data (decision 6). It then becomes one two-state unit of its hub-side capacity.
- Carrier-only Link selection (§6 rule 3), or a hub side that can't be told apart from the far side, is `not_established` with the instruction "tag `eh_role`/`eh_poc`".

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
- Per-load DtC attribution on the current single slack-per-bus geometry — *superseded 2026-09-26 (P16)*: available opt-in as `attribution="per_load"` under the §10 VOLL-priority rule; never the default, never without Load-keyed shed data
- Replacing the existing FMEA worksheet UX
- Rebuilding shipped Class-B Link sweep

---

## 9. Relationship to FMEA design

Solution FMEA remains the **diagnostic** under a single plan. The EH reference design is the **product wrapper**: archetype → orchestrated levers → existing co-opt/diagnostic engines → `ReferenceDesignReport`. Every point on an EH frontier still gets its own FMEA ranking (same principle as solution FMEA).

**Amendment (2026-09-25, P12):**
- **Per-frontier-point FMEA is deferred.** In v1 the EH study ranks **only the ENS plan**. `fmea_top` is the top-5 Class-B Link modes on the pack-applied `ens_solve` plan, frozen.
- **Frontier scope.** The `frontier` stage sweeps the pack target and its nearest default targets. It runs by default only for `strong_grid` (pack flag `frontier_default`) and takes at most ~40% of `budget_solves`.
- **Closing re-solves are skipped.** Both stages run on disposable private copies, so they skip the engines' closing re-solve (decision Q4). HTTP routes always restore.

## 10. DtC planning spike (P4b) — decision recorded

**Spike question.** Plan Phase 4b required a choice before expansion under a DtC overlay:

1. **Slack/attribution redesign** — per-load or multi-slack shed so critical unmet can be attributed inside a shared bus, or  
2. **Islanded topology + system ENS under retained critical demand** — Class-B island the PoC, keep critical-bus loads, remove non-critical-bus demand from the planning solve, expand under the existing ENS-cap LP, report **system** ENS/cost (still bus-aggregate honesty).

**Decision (2026-09-15):** **(2) Islanded topology + system ENS under retained critical demand.**

**Rationale.** Spec decision 8 and non-goals already refuse per-load attribution on one-slack-per-bus. P6 still owns any future multi-slack redesign. P4a already requires critical vs non-critical loads on **different buses**; zeroing non-critical-bus `p_set` for the planning solve is therefore an honest retained-critical overlay, not a fake per-load shed claim.

**Normative planning overlay**

| Step | Action |
|---|---|
| 1 | Apply each `islanding_contingencies` Link via Class-B `p_*_pu→0` (same as stress). |
| 2 | Retain critical demand: keep loads on critical buses; set non-critical-bus load `p_set`→0 for the solve (undo restores). |
| 3 | Run ENS-capped expansion (`run_simulation` / existing solver path) — not a second cost engine. |
| 4 | Report system ENS, cost@target, sizing; `attribution=bus_aggregate_not_per_load`; honesty notes must include `retained_critical_demand` and `no_per_load_attribution`. |

**Not claimed.** Per-load shed ranking; certified islanding resilience without the Class-B contingency set; multi-energy critical carriers (P6).

**Amendment (2026-09-26, P16 — decision Q5): opt-in per-Load attribution.**

*Premise change.* P6(b) replaced one-slack-per-bus with one VOLL slack per **Load**, and the capture (`lost_load_load_period_mwh`) is keyed by Load id. The remaining obstacle was degeneracy: every slack bids the same VOLL, so on a shared bus the LP's split of shed between critical and non-critical Loads is arbitrary and any per-Load number would be a solver artefact.

*Rule — VOLL priority.* With `DtcConfig.attribution="per_load"`, the DtC **stress** re-dispatch prices each **critical** Load's slack at `VOLL × (1 + ε_crit)` (ε_crit = 0.05, reported as `voll_premium_eps`). The LP therefore sheds non-critical Loads first; on a shared bus short by X MWh with non-critical demand N, critical unserved = max(0, X − N) per snapshot. The premium is a documented priority, not a valuation: it changes cost only by the ε term, and that cost is not reported.

*Scope [R5].* The premium is applied **only** inside the DtC stress re-dispatch (a context the DtC loop sets around its own solve on a disposable copy). It is not a `SolverConfig` field, so no API, saved project, `ens_solve`, frontier, sweep or user solve can carry it.

*Critical set.* Under `per_load`, critical Loads = `critical_load_ids` ∪ every Load on a critical bus (`critical_bus_ids` / `eh_critical`); every other Load is non-critical — including Loads that share a bus with a critical Load. Under the default `bus_aggregate_not_per_load`, a critical Load still promotes its whole bus (unchanged).

*Refusal.* `per_load` refuses (the stage fails with the reason) when the shed capture carries no Load-keyed data. There is no `"auto"`.

*Planning.* Under `per_load`, the retained-critical overlay zeroes every **non-critical Load** (not every non-critical bus); system ENS stays the planning metric. No premium is needed there — only critical demand remains.

*Honesty notes.* Under `per_load`, stress replaces `no_per_load_attribution` with `per_load_by_voll_priority` and reports `voll_premium_eps`; its rows add `critical_unserved_by_load`, `critical_loads`, `noncritical_loads`. Planning replaces it with `retained_critical_by_load` (no premium is applied there).

---


