# Expert Results: the study verdict, expected shortfall and "Why this number" (slice 02c)

Status: ready-for-agent
Type: task
Spec: `.scratch/guided-results/spec.md` §1.4; issue 02 (owner decision WHY-FULL-1, "the same component goes on
Expert's Results headline"); EXPERT-GUIDED-1.
Blocked by: 02b (the `WhyThisNumber` disclosure); control-strategy PR C, which edits `SummaryTab.tsx`.

Gated: GK B, UX.

## What to build

Expert never shows less than Guided (UX 23 condition 3, #1193). After 02b, Guided's headline explains its study figures
and Expert's does not. Also, for the same project Expert's Summary reads "not run yet" while Guided reads "Certified":
Expert's Summary has no study verdict or expected-shortfall figure, and only links to Adequacy (keyed walkthrough audit
J6, `origin/claude/ux-walkthrough-2026-11:.scratch/ux-walkthrough-2026-11/issues/01-guided-expert-result-mismatch.md`).

- Expert's Results Summary shows the study's verdict and expected shortfall, from the same reads and with the same
  freshness words as Guided's headline.
- The same `WhyThisNumber` component sits under those figures and under the design cost. In Expert, Edit opens the
  input in Expert, and Back returns to Results.

## Done when

- For one project, Guided and Expert show the same verdict, the same expected shortfall and the same freshness words;
  a test pins each against the same fixture.
- Expert's figures carry "Why this number", the same component as Guided's, with its tests.
- UX shots: Expert Summary for a DC and a microgrid project, dark and light, with the disclosure open.
