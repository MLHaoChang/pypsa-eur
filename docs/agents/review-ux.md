# UX reviewer: standing rules

What the UX reviewer applies to every UI PR, on top of the workflow design (`.scratch/project-workflow/design.md`). A successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `UX OK @<full sha>: <what was checked>` or `UX NOT OK @<sha>: <what to fix>` (owner decision VERDICT-PR). Plan reviews go to the coordinator, because there is no PR yet.

## Navigation

- Every jump in `layout/flowNavigation.ts` that leaves the user's view goes through `withReturn(...)`, so "Back to <origin>" is set. In Guided, Back restores the origin slide panel (`o.panel ?? 'hubDesign'`).
- Panel links from Results or tabs use a mode-aware opener: `openFlowTargetInPlace` in Guided, `openFlowTarget` in Expert (`pages/results/openPanel.ts`). A bare `openFlowTarget` switches Guided to Expert.
- A link to a block deep in a panel uses request-then-scroll (`requestSolverBlock`, `requestGridReadiness`), never a bare `setSlidePanel`. A whole-panel gate in `setSlidePanel` is fine. In Guided it targets the block's Guided home (Grid readiness scorecard: Site card, section `connection`).
- Time Series' Guided home is the field drawer over the section-6 card (what `openTimeSeriesFor`'s Guided branch opens), never the full-screen `timeseries` panel.

## Wording and layout

- Separators: "›" (U+203A) only for links that go somewhere; "·" separates data; a colon in sentences. Disabled reasons are visible text.
- One entry point per feature, one bulk action, no duplicate buttons (owner decision UX-flow).
- A UI PR needs screenshots of each state, in Guided and Expert.

## Master merges

A master merge carries an OK when the head tree equals `git merge-tree --write-tree <old> <master>`. Any difference is the conflict resolution and needs a real look. `scripts/coord/premerge.sh` prints whether an OK carries.
