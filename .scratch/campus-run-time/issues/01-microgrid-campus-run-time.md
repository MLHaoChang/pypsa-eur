<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The microgrid's campus run takes about 55 minutes

Status: ready-for-agent
Type: task
Blocked by: None

Logged by the campus engine-gaps package (2026-10-15). The owner wants the
speed-up later; nothing here is changed yet.

## What was seen

The every-stage smoke test ran `eh_microgrid_staged` through the campus run
(`POST /api/campus-electrical/{name}/run`, default settings). It took about
55 minutes. Most of that is the `island_checks` stage
(`pypsa-gui/backend/services/campus_electrical_service.py`, `_run_island_hours`):

| Step | Call | Time |
| ---- | ---- | ---- |
| I6 frequency check | `cs.island_frequency_campus(run_dir)` | ~9 min |
| I7 cuts | `cs.cuts_campus(run_dir)` | ~29 min |

The rest (prepare, rank, I4/I5 static checks, size, invest) shares the
remaining ~17 minutes.

## What to do

1. Profile `cuts_campus` (`gridspine/drivers/campus_study.py`) and
   `island_frequency_campus` on the microgrid's run directory, and name where
   the time goes (per hour, per scenario, per solve).
2. Propose the speed-up (fewer re-solves, reuse of the I6 results in I7,
   parallel hours, a cheaper first screen) with its expected gain, and bring
   it to the owner before changing results.
3. Keep every check's result identical on the microgrid, or say which change
   moves it and why.

## Done

The microgrid's campus run finishes well under its present ~55 minutes, with
the same compliance and island results, and a test pins the faster path.
