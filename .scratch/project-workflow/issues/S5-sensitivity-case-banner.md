<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# S5 A sensitivity case has no banner and no "Back to base"

Status: needs-triage
Blocked by: `template-e2e/23` (what-if scenario cannot run or compare; in triage, #1760)
Design: `.scratch/project-workflow/integration-sensitivities.md` §5 and §6 rows S5–S7. Owner decisions: Q11–Q16.
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), design map.
Model: sonnet. Review: UX.

## Problem

`live-copilot/17` and `18` (done) let the assistant create sensitivities and compare them, and a CompareView exists. The by-hand journey of the design (a base plus up to 8 cases; while a case is open, a banner names it, with "Back to base"; compare from the banner) is not built. `template-e2e/23` found that a what-if cannot even run or be compared by hand.

## User-visible impact

A user who opens a case cannot tell they are no longer editing the base, and has no one-click way back.

## Question for the owner

1. **(Recommended) Build S5 together with template-e2e/23's fix**, one package once 23 is triaged: the banner, Back to base, and Compare from the banner.
2. S5 alone, after 23 merges.
3. Leave sensitivities to the assistant only.

## Acceptance criteria (option 1)

- [ ] With a case open, every section shows "Case: <name> (of <base>)" with "Back to base" and "Compare". Edits go to the case only (tested).
- [ ] At most 8 cases per base, with a plain refusal at the 9th.
- [ ] End to end in the real app, with the assistant off: create a case from the base, change the battery size, run, open Compare, and return to the base unchanged.

## Likely files

the scenarios panel and project header in `pypsa-gui/frontend/src/`, `pypsa-gui/frontend/src/pages/decision/CompareView*.tsx`, `pypsa-gui/backend/services/study/forks.py` (read only).

## Comments
