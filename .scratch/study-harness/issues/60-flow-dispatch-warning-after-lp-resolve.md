<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 60 — "Dispatch saved from load flow" can show after an LP-only re-solve

Status: needs-triage
Gated: GK
Type: bug

## What is wrong

`pypsa-gui/backend/routers/projects.py` (the disclosure before the "Dispatch saved from load flow" warning) treats a non-empty `buses_t.v_mag_pu` as proof that the saved tables come from a flow. The LP never writes |V|, so after a legacy project that ran a flow is re-solved with the LP only, the stale |V| stays and the warning claims the dispatch came from the load flow when it is the LP's.

Raised by GK A as an optional point in #1616's review; not blocking there.

## Acceptance

- A project whose last save followed an LP-only solve does not show the warning, even when it still carries |V| from an earlier flow.
- A project saved from a flow (standalone or chained) still shows it once.
- A test covers the LP-only re-solve of a legacy flowed project.

## Journey fit

Project open (load) in Guided and Expert. Hand-off: run → save → load, through the saved solver condition.
