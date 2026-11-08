<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# DC-07 S3 test nit: the reference-before-window ordering test passes with no windows

Status: needs-triage
Type: bug (test)
Found by: DC-07 S3 review (#1255, merged a6b4b3e4), follow-up noted by the coordinator.

## Problem

In `pypsa-gui/backend/tests/test_operational_gap.py::test_no_window_is_current_during_the_reference_solve`, `assert all(w is not None and w["network"] is n for _, w in wins)` and the index check both pass when `wins` is empty, so a run that solved no window at all would pass the ordering test.

## What done looks like

The test also asserts the number of windows it expects (`assert len(wins) == <expected>`, or at least `assert wins`), and a mutant that skips the windows fails it.

## Comments
