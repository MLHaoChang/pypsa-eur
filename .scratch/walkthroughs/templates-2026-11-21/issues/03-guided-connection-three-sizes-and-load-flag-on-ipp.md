<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Guided Connection step shows four different connection sizes and a load flag on the IPP

Status: needs-triage
Impact: medium
Templates: IPP
Found by: template walk, 2026-11-21 (master 6b421b06)
Shot: ../shots/ipp-g3.png

## What

IPP, Guided step 3 Connection & grid, after switching Grid connection to "Weak or limited" in Expert (Guided shows the same choice, so that edit round-trips). The step shows:

- Sizes: the template text says a 70 MW connection; the step says "Connection point: Grid import (50 MW)"; the header says "Grid export, Grid import, 20 MW"; the Expert canvas and Results › Key capacities say "grid 500 MW".
- "Must stay on: none tagged", flagged "!" with "Fix with the assistant", on a generation-only site that has no load.
- "Grid readiness: 0 things to look at, 20 not yet known".

The same step prints `eh_sk_mva`, `eh_poc` and "SCR gate" (engine names): added to `template-e2e` 05 instead of here.

## Why it matters

The user cannot tell which of 70, 50, 20 or 500 MW the study used, and is asked to fix a load that does not exist.

## Suggested fix

Show one connection size (the limit the run used) with its source; do not flag "must stay on" for a site with no load.
