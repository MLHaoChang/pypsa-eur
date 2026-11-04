<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-18 — Link the assumed-X/R readiness rows to the PoC X/R field

Status: done (landed in #1050)
Type: task
Blocked by: AS-17b (it edits the same backend module)

Spec: [spec.md](../spec.md). Follow-up of AS-17c (PoC X/R field on Project info → Settings, #1048); UX (7) condition 4.

## Scope

The grid-readiness RVC and flicker rows say "enter the network operator's X/R" (`pypsa-gui/backend/.../grid_readiness.py:523`) but not where. The field lives in Project info → Settings ("PoC X/R").

- Add a "Set PoC X/R >" link from the assumed-X/R rows to that field.
- It goes through `withReturn`, uses request-then-scroll to the field (not a bare `setSlidePanel`), and opens in place in Guided (`openFlowTargetInPlace`).
- Reword the row text to name the place in plain words ("in Project info, Settings").

## Done when

- The link lands on the focused PoC X/R field and returns to the readiness view; Guided and Expert agree.
- Test in the scorecard test file; UX (7) OK with shots.
