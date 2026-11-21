<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 26 — Assumed inputs are labelled only in the report, not in Guided or Expert results

Status: ready-for-agent
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: results → report (template-e2e 08 is built elsewhere; this is the results half).

## What

The report has an 'assumed' column and the grid-readiness count of assumed limits (DC: 17 assumed, 13 unknown). Guided results show no 'Assumptions behind this result' and no 'synthetic illustrative data' banner; the missing site location is mentioned only in section 9. Reproduces on every template. The IPP report is labelled (15 assumed, 20 unknown). Depends on 08.

## Acceptance

- Guided and Expert results list the assumed inputs behind the verdict and cost, and the synthetic-data banner.
- The same list appears in the report (test per template).

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Still true: no 'Assumptions behind this result' text exists in the frontend. Acceptance as above. Reuse the assumed-input label from #1734 and the one from issue 13; do not build a third. Gated: UX. Blocked by: none.
