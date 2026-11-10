<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flaky 2 s timing assert in `test_v1_value_flows_payload[multi]`

Status: done (#1337)
Type: bug (test)
Found by: coordinator, CI on #1257 (GUI backend shard 3/4, 2026-11-07), a PR that does not touch the file.

## Problem

`pypsa-gui/backend/tests/test_results_value_flows.py::test_v1_value_flows_payload[multi]` ends with `assert elapsed < 2.0, elapsed` (WP3.4: no cache unless the GET measures over 2 s on the P1 fixture). On a loaded CI runner it measured 2.199 s and failed a PR whose files it does not cover.

## What done looks like

The WP3.4 budget is still checked, but a shared CI runner's load cannot fail it on its own: for example, measure the best of a few GETs, or move the wall-clock budget to a marked perf check, with the reason written in the test. Do not delete the assert or raise the bound without saying why (steward CI rules).

## Comments
