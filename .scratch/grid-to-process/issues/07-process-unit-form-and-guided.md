# Process-unit form and the Guided process view

Status: needs-triage
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
