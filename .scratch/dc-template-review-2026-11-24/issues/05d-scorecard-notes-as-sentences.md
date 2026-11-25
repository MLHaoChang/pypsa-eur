<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05d Scorecard row notes read as one clause chain, not sentences

Status: needs-triage
Blocked by: none
Source: UX review of the DC 05 tail (load wording by workload kind).
Severity: minor

## Problem

`services/adequacy/grid_readiness.py` builds each row's `note` as clause
fragments joined with "; " (e.g. `f"{note}; {CONN_LABEL_ONLY}"`), and no note
ends with a full stop. A long note reads as one run-on chain.

## Wanted

The scorecard renders a row's note as separate sentences, each ending in a
full stop, for every template and workload kind at once (not per kind, so the
AI-training wording does not drift from the rest). Check the report chapter
and the copilot, which read the same notes, and any evidence hash that moves.
