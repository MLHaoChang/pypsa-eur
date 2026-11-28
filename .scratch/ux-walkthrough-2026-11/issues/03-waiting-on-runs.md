<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Waiting on a run: header says Idle, no ETA, long runs unbounded

Status: ready-for-agent; audit 2026-11-25: #1614, #1627, #1630, #1716 merged (pill, ETA, wait); left: 409 Conflict cause, Guided-vs-Expert run wording
- 2026-11-28: 409 cause = autosave tick during a queue job or Guided study (only legacy /run paused it); fixed.
Model tier: sonnet
Size: S
Blocked by: #1627, #1630 merged
Likely files: `pypsa-gui/frontend/src/` run-state hook and the run routes; find the 409 source first (diagnosing-bugs)
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J4 Run, J5 Wait
Severity: high
Send to: run progress, ETA, notify. Duplicate check: mostly the run-progress package; only the Mining run above 25 minutes and the 409s are new

## What happens

- While Guided shows "Studying… (up to 30 calculation steps) · 0:08 elapsed" with Abort, the header pill reads "Idle" (`shots/mg-run-wait.small.png`).
- Study times seen: Island Microgrid 12 s, Data Center 33 s, Data Center staged 78 s, Industrial Hydrogen staged 69 s, Island staged 138 s, Green Ammonia staged 253 s. Mining Concentrator (26 280 snapshots) was still "Running" more than 25 minutes in, with only an elapsed counter. "up to 30 calculation steps" does not say how far along it is.
- The run survives a page reload (good): section 10 then says "Running. The study is running; its verdict, cost and risks appear here when it finishes."
- The browser console logged 409 (Conflict) responses during three of the runs; the page showed no error.
- The Guided Run study and Expert Run LOPF are two different runs; the wait text does not say which one is running.

## Done when

One run state shows in the header, Guided and Expert. A long run shows how far it is or a rough time, and the end of the wait lands on the section 10 headline.

## Journey fit

J4 to J5 to J6: the wait ends where Guided results begins.

## Comments

**2026-11-17 triage (SCALE-1).** Narrowed. The "Idle" header pill, elapsed/ETA and the end of the wait landing on the headline belong to run progress (#1614, #1627, #1630; session_01G7qCunh1ZQ2fxfbTPMJRin). What stays here: (1) the 409 Conflict responses logged during three runs with no error shown; find the cause and either remove it or say it in words; (2) the wait text does not say whether Guided "Run study" or Expert "Run LOPF" is running (check after #1630). A bound on a 25-minute-plus run is template-e2e 09, not this issue.

- 2026-11-23 status sweep: landed in #1614 (0bca65ec48); #1627 (44387a6952); #1630 (233c38fd14); #1716 (1676808a11): pill, ETA and wait. Remains: the 409 Conflict cause and Guided-vs-Expert run wording.

## Audit (2026-11-25)

- 2026-11-25 audit: #1614, #1627, #1630, #1716 are in the origin/master log; the 409 cause and the Guided-vs-Expert wording have no PR, so still open.
