<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-10 (LC-01C) — resolving "this", "the" and "latest"

Status: implementation in progress (Claude, branch `claude/copilot-reference-ladder`, stacked on PR #391)
Type: task
Baseline: master `d006f4beaa302b97432c65757762ae180222731a`
Dependencies: LC-01B (exact names), LC-01A (schema discovery)
Contract: LC-C11 in the [specification](../spec.md)

## Problem

The app already tells the model what the user is looking at: panel, results tab
and source, selected component, snapshot, Guided step
(`frontend/src/utils/uiContext.ts:40-80`, formatted at `harness/loop.py:1719`).
LC-C2 lists bindings but no order. So "this line", "the storage" and "the
latest calculation" can resolve differently from turn to turn.

## The ladder (first rung that gives exactly one target wins)

1. **Said explicitly**: an exact name or ID in the sentence (LC-01B resolver
   for projects; `get_component` for components; `list_project_runs` for
   runs).
2. **On screen now**: `selected_component` for "this <class>"; the open
   results tab and its source for "this chart", "these results"; the current
   snapshot for "this hour". The class must match ("this line" with a selected
   generator does not resolve).
3. **Discussed last**: the discussed run (`discuss_project_run`) and the last
   entity a tool in this conversation read or changed.
4. **Only one exists**: "the storage" when the project has exactly one
   storage component across StorageUnit and Store.
5. **Ask**: one `ask_user` Choice with the real candidates (class, name, key
   field) and "something else". Never guess.

"Latest calculation" is rung 3/4 over runs: newest completed run of the active
project; failed and running runs are named, not used.

The answer always says what was resolved ("Line L2, selected on the map").
Manual selection after the question supersedes it. UI context stays an
untrusted hint: the server re-reads the entity and checks access on every use.

## Owner decision Q4 (2026-10-19)

With a model available, every sentence goes to the copilot. The dock's local
results tour and project picker (`frontend/src/components/AssistantDock.tsx:106-133`)
run only when no key is configured. This narrows LC-01B's exact-phrase
shortcuts to the no-key path; the LC-01B owner applies it in that slice or the
next one.

## Tests

Tier A: OJ-05 (selection wins), OJ-06 (ask), a selected generator with "this
line" (ask, not mismatch), a stale selection from another project (ignored),
"latest" with a newer failed run (names it, uses the completed one).
LC-T21 in the [acceptance matrix](../test-plan.md).

## Ownership

Prompt rule in `harness/prompts/assistant_stance.md`, a small read-tier
resolver over existing reads if the prompt alone fails Tier B, and the
OJ tests. No change to `uiContext.ts`'s identifiers-only rule.

## Coordinator decision, 2026-10-19 (derived from owner decision Q2)

Build the resolver now, together with the prompt rule. Q2 makes the live-model
tier manual and never a merge gate, so only a ladder in code can be proven in
CI. Read-only and access-checked; the answer always names what it resolved.

## Implementation (2026-10-19)

- `services/reference_resolution.py`: read-tier `resolve_reference(target,
  component_class?, name?, run_id?, latest?)`. Rungs: explicit, then on screen
  (the turn's `selected_component`, or a displayed historical run of the
  active project), then discussed (the last component a successful tool call
  named, or `discussed_refs`), then the only one, then ask (up to 12
  candidates with key fields, plus an `ask_user` instruction).
  `component_class: "storage"` spans StorageUnit and Store. A selection of
  another class, or one not in the active network, is reported in `ignored`
  and never used; a class mismatch asks rather than falling back to "the
  only one" or "discussed".
- `harness/loop.py` records each turn's UI context on the session
  (`turn_ui_context`, replaced every turn), so the hint is the browser's, not
  something the model retypes.
- `harness/prompts/reference_ladder.md`: a new tools-on part (no pinned
  fragment moved), pinned in `test_harness_prompts.py`.
- Tests: `tests/test_reference_ladder.py` covers LC-T21 and the resolution
  halves of OJ-05 and OJ-06, through the ordinary turn loop plus one
  authenticated HTTP stream. The edit halves of OJ-05 and OJ-06 close with
  LC-04.
