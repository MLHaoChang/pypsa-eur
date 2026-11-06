<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GS-04: non-spinning gensets get no I7 cut coefficient

Status: needs-triage
Type: bug
Found by: GS-04 (`04-e1-genset-credit.md`); owner list DOCS-1.

## Problem

A genset that is not spinning (u = 0) contributes no coefficient to the I7 frequency cut at its hour, while a spinning one does. State which is intended, and if the credit should exist, add it. Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/services/solver/island.py`, the I7 cut rows (`#: The I7 cut kinds`, ~line 929 and 939).
