<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 226 `experiments.py` reads both fingerprint forms back to back outside `one_input_walk()`

Status: needs-triage
Source: #1949 hand-off

## What

`services/experiments.py` calls `workflow_tools._network_input_fingerprint(ctx)` and then `..._(ctx, exact=True)` back to back in 4 places. Each pair walks the network inputs twice, and none sits inside `one_input_walk()` (the helper #1949 adds; it is not on master 7677384a2). Wrap each pair so the inputs are walked once. Related: 225.

## Where

`pypsa-gui/backend/services/experiments.py` lines 491-494, 530-531, 574-575, 577-578.
