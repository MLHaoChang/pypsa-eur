<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flaky `test_harness_engine_loops` follower test

Status: done
Type: bug (test)
Found by: Seen in CI; owner list DOCS-1.

## Problem

A follower test in the engine-loops suite fails intermittently. Root-cause it (do not skip or loosen it, per steward CI rules): likely a timing or ordering assumption between loop and follower. Details: ask the coordinator.

## Where to look

`pypsa-gui/backend/tests/test_harness_engine_loops*.py`, the follower test. Details: ask the coordinator.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/backend/tests/test_harness_engine_loops*.py` follower test.
- Blocked by: none.
- Model tier: sonnet.
- Gated: GK B.
- Size: S.
- Root-cause the timing/ordering assumption; never skip or loosen the test.
