<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 41 — Off-grid scorecard still judges ramp, oscillation and peak rows against the connection

Status: needs-triage
Type: task
Found by: GK A's review of issue 12, slice 12a (#1361)
Owner decisions: none yet

## What was seen

On an off-grid project the grid-readiness scorecard now leaves out the SCR row,
the fault-level rows and the two voltage-stability rows. `ramp_rate`,
`oscillation` and the `energization_peak` rows stay, and they are judged
against the grid connection, which an island with its tie open does not use.

## To decide

Whether these rows are excluded on off-grid with a reason, or judged against
the island's own limits instead.

## Comments
