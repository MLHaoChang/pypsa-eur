<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 43 Journey harness follow-ups from #1815 (G5)

Status: needs-triage
Source: GK B3 (6) carry-forwards on #1815 (template-e2e G5, merged); its builder is no longer active. Non-blocking.

## What

- Declare `jszip` in `pypsa-gui/frontend/package.json` `devDependencies`: the journey harness uses it, but it is not declared there (checked on master e51d4e71).
- Re-measure `ctx.added.workbookS` (`pypsa-gui/frontend/scripts/template-journeys.mjs`, the CI-PACE timing) once template-e2e 30 is fixed: today the workbook step is xfail-keyed to 30, so the figure does not reflect a real download.
- Add a short-mode skips row, so the short run reports the steps it skips, for parity with the full run.

Suggested: Sonnet, GK B3 review.
