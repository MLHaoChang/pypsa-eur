<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 25 — Expert is not read-only during a study run and the run cannot be aborted

Status: ready-for-agent
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: run → results (RUN-READONLY-1, UX-FLOW-NEXT-1).

## What

Guided is read-only and hands off into results. In Expert during a Guided study run the header says 'Idle' or 'Checking whether the project is ready…', Run LOPF stays enabled, Capacity bounds stay editable, and there is no Abort (DC, microgrid, H2, ammonia). An Expert LOPF run does show Running, Abort and a read-only banner. Progress text is only 'Studying… (up to 30 calculation steps) · 0:03 elapsed', no step name or ETA. Guided section 5 shows '? not known yet' during a run. The 'Open results' hand-off only works if the user stays on section 9. See 12 for the concentrator run that held the backend 75+ min.

## Acceptance

- Expert shows the run state, disables Run LOPF and editing, and offers Abort for a Guided study run.
- Progress names the step; section 5 says a run is in progress.

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Acceptance as above (RUN-READONLY-1). Shares the run-state display with issue 17 (TE2E-17-RUN-1): reuse its stage x of N line, do not build a second one. Gated: UX and GK B. Blocked by: 17 for the progress line only; the disable-and-Abort part is not blocked.
