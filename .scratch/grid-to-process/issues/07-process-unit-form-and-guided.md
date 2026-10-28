# Process-unit form and the Guided process view

Status: ready-for-agent (triage 2026-10-28: 02 and 04 are done; the form and the Production tiles are not built on master)
Type: task
Blocked by: 02, 04
Gated: no
Model tier: sonnet
Size: M

Read [the design](../design.md) §2.2 (item 3) first.

## What to build

1. **A "Process unit" type** in `frontend/src/layout/CreationForm.tsx`:
   - one product output;
   - 1–3 inputs, each with a rate per t of product;
   - an electricity input in kWh/t;
   - a UC section: min load, min up and down, restart time.

   The form computes the PyPSA efficiencies, including the negative `efficiency2..`. The user
   never types an efficiency. Writes go through `utils/assetWrite.ts`.
2. **Guided Results** gets a "Production" tile group from ticket 04's process view:
   - tonnes made and delivered;
   - lost tonnes and EUR;
   - inventory.

   Figures go through `kpiFigure` and `format.ts`, following the UX notes in
   `.scratch/generic-site/design.md`.

## Acceptance

- Creating an HB-like unit through the form gives the same Link as the template's.
- A frontend test covers the efficiency arithmetic.

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built.
- The process-unit fields from 02 exist as Link fields. They are revealed in the Link form when `eh_process` is ticked.
- `GET /results/process_view` (ticket 04) exists, but no frontend code reads it.
- `CreationForm` has no "Process unit" type, and Guided Results has no Production tiles.

### Agent brief

**Category:** enhancement
**Summary:** a "Process unit" creation type that derives the Link efficiencies from rates per tonne, and a Guided "Production" tile group fed by the process view.

**Current behaviour:** a process unit is made as a plain Link. The user ticks `eh_process` and types `efficiency`, `efficiency2..` by hand, including the negative ones for extra inputs.

**Desired behaviour:**
- The creation form offers "Process unit". The user enters:
  - one product output;
  - 1–3 inputs, each as a rate per t of product;
  - electricity in kWh/t;
  - the UC fields (min load, min up and down, restart time).
- The form writes one Link with `eh_process` true and the efficiencies derived from those rates. bus0 and the signs follow how `eh_ammonia_staged` builds `haber_bosch`.
- The user never types an efficiency. The write goes through the existing asset-write path.
- Guided Results shows a "Production" tile group from the process view: tonnes made and delivered, lost tonnes and EUR, and inventory.
  - Figures go through `kpiFigure` and the `format.ts` helpers.
  - When the network has no process unit, the group is absent, not a row of zeros.

**Acceptance:**
- [ ] A unit entered with haber_bosch's rates gives the same bus0..busN, efficiencies and UC attributes as `eh_ammonia_staged`'s `haber_bosch` Link (frontend test against those numbers).
- [ ] A frontend unit test covers the efficiency arithmetic, including a negative `efficiency2` and the kWh/t → MWh conversion.
- [ ] The Production tiles render from a mocked `/results/process_view` payload, and are absent for a network with no product bus.
- [ ] The existing CreationForm and Guided Results tests pass unchanged.

**Out of scope:** new backend fields or endpoints; the assistant (14); heat levels (15).

**Carried from 09 (GK A condition on 09's plan):** issue 09 added `delivery_interval_h` on Store (hours between
shipments out of an inventory; absent or 0 = continuous) to `models/process_unit.PROCESS_CUSTOM_COLUMNS`, set by
templates and read by the `inventory_cadence_needs_year` validator rule. Its user-facing registration lands here, as
the one exception to "no new backend fields": the Store `FieldSpec` (`services/asset_schema/fields.py`), the
`attrs_doc` line, the `StoreCreate` field and lazy-column set in `models/schemas.py`, and the regenerated
`assetSchema.json` fixture, so a hand-built inventory can declare its cadence.

**Likely files:** `pypsa-gui/frontend/src/layout/CreationForm.tsx`, `pypsa-gui/frontend/src/utils/assetWrite.ts`, `pypsa-gui/frontend/src/pages/Results.tsx`, `pypsa-gui/frontend/src/components/Kpi.tsx`, `pypsa-gui/frontend/src/api/types.ts`.
**Blocked by:** none (02 and 04 are done). This is a UI change, so it needs the UX reviewer's OK, and the builder tells UX where the change sits before building.
**Model:** Sonnet (frontend only).
