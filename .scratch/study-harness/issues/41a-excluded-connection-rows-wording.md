<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 41a — Excluded connection rows are named, with their reason, in the UI, the report and the checked source

Status: done
Size: S
Blocked by: 41
Type: task
Found by: PR #1516 (issue 41 follow-ups)

## What was seen

On an off-grid project whose tie is open, issue 41 leaves the ramp, oscillation
and energization-peak rows out of the scorecard (`excluded`, each with a reason).

1. `ConnectionLimits.tsx` then said "No grid connection is tagged on this project."
   although the connection exists.
2. The report's grid-readiness chapter and the checked-results source did not
   mention the excluded rows.

## Done

1. `ConnectionLimits.tsx` lists the excluded connection rows with their reason; the
   "not tagged" text stays only when nothing is excluded.
2. The report chapter disclosures and the checked-results notes carry one line
   naming every excluded row and its reason (only when there are some, so other
   reports keep their evidence hash).
3. Tests: `ConnectionLimits.test.tsx`, `test_grid_readiness_assurance.py`.
Shots: shots/41a/excluded-{expert,guided}.png (component harness, mocked scorecard).
