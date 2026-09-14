# Design — Energy Hub Reference Design (gap closure)

**Status:** design / planning companion to `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md`.

**Goal.** Package the existing solution-FMEA and cost–availability stack into configurable **Energy Hub reference designs** for three archetypes, with outputs that link availability and cost. This doc pins product decisions; it does not replace the FMEA adequacy design (`2026-08-27-solution-fmea-adequacy-design.md`).

---

## 1. What already exists (reuse)

| Capability | Role in EH product |
|---|---|
| ENS-capped expansion + VoLL | Plan at an availability target |
| ε-constraint frontier | Cost vs availability curve |
| Coupling / margin loops + sequential MC | Certify LOLE; iterate plan |
| COPT + FMEA worksheet (A/B/C/D) | Residual risk ranking on a solution |
| PRM + ELCC | Firm-capacity view |
| Gridspine plain SCR | Weak-grid feasibility gate (later phase) |

Electricity-only adequacy remains the default until the multi-energy phase.

---

## 2. Decisions (pinned)

| # | Decision |
|---|---|
| 1 | **Plan on ENS (LP); certify on MC LOLE** when loops are run. Always report both ENS and shed-hours. |
| 2 | **Cost axis** = total system cost excluding shed (`excludes_shed_cost: true`), same as frontier. |
| 3 | **Archetypes** = packs (config + network assumption overlays), not new solvers: `strong_grid`, `weak_flexible`, `off_grid`. |
| 4 | **Redundancy** = discrete options / train counts (scenario enum first, then outer-loop selection). Not continuous FOR derating. |
| 5 | **DtC** = critical-load subset + islanding contingency set; stress-on-fixed-plan first, planning overlay second. |
| 6 | **TEA** = post-process wrap (LCOE / optional LCOH) over existing cost and energy results — no second cost engine. |
| 7 | **EMT** = escalation flag behind SCR gate; not a co-optimization objective in v1. |
| 8 | **RAM v1** = rate library + provenance + optional planned-outage calendars; not full CMMS. |

---

## 3. Archetype packs (normative intent)

### `strong_grid`
- Grid import treated as available economic resource (loose limits).
- Primary deliverable: frontier + least-cost plan at stated ENS/LOLE.
- SCR gate optional / informational.

### `weak_flexible`
- Tight power and/or energy import limits; DSR / flexibility defaults on.
- DtC stress pack enabled by default (critical loads under islanding).
- SCR preflight warn/block on POC buses.

### `off_grid`
- Import capacity forced to zero for the study horizon.
- Storage and fuel/backup are primary adequacy resources.
- Certification emphasizes multi-hour/multi-day energy sufficiency (ENS + MC).

---

## 4. Reference design output (normative fields)

`ReferenceDesignReport` must be assemblable from existing study fragments:

- `archetype`, `pack_hash`, `assumptions_hash`
- `target` / `achieved` (ENS ‱, shed-hours, optional MC LOLE)
- `cost_at_target_eur` (+ period basis)
- `frontier`: list of `{target, cost, achieved}` (may be empty)
- `sizing`: new capacity by carrier / technology
- `redundancy`: selected option id + alternatives table (may be empty until redundancy phase)
- `dtc`: critical-load unmet metrics under islanding (may be empty)
- `fmea_top`: top-N modes by €/yr criticality
- `tea`: `{lcoe_eur_per_mwh?, lcoh_eur_per_kg?, notes}` post-processed
- `gates`: `{scr?: pass/warn/fail, emt_recommended?: bool}`

---

## 5. Non-goals (v1)

- Joint MILP of unit commitment + redundancy integers in one solve
- In-tree EMT simulation
- Statutory PRAS/Antares replacement
- Full maintainability / spares logistics program
- Replacing the existing FMEA worksheet UX

---

## 6. Relationship to FMEA design

Solution FMEA remains the **diagnostic** under a single plan. The EH reference design is the **product wrapper**: archetype → levers → existing co-opt/diagnostic engines → `ReferenceDesignReport`. Every point on an EH frontier still gets its own FMEA ranking (same principle as solution FMEA).
