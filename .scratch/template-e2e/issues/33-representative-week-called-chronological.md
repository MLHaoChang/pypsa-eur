<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 33 Guided section 1 calls a representative-week horizon "a chronological year"

Status: done (#1808)
Landed in #1808.
Blocked by: none
Source: gap review 2026-11-21 (`.scratch/gap-review-2026-11-21/README.md`), walk W1.
Severity: misleads (small)
Model: sonnet. Review: UX.

## Problem

On the DC staged template, one section-1 card says "Each period is the same representative July week" and "Representative weeks: no, a chronological year". `pages/hubDesign/ExpertOnlyLines.tsx` (~91, `hasGaps()`) treats one contiguous week per period as chronological.

## Acceptance criteria

- [ ] The representative-weeks line reads the horizon's own type (snapshot weightings or the template's horizon kind), not gaps in the snapshot index. One contiguous representative week per period reads "Representative weeks: yes, 1 week per period, weighted to a year".
- [ ] A vitest for a chronological year, one representative week, and several weeks.
- [ ] End to end in the real app: DC staged › section 1 shows "Representative weeks: yes"; the IEEE 14 template (chronological) shows "no".

## Likely files

`pypsa-gui/frontend/src/pages/hubDesign/ExpertOnlyLines.tsx`.

## Comments
