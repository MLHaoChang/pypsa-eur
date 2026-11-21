<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03: "AC (LV UPS)" offers no grid services, yet frequency support and voltage control pass

Status: ready-for-agent
Type: task
Impact: high (a pass that contradicts the stated input; the architecture compare can't tell the options apart on grid services)
Templates: eh_datacenter, eh_datacenter_staged (Guided scorecard, Expert scorecard, Expert Power architecture compare, report)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: spec feature 5 ("which grid services it can offer (feeds 02)"); WALK-E2E-1 "results traceable to inputs"

## Repro

1. New project › Data Center Energy Hub, Power architecture "AC (LV UPS)" (the default), Backup mode Gensets, Create.
2. Guided › 3 Connection & grid › Site › Grid readiness: expand it and find "Frequency support offered by the site's converters".
3. Expert › Scenarios & optimisation › Grid study › Power architecture: enter Energy price 80, press Compare.

## Actual

- The form's hint for the chosen option: "AC (LV UPS): Double-conversion UPS at low voltage, the common baseline. Offers no grid services."
- Scorecard row: "Pass · Project: grid forming, synthetic inertia, frequency containment, voltage control". The note reads "Control preset: none; what each unit offers follows its control mode". It doesn't name the architecture (an MV UPS project's note does).
- Compare table, the AC (LV UPS) (this project) row: Grid services "none", Frequency support "Pass", Voltage control offered "Pass". The compare payload says why AC has none ("the LV UPS rectifiers are unidirectional power-factor-corrected front ends"), and every one of the four architectures still reads Pass/Pass.

Cause: `_support_rows` in `backend/services/adequacy/grid_readiness.py` (around line 1466) takes the island sidecar's unit services (the template's `ups_battery` set to grid forming) as offered, whatever the architecture. `service_verdict` then passes on those alone.

## Expected

With an explicitly chosen architecture, a unit the architecture can't connect to the grid as a service source isn't credited. For AC (LV UPS), the UPS behind unidirectional rectifiers offers nothing at the PoC. Frequency support then reads Warn ("none offered") unless another unit (a grid-facing BESS or genset) offers it. The row names the architecture and the unit each credited service comes from. The compare's two service columns agree with its "Grid services" column.

## Pointers

`backend/services/adequacy/grid_readiness.py:1426-1500` (`service_verdict`, `_arch_offer`, `_support_rows`); `backend/services/dc_architecture.py` (`services`); `frontend/src/pages/hubDesign/dcLoadForm.ts` `ARCH_HINT`. Needs a GK A plan (adequacy engine).
