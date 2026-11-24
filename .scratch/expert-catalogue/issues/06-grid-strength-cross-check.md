<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Slack grid strength: minimum never above maximum

Status: done
Type: task
Gated: yes (GK B)
Depends on: DGS-EXTRAS-1 (claude/dgs-extras-fields); lands with 01a-3 (ranges and one validator in every write path).

## What

A slack Generator's `sk_min_mva` must not exceed `sk_max_mva`. This is a cross-field check, so the single-field type checks added in DGS-EXTRAS-1 can't make it (GK B X8). `rx_min` and `rx_max` are the R/X at the weakest and strongest grid, not a pair with an order, so they get no such check.

## Acceptance

- Every write path refuses an edit that leaves `sk_min_mva > sk_max_mva` on one generator, with a typed 422: "Minimum short-circuit power: it is above the maximum short-circuit power (… MVA)." This covers POST, PUT, bulk/preview, the chat edit tool and experiment copy.
- An edit that sets only one of the two is checked against the stored other. A blank on either side skips the check.
- Imports (DGS, legacy, PSS/E) load a reversed pair and flag it in readiness; they never refuse it or swap the values (C9).
- One test per path, and one mutant per path that skips the check, killed by that path's test (C12).

## Comments

### Build notes (GK B4 plan GO, conditions)

- One guard, `check_grid_strength_pair` (`services/adequacy/eh_columns.py`), called from `_create_component`, `_update_component` (merged row) and `apply_bulk_update` (each target's own merged row, all before the first write). Chat edit/bulk tools and the edit-card preview reach those same functions; each has a test. It fires only when the write names `sk_min_mva` or `sk_max_mva`.
- The stated limit is the stored maximum at full precision (never rounded above it); an infinite stored minimum counts as reversed. The three write paths refuse inf before storage.
- Exempt writers (they can load a reversed pair; the readiness flag `grid_strength_pair:<generator>` names the generator and both values): the Excel workbook import and the DGS/PSS-E/PLEXOS model import (external gridspine readers, no repo-side generator cell writes), legacy import (no Generator column writes found). Library apply, project templates and run-profile levers write no `sk_*`; run-profile levers reach the guard through `update_component`, and `sk_min_mva` is in `NEVER_TUNABLE`. Experiment copy edits only `workflow_tools._ATTRIBUTES`, which has neither column (test pins it).
- Readers (`grid_readiness`, `sizing_loop_runner`, `run_gates_concentrator`, `sizing_cuts`) take the minimum S_k'' from the campus description via `_poc_sk_min`, never from the generator's columns; with none, they do not evaluate. A test pins `_poc_sk_min` against a reversed pair.
