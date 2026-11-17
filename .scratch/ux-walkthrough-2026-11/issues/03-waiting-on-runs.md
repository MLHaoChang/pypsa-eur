<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Waiting on a run: header says Idle, no ETA, long runs unbounded

Status: needs-triage
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
