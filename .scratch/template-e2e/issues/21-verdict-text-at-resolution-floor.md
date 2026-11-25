<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 21 — Guided verdict says 'straddles the goal' when the run cannot resolve it

Status: ready-for-agent; audit 2026-11-25: #1927 merged (verdict states the resolution floor and minimum runs; runs below the floor refused at start); left: goal parity on the concentrator rail (issue 41)
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: results. The verdict drives the go/no-go decision.

## What

With 'Simulation runs per check' = 1 (allowed, minimum) the verdict reads 'Not decided: the shortfall estimate (0.0–0.0 h/yr) straddles the 3 h/yr goal: more simulation runs would settle it' (DC, microgrid, hospital, staged DC, staged microgrid). Expert and the report give the real reason: 3 h is below the 52.1 h resolution floor at 1 draw. Improve then offers '2 runs (was 1)', which cannot fix a 52 h floor. Nothing stops runs = 1. Goal panel shows 'No goal yet' while the rail shows '3 h/yr (assumed)'; the hospital reference design says 'allowed shortfall not set' while the verdict uses 3 h/yr. With no goal (H2, ammonia) the text sends the user to 'step 3 (Goal)' but the goal is in section 8 (see 13).

## Acceptance

- The verdict states the resolution floor and the minimum runs needed.
- Runs below the floor are refused or flagged before the run.
- Goal reads the same value in rail, panel, verdict and reference design.

## Triage (2026-11-20, master 8a039460)

Checked by code reading only; the app was not re-run. Still true: the 'straddles the' text is still in `eh_study.py` and `headline.ts`. Goal display was partly fixed by #1734 (assumed-goal note). Acceptance as written above; re-check the goal-parity item after #1734. Gated: GK A. Blocked by: none.

## Audit (2026-11-25)

- 2026-11-25 audit: #1927 in master log; floor and min-runs text in eh_study.py:376-490 and headline.ts:55-70; rail parity open as issue 41.
