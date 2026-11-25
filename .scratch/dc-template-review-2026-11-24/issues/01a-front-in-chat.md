<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01a The cost, reliability and CO2 front in chat

Status: needs-triage
Blocked by: none
Source: issue 01. The front shipped without a chat tool (not_via_chat entry `multi_objective_front`, disposition `unbuilt`).
Severity: minor
Model: opus. Review: GK A (tool) + UX (chat words).

## Problem

The front's routes have no chat tool, so the assistant cannot run the study, read its points, or use a design:

- `GET /api/results/front`
- `POST /api/results/front`
- `POST /api/results/front/abort`
- `POST /api/results/front/apply`
- `GET /api/results/eh_front`

A user who asks in chat which designs are on the front, or to use one, is pointed to Results › Adequacy (Expert) or the Guided Results card.

## Fix

Add chat tools that start and stop the study, read the points in the panel's words (`pages/results/front/frontModel.ts`), and apply a point through the same revision-checked route, with the apply behind the usual confirmation. Then drop the `held-by-spec` route-parity declarations that point here and move the not_via_chat entry `multi_objective_front` off `unbuilt`.

## Comments
