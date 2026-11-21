<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 2b: Runs indicator and the run's end in the strip

Status: ready-for-agent
Type: task
Blocked by: 02a (stacked on PR #1627)

Spec: [../spec.md](../spec.md) §4, "package also owns the lasting header Open
results link" (audit #25); UX plan GO conditions 1, 2, 3, 10.

## What

- `components/runStatus/RunsIndicator.tsx` in the header: "N runs active"
  (count in words, not colour alone) across the projects you can see, plus the
  Guided goal study. Expert opens the Solve queue; Guided opens a list in place.
- `hooks/useRunOutcome.ts` + `components/runStatus/RunOutcomeLine.tsx`: when a
  run of the project on screen that this tab saw going on ends, the strip keeps
  one line until dismissed, the next run starts or the project changes:
  - finished: "Optimisation finished · 6 min" + **Open results**
    (`openResults`: Guided `withReturnFrom` + `openSectionInGuided('results')`,
    the section-10 card `hub-card-results`; Expert `openFlowTarget` on section
    10). Never jumps by itself.
  - failed / interrupted: **See why**; Guided shows the run's own reason in
    place, Expert opens the Solve queue (the row holds error and log).
  - stopped by the user: the line alone.
- Guided uses `openSectionInGuided` inside `withReturnFrom`, not
  `openFlowTargetInPlace`: with no component and no panel the latter opens the
  section in Expert, which would switch mode.

## Not here

- Toasts, browser notification, missed-while-away: slice 3.
- Abort for the #1620 Guided "Run the optimisation" card and its failure next
  step: a later slice, once #1620 is on master.

## Comments
