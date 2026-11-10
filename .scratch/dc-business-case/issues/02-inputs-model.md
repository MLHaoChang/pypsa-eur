<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Business-case inputs model

Status: wontfix (superseded by ../../business-case/issues/02 (line-item model, on #1320's branch); its DC classes become 07's DC preset; owner BC-ALL-1, 2026-11-10)
Type: task
Blocked by: 01
Gated: yes (GK B)
Model tier: opus
Size: S

Read [the spec](../spec.md) §4, §5 and §9, and GK B conditions BC7 and BC9 in [01](01-design-plan.md) ## Comments, first.

## What to build

1. `models/business_case.py`: `BusinessCaseInputs` (pydantic), stored beside
   `FinanceInputs` on the project. Fields: revenue model(s) chosen (R1–R4) with
   R1 GPU-hour and R2 kW-month (DCBC-SCOPE-1; R4 and SLA credits later) with
   their prices (no defaults; `None` = not established), reserved share and
   contract indexation, sold share, kW per GPU, lease-up per phase, the R3
   pass-through flag with share and margin; facility capex per MW IT and life; IT
   hardware capex as a lump per GPU or per MW IT, life default 5 years (4–6
   sensitivity) and refresh years; non-energy opex lines
   (staff, maintenance share, insurance share, property-tax share, network,
   SG&A share); receivable days.
2. The researched defaults for the cost lines as a versioned defaults entry with
   source and year per value (marked assumed), from `research.md`.
3. Validation with field-named errors (ranges, life ≥ 1, shares in [0, 1]).

## Tests

Round-trip; every refusal names its field; defaults carry a source.

## Not in this slice

The engine, any route, any UI.
