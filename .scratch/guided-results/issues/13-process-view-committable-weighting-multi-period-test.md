<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 `process_view` committable-branch weighting has no multi-period test

Status: needs-triage
Source: #1939 review (GK nit)

## What

The committable branch of `process_view` weights its figures by the snapshot weights, and no test runs it on a multi-period network. A weighting slip there would pass every current test.

## Where

`pypsa-gui/backend/services/results/process_view.py` (the `committable` branch, around line 243). Add a test beside the existing process-view tests, using a two-period network with unequal period weights.
