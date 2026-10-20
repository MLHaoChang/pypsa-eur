# Data-centre grid readiness: spec

Status: needs-triage
Owner input: 2026-10-20 (brainstorm with the coordinator)

## Goal, in the owner's words

> "A complete Grid Compliance framework where developers can qualify their projects early and understand the implications of future grid requirements before finalizing power system architecture decisions."

AI-factory developers plan around today's connection rules. TSOs (Statnett, NVF 2026) are moving towards requirements on load dynamics, power quality, voltage support and stability. The product should flag that risk early ("red flag"). It should also carry the architecture shift to battery-backed MV UPS, large BESS, 800 VDC and SST, inside the existing PyPSA / gridspine workflow.

The requirements are assumptions until confirmed by TSO dialogue or clients. Every assumed limit is labelled "assumed" in the UI and reports.

## Owner decisions (2026-10-20)

- D1. The GPU load profile lives **inside the data-centre template** (`DataCentreLoadSpec`).
- D2. The compliance check is a **generic function** any template can call. Each template supplies its own inputs.
- D3. **No Statnett/NVF-specific grid-code profile for now.** Keep it generic: `generic_assumed` and `eu_rfg_dcc_ce`, plus the existing upload/extract flow for a TSO PDF.
- D4. All seven features below are in scope ("I like the 7 bullet points").
- D5. Start 01 and 04 now (two Opus sessions); 02, 03, 05, 06 and 07 queue behind them.

## What exists to build on

| Piece | Where |
|---|---|
| DC load spec (utilisation `ai_training`, PUE, redundancy, critical/curtailable share, gensets) | `pypsa-gui/backend/models/flex_archetypes.py` |
| BESS spec (fade, augmentation, AC/DC coupling) | same file, `BessSpec` |
| DC templates | `pypsa-gui/backend/project_templates/eh_datacenter*` |
| Grid-code profiles: upload, extract, review, publish | `services/campus_grid_code_service.py`, `gridspine/templates/data/grid_codes.yaml` |
| SCR gate (warn below 3.0, EMT recommended) | `services/adequacy/scr_gate.py` |
| Energization schedule in MW | `services/adequacy/energization.py` |
| Island sidecar: converter mode, droop, inertia, UPS behaviour, ride-through | `services/adequacy/island_config.py`, `gridspine/schema/island.py` |
| DtC stress re-dispatch, critical unmet per load | `services/adequacy/dtc.py` |
| Gridspine planning-dynamics study | `services/gridspine_service.py` |

## Features (one ticket each)

1. GPU / AI-training load profile generator, in the DC template. → `issues/01`
2. Grid-readiness check, generic, red-flag scorecard. → `issues/02`
3. Battery-based backup in place of diesel. → `issues/03`
4. ESS / UPS / converter control presets. → `issues/04`
5. Power-architecture comparison: AC / MV UPS / 800 VDC / SST. → `issues/05`
6. Flexibility-market revenue from reserved BESS/UPS headroom. → `issues/06`
7. Rolling operational optimisation (dispatch of BESS and flexible load against prices and grid limits). → `issues/07`

## Order

01 and 04 have no dependencies. 02 reads 01's dynamic envelope and 04's presets. 03 reads 01. 05 reads 03. 06 reads 03. 07 comes last.

## Out of scope for now

- A Statnett/NVF-specific grid-code profile (D3).
- Sub-second EMT simulation inside pypsa-gui. The check recommends EMT; it doesn't run it.
- Vendor- or product-branded naming in code or UI.
