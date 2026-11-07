<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GS-04: rename the `Island-event-genset-*` constraints

Status: ready-for-agent
Type: task
Found by: GS-04 (`04-e1-genset-credit.md`); owner list DOCS-1.

## Problem

The constraint names say "event" but the rows are shared with the islanding step. Rename to the names the user and the infeasibility explainer should see. Check `git grep "Island-event-genset"` for substring matches first (review-gk: a message rename). Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/services/solver/island.py` ~lines 1369–1370: `Island-event-genset-credit-{g}{tag}` and `Island-event-genset-room-{g}{tag}`.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/backend/services/solver/island.py` (~1369-1370).
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK A.
- Size: S.
- `git grep "Island-event-genset"` for substring users first; pick the user-facing names in the plan.
