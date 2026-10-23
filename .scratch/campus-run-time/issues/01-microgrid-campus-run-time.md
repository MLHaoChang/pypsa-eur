<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The microgrid's campus run takes about 55 minutes

Status: done (measured 2026-10-23; no code change)
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

## Measured 2026-10-23

Measured on master 30b313be in a 4-CPU cloud container: `eh_microgrid_staged`,
the campus run's default settings (k=3, invest on), 35 island critical hours.
The 55 minutes no longer holds. The issue predates the island-speed change
(`doc/release-notes.d/claude-campus-island-speed.md`: the simulation about 2x
faster, the hours and the cuts' events over worker processes) and the
seamless event rows of #332 (fewer failing events for I7 to re-run).

| Run | Total | I6 frequency | I7 cuts |
| --- | ----- | ------------ | ------- |
| Serial, 1 worker (the packaged desktop app; the backend tests set `GRIDSPINE_WORKERS=1`) | 428 s (~7 min) | ~185 s | 208 s |
| Default 3 workers (`parallel.workers()` on 4 CPUs) | ~3 min | 70 s | 76 s |

The rest of the serial run: prepare and rank 2 s, I4/I5 static checks 17 s,
size and invest 14 s.

Where the time goes (serial):

- **One simulation** (`frequency.simulate`) costs about 0.5–0.6 s: 20,001
  fixed RK4 steps of pure Python (20 s at 1 ms), spread over the per-step
  `balance`, `targets`, `deriv` and `add` calls, with no single hot spot.
- **I6**, 173 s for the hours: about 5 s per hour, evenly spread (the slowest
  hour 6 s, the median 4.8 s). 342 simulations: E1 and E2 run 4 times per
  hour each (the event and the 3 i_max sensitivity reruns, Q8), so the
  sensitivity reruns are about 60 % of I6; E3 35 and E5 27 simulations.
- **I7**, 190 s: 23 failing events (E5 104 s, E3 75 s, E2 11 s; no failing
  hour in the every-hour E1 sweep), about 13 simulations per event, almost
  all of them the inertia bisection (`_bisect`, rel 1e-3, about 11 steps).
  The settle-time doubling rarely fires. The first re-run of each event
  repeats I6's run (23 of 307 simulations).

### Options not taken, 2026-10-23

The owner chose to close the issue as met. These were offered and can be
revisited:

1. **A faster simulator loop, identical to the bit.** Rewrite
   `frequency.simulate`'s inner step on flat state, the same float operations
   in the same order. Expected 1.5–2x on I6 and I7: serial ~7 → ~4 min,
   parallel ~3 → ~2 min. Every check result stays identical; a regression test
   on the I6 and I7 tables pins it.
2. **A cheaper inertia search in I7.** Start the bisection from an estimated
   bracket and stop at 1 % instead of 0.1 %. Expected I7 −40 % (serial 208 →
   ~125 s). The inertia cuts' `required` and `delta` move by up to 1 %, on the
   passing side; droop, headroom, feasibility and the failing hours stay.
3. **A 5 ms step for I7's re-runs only.** Expected I7 ~4–5x faster (serial
   208 → ~50 s). The inertia cut values move by the integration difference
   (not measured, likely under 1 %); an event at the pass/fail edge could read
   "not reproduced".
