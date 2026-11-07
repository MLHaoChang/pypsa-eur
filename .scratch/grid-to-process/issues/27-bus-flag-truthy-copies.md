<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Bus-flag `_truthy` copies still differ from `process_chain.truthy`

Status: needs-triage
Type: task
Found by: GK A review of #1242 (issue 26).

## Problem

Issue 26 unified the link flag rule in `gridspine.drivers.process_chain.truthy`. The bus-flag readers keep their own copies, with different rules ("yes" or whitespace handling differs): `services/adequacy/eh_stages.py:286` `_truthy`, `services/adequacy/grid_readiness.py:~1063` `_truthy` (bus `eh_poc`, line ~1053), the archetypes copy and `services/adequacy/scr_gate.py:73` `_truthy_poc`. Route them through `truthy`, pinned per string.

## Where to look

The four files above.
