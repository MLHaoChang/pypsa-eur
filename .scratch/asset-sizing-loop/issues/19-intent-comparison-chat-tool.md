<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-19 — Design intent comparison from chat

Status: wontfix
Type: task
Blocked by: none
Reviewer: GK B

Spec: [spec.md](../spec.md). Split from [AS-10](10-preference-comparison-ui.md), which landed (#1009, #1017, #1024, #1041) with its chat disposition held "until the owner decides on a chat tool".

## What

Decide whether the assistant gets a tool for the design intent comparison, and build it if so. Until then the comparison runs only from the Intent comparison section of the Grid study tab, and chat says so (`services/not_via_chat.py` row `intent_comparison`, disposition `unbuilt`).

Routes this issue holds (route-parity `held-by-spec`):

- `GET /api/projects/{name}/intent-comparison`
- `POST /api/projects/{name}/intent-comparison`
- `POST /api/projects/{name}/intent-comparison/abort`

## Owner question

Should chat start, watch and abort an intent comparison (two to five sizing loops), or stay a pointer to the Grid study tab?

## Comments

- 2026-11-04: Owner decision AS19-POINTER-1 ("Pointer only"): chat explains and links to the Grid study tab for a design intent comparison; it does not start, watch or abort one. Chat control is won't-do. The three routes are declared `ui-pointer` (decision `AS19-POINTER-1`) in `smoke/assistant_route_parity_declarations.d/claude-as-10-ui.json`, so closing this issue no longer fails route-parity.
