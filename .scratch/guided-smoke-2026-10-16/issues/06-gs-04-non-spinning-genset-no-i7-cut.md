<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GS-04: non-spinning gensets get no I7 cut coefficient

Status: needs-info
Type: bug
Found by: GS-04 (`04-e1-genset-credit.md`); owner list DOCS-1.

## Problem

A genset that is not spinning (u = 0) contributes no coefficient to the I7 frequency cut at its hour, while a spinning one does. State which is intended, and if the credit should exist, add it. Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/services/solver/island.py`, the I7 cut rows (`#: The I7 cut kinds`, ~line 929 and 939).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (GK A): GK A (engine owner): is it intended that a non-spinning genset (u = 0) gets no coefficient in the I7 frequency cut at its hour while a spinning one does? If a credit should exist, we build it in `services/solver/island.py` (~929, ~939): M, opus, GK A; if intended, we close as wontfix and document it.
- Blocked by: the answer.
