<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 A component edit discards the last run (not_run) instead of marking it stale

Status: ready-for-agent
Blocked by: none. Likely a regression of 04 (done; D1(a): "a value edit … reads stale").
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W2 (shots `s29-stale-expert`, `s29-inv-after-edit`).
Model: sonnet (diagnose first: `diagnosing-bugs` skill). Review: GK B.

## Problem

On the IPP template after a run, one Expert edit (`bess` `p_nom_max` 50 → 20 via `PUT /network/storage_units/bess`, the call `updateStorageUnit` makes) turns the flow's results freshness from `fresh` to `not_run`, not `stale`. Expert Results then shows "No results yet: run the model", and Run LOPF has no "(n out of date)". The investment case on the same project keeps its figures with "Out of date: dispatch changed…", so the two views disagree about one run.

## User-visible impact

After any edit the user loses sight of the last results until they re-run, and two screens contradict each other.

## Acceptance criteria

- [ ] A failing test first: run, edit one component value through the route, then the flow's results freshness reads `stale` with the edited part named. The last run's figures stay readable and marked out of date.
- [ ] Every component PUT route (generators, loads, lines, links, storage units, stores) behaves the same; the test is parametrised over them.
- [ ] Expert Results shows the stale figures with an "Out of date" band and Run LOPF "(1 out of date)", matching the investment case.
- [ ] End to end in the real app: IPP template › run › Expert › edit the battery's max power in the table › Results still shows the run, marked out of date, and so does the Investment case. Re-run → both fresh.

## Likely files

`pypsa-gui/backend/routers/network.py` (component PUT routes), `pypsa-gui/backend/services/provenance_graph/`, `pypsa-gui/backend/services/flow_service.py` (results freshness), `pypsa-gui/frontend/src/hooks/useStaleRunKinds*`, the Results Summary empty state.

## Comments
