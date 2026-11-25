<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02b Chat does not read the CO₂ limit goal's result

Status: needs-triage
Blocked by: none (02, #1987 and #2007, built the route and the UI)
Source: issue 02's Comments (PR 2). Issue 02 is done; this issue now holds the route in route parity.
Severity: minor
Model: sonnet. Review: GK B.

## Problem

`GET /api/results/co2_limit` returns the limit, each period's cap and dual, and the typed refusal (`co2_cap_unreachable`, `co2_cap_duplicate`, `co2_cap_invalid`). The app shows it in the headline strip, the Guided Results card, Emissions and the Issues panel's failure banner. The assistant has no read tool for it, so in chat it cannot say whether the limit was met, what it cost, or why a solve was refused.

## Acceptance criteria

- A chat read tool returns the same result and refusal as the route.
- `not_via_chat.d/claude-dc-co2-cap-goal.json`'s "unbuilt" entry and `assistant_route_parity_declarations.d/claude-dc-co2-limit-ui.json`'s held-by-spec row are removed with it.

## Likely files

`pypsa-gui/backend/harness/` (read the README first), `routers/results.py` (`co2_limit`).

## Comments
