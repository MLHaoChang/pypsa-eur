# UX reviewer: standing rules

What the UX reviewer applies to every UI PR, on top of the workflow design (`.scratch/project-workflow/design.md`). A successor reads this file instead of a hand-off note's rule list. To add a rule, send it to the coordinator, which lands it here in the next docs PR.

Post each verdict as a PR comment, with the attribution footer: `UX OK @<full sha>: <what was checked>` or `UX NOT OK @<sha>: <what to fix>` (owner decision VERDICT-PR). Plan verdicts go straight to the builder (its session id is in the plan message), because there is no PR yet. The verdict must be the first line of the comment; the attribution footer goes last. The PR comment is the verdict. After a NOT OK, also `send_message` its asks to the builder named on the PR body's `Session: <id>` line; a missing line goes to the coordinator. Message the coordinator only for a NO-GO the builder disputes, a NOT OK that blocks a merge, or a standing ruling to record. A clean master merge (its tree equals `git merge-tree --write-tree <old-head> <master>`) carries the OK: reviewers skip it, and builders don't ask for it.

## Review setup

- Read a PR with `git diff --stat` first, then one hunk or file at a time, each capped with `| head -c 6000`. Whole-file or cross-file reads go to an Explore subagent that returns file:line findings only.
- Open screenshots downscaled: resize to at most 1000 px wide before `Read` (13 full PNGs cost ~15k tokens).

## Navigation

- Every jump in `layout/flowNavigation.ts` that leaves the user's view goes through `withReturn(...)`, so "Back to <origin>" is set. In Guided, Back restores the origin slide panel (`o.panel ?? 'hubDesign'`).
- Panel links from Results or tabs use a mode-aware opener: `openFlowTargetInPlace` in Guided, `openFlowTarget` in Expert (`pages/results/openPanel.ts`). A bare `openFlowTarget` switches Guided to Expert.
- A link to a block deep in a panel uses request-then-scroll (`requestSolverBlock`, `requestGridReadiness`), never a bare `setSlidePanel`. A whole-panel gate in `setSlidePanel` is fine. In Guided it targets the block's Guided home (Grid readiness scorecard: Site card, section `connection`).
- Time Series' Guided home is the field drawer over the section-6 card (what `openTimeSeriesFor`'s Guided branch opens), never the full-screen `timeseries` panel.

## Wording and layout

- Separators: "›" (U+203A) only for links that go somewhere; "·" separates data; a colon in sentences. Disabled reasons are visible text.
- One entry point per feature, one bulk action, no duplicate buttons (owner decision UX-flow).
- A UI PR needs screenshots of each state, in Guided and Expert.
  - Waived for a text-only change inside an existing banner or layout that already has shots in both modes (#894, #929, #971).
  - For text swaps, a shot harness that mocks the component is acceptable (#917, #928).
- No Python-repr quotes (`{x!r}`, `Line 'L1'`) in user-visible text, refusals and chat tool results included: plain words, or curly quotes “…”.
- A colon, not an em dash, joins clauses in a sentence, refusals included.
- Freshness wording is "out of date" (as `ResultState` says), never "stale".
- Card labels reuse the owning panel's own word map (for example `FIELD_WORDS`, `CARD_GROUPS`), with a parity test that fails when the two drift.
- Guided edit cards cap the headline at 4 lines (`HEADLINE_CAP`, #784), and show all lines when the cap would hide only one.
- A background run never jumps to another panel on its own when it finishes; offer a "›" link through `withReturnFrom` instead.
- A refusal message names only actions its real caller can take. When the GUI never shows it, the chat tool's own arguments decide what it may name (#951).
- Activity (POLISH-PW-2): show the "Outcome: <word>" line only when the native state adds something to the plain status.
- The FMEA criticality label is "Criticality (€ per year)" (POLISH-PW-2).
- The turn check mark is muted, is cleared when the turn ends, and shares `ANSWER_NOT_CHECKED` (#946).

## Master merges

A master merge carries an OK when the head tree equals `git merge-tree --write-tree <old> <master>`. Any difference is the conflict resolution and needs a real look. `scripts/coord/premerge.sh` prints whether an OK carries.
