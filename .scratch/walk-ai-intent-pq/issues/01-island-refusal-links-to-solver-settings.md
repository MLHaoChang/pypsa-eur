<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — the intent comparison's island-limits refusal links to the setting

Status: done (#1756)
Landed in #1756.
Type: task
Gated: no (UI)

## What

On the Data Center Energy Hub template, Expert > Grid study > Intent comparison refuses with
"Island limits in the optimisation: on for this project, and the intent comparison cannot include them yet." (#1698).
The refusal names the setting in words only. The setting lives in another panel (Solver settings), so the fix action
on the refusal's own line needs a cross-panel link: request-then-scroll to the island-limits setting, with a Back pill
to the comparison, as the "Set grid code ›" link in the same section does for the grid code (review-ux.md: the fix
action sits on the refusal's own line).

## Why not in #1698

UX 2 (3) on #1698: same-form fields link in that PR; a refusal whose field lives in another panel links with
request-then-scroll, or words only plus a filed issue when that is more than a small change. There is no scroll
request for the island-limits setting yet.

## Acceptance

- The refusal line carries "Island limits ›", which opens Solver settings and scrolls to and focuses the island-limits
  setting; a Back pill returns to the comparison.
- A component test covers the link; a shot in light and dark.
- Moot once the sizing loop includes island rows (GK A follow-up from walkthrough issue 05); then the refusal goes away.

## Triage (2026-11-19, master 9f850d36)

Still true: `backend/routers/compare.py` returns the refusal text; `IntentComparisonSection.tsx` has only the 'Set grid code ›' link. UX reviewer needed. Blocked by: none.
