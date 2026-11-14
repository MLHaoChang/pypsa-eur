# Guided Results card: say when results were never checked against the inputs

Status: done
Model tier: sonnet.
Size: S.
Blocked by: 04b (the `uncompared` header count, PR on `claude/provenance-graph-04b-reserves-uncompared`).
Gated: UX.
Likely files:
- the Guided Results card (it already carries its own "Out of date" banner)
- `pypsa-gui/frontend/src/hooks/useStaleRunKinds.ts` (`useUncheckedRunKinds`, `uncheckedKindsSentence`)
Type: task

## What

04b shows "(N unchecked)" beside Run in Expert, for run kinds whose `freshness_by_kind` is `uncompared` (results that came with the project; no solve here recorded their inputs). Guided hides the header Run, so Guided users see nothing for those kinds, and a blank card reads as current.

Add an "unchecked" notice to the Guided Results card, as UX (20) asked on the 04b shots:
- the same words as the header tooltip: "<Kinds> results came with the project and were not checked against its inputs. Run to check them.";
- visible text, not only a tooltip;
- one mark per card, as in #1450.

## Acceptance

- [ ] A Guided Results card whose kind is `uncompared` shows the notice; one that is `fresh` or `stale` does not.
- [ ] `uncompared` never reads as current anywhere in Guided (vitest).
- [ ] UX OK on shots of the card, with and without the notice.
