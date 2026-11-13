<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Workbook view drops all-empty columns; the horizon refusal has no error kind of its own

Status: done (this PR; PR number pending)
Type: follow-up
Gated: GK B

Follow-up of 08. Confirmed real on master 21d44207.

- `results_workbook/views_network.py` `_rows` kept only numeric-dtype columns, so a column with no values (all None, object dtype) vanished. It now stays, as empty cells. Test: `test_rows_keeps_a_column_with_no_values`.
- `harness/experiment_comparison.py` refused an over-cap horizon as `invalid_experiment`, the same kind as malformed plan data, so the assistant could not tell them apart. New kind `horizon_too_large_to_compare` (fragment `tool-error-kinds.d/claude-followup-bundle-1.json`).
