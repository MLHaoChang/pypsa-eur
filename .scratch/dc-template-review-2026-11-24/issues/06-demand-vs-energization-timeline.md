<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 Demand vs energization over time: one chart, Guided and Expert

Status: needs-triage
Blocked by: none
Source: DC template review 2026-11-24 (`../README.md`). Stage 4.
Severity: major
Model: sonnet. Review: UX + GK B.

## Problem

The pieces exist but never meet in one view:

- the energization schedule is edited as rows per period (`components/gridReadiness/EnergizationRows.tsx`, `PUT /{name}/flow/energization`, `services/adequacy/energization.py`) and listed as text in the run monitor ("2027: … MW energized", `components/runMonitor/EnvelopeSection.tsx:84`);
- time to power is one headline number (`services/dc_time_to_power.py`, `GET /results/headline_kpis`), counting grid-delivered power only (its docstring);
- the staged load per period is a table (`pages/results/SizedDesignTable.tsx:95-119`).

No chart shows, per period (and inside a period, per hour), the DC demand against the energized grid import, plus the on-site firm capacity (gensets, BESS) as it comes online, with the gap that is shed or curtailed. Nothing updates when an input changes before a run: the rows refresh on save only.

## User-visible impact

The question "when can each data hall be powered, and by what" needs three screens and mental arithmetic.

## Acceptance criteria

- [ ] A stacked chart per period: energized grid import, on-site firm capacity by kind, against peak facility demand; the time-to-power date marked; shortfall shaded.
- [ ] Before a run it draws from inputs (spec phases × PUE, energization rows, committed assets) and redraws on each saved edit; after a run it overlays the dispatch (served, shed, curtailed) and says which basis it shows.
- [ ] The same component in Guided (section 3 or 10) and Expert results (parity test).
- [ ] Words, units and the assumed marks follow `docs/agents/review-ux.md`.

## Likely files

`services/adequacy/energization.py`, `services/dc_time_to_power.py`, a read route under `routers/flow.py` or `routers/results.py`, new `frontend/src/components/gridReadiness/EnergizationTimeline.tsx`.

## Comments
