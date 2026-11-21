<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 29 The CI template journeys stop at the report download: add the workbook export and an Expert→Guided parity step

Status: done
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), coverage map.
Owner decisions: E2E-TEMPLATES-1, HOLISTIC-1.
Model: sonnet. Review: GK B.

## Problem

`frontend/scripts/template-journeys.mjs`, run by the `gui-template-journeys` CI job (`--mode short` on PRs), is the only CI browser check that goes template → sections → run → results → report. It:
- stops at "Download report" (~line 273) and checks that the file is a `.docx`, not what it contains;
- never exports the results workbook (`.xlsx`) or the project bundle;
- says in its own header that it skips "an edit made in Expert and read in Guided", the core Guided/Expert agreement of design.md §3.5.

## User-visible impact

A regression in the workbook export, the report's content, or the Guided/Expert agreement reaches master with CI green.

## Acceptance criteria

- [ ] After the report step, the short mode downloads the results workbook from the latest run. It checks that the file opens (zip + `[Content_Types].xml`), has the About sheet, and that its headline figure equals the Guided results headline, within its stated rounding.
- [ ] The report `.docx` check reads `word/document.xml` and finds the project name and the run's headline figure.
- [ ] One step per journey edits a value in Expert (the first asset's capacity or the connection limit, whichever the template has), returns to Guided and reads the same value in its section. It then reverts the edit and reads the original back.
- [ ] The added steps add at most 60 s per journey in short mode (state the measured time in the PR). Steps a template cannot do are xfail with a reason, as the suite does today.
- [ ] End to end: the suite runs green in `gui-template-journeys` on the PR. A deliberate break (the workbook route returning 500, on a scratch branch) turns it red; quote the run in the PR body.

## Likely files

`pypsa-gui/frontend/scripts/template-journeys.mjs`, `pypsa-gui/frontend/scripts/template-journeys-lib.mjs`.

## Comments
