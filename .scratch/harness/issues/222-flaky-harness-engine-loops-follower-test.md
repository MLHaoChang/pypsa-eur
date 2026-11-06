<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flaky `test_harness_engine_loops` follower test

Status: needs-triage
Type: bug (test)
Found by: Seen in CI; owner list DOCS-1.

## Problem

A follower test in the engine-loops suite fails intermittently. Root-cause it (do not skip or loosen it, per steward CI rules): likely a timing or ordering assumption between loop and follower. Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/tests/test_harness_engine_loops*.py`, the follower test. Details: ask the coordinator.
