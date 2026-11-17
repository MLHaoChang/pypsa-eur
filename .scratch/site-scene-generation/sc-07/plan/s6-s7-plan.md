<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SC-07 S6 and S7 plan (and the SC-06 slice (c) asks)

Master `0ab07339`. S1–S5b are merged. What is left of SC-07 is the
`SC-07-results` unit (primary V61, V69, V97) and the closing evidence. The mock
is `s6-s7-mock.png` beside this file (a mock, not the app).

## What is still open on master

- Three routes are still `held-by-spec` on this issue: `GET .../scene/results`
  (`claude-sc03-overlay.json`), `POST .../scene/adopt` and
  `POST .../scene/proposals` (`claude-lv08-r2-route-parity-drift.json`).
  `route_parity --check` fails when Status turns done with any of them held.
- No chat read of the SC-03 scene results exists.
- No integrated scripted-provider journey test exists (V61).

## Slices (PR-SIZE-1: one PR each)

### S6a: `site_scene_results`, the chat read of the scene results (GK B)

Journey: results, decide/compare, report. Guided and Expert share it.

- New `services/site_scene/chat_results.py`. It runs exactly what the route
  runs (`results.parse_query`, `results.gate`, `results.project`) on the
  foreground project, under the store lock then `PyPSAService.get_lock()`, in
  the route's order. Nothing new is computed.
- Inputs: `site_id`, and optional `snapshot`, `period`, `window_from`,
  `window_to`, `run`, `building` (a word from `feature_words`, never an id),
  each validated by the route's parser. The UI's selected snapshot reaches the
  model only as the `ui_context` hint; the server re-parses it (G3, V62).
- Output, capped in items and bytes like `site_scene_read` (G5), cuts named in
  `truncated`:
  - every channel carries `unit`, `run` (the dispatch digest), `freshness`
    (`workflow_tools.solve_freshness()`: `results_current`,
    `inputs_changed_since_solve`) and the native sign convention (G10);
  - an unavailable leaf keeps the route's `reason` (`REASONS`), never a number;
  - stale results carry `stale: true` with the words for it; the answer must
    say so (V69);
  - products and inputs per port role, process mass/energy kept separate from
    allocated demand (V97). A request to "show" a channel never creates a
    component, demand or solve (H4-style fingerprint test).
- Registered once: `catalogue.TOOLS`, `chat_tools.DISPATCHERS`,
  `catalogue.TOOL_ROUTES` → the results route. Read tier, no card.
- Releases the `scene/results` held declaration (fragment edit).
- Tests `tests/test_sc07_results_read.py`: current; another run
  (`run_changed`); wrong period/window; unsolved; stale; unbound target;
  missing series; mixed units; group partial allocation; foreign or deleted
  Site → `not_found`; a project switch between calls; instruction-shaped
  building label fenced; caps at the boundary; values equal an independent
  `n.links_t` read; the network fingerprint and revision unchanged. Mutants
  on the new lines, hand mutants for: dropping freshness, dropping the lock,
  answering a number for an unavailable leaf.
- Shared files touched: `harness/catalogue.py` and `services/chat_tools.py`
  (append only). Open PRs on them today: #1617, #1576, #1624, #1601. I ask
  the coordinator before editing them; the new module and its tests come first.

### S6b: the integrated journey test (GK B)

Journey: template → setup → readiness → run → wait → results → decide → report,
in Guided and in Expert. Tests only; a production change found here is a
defect report, not a fix inside S6b.

`tests/test_sc07_integrated_journey.py`, on `journey_wire.run_journey`
(the real provider wire, only its HTTP mocked), real native services:

1. Template project, anchor, Generate and adopt through the routes (adopt is
   route-only).
2. `get_project_flow` names the first blocker; `advance_workflow` cannot mark
   a section complete (V63).
3. `site_scene_read` → `site_scene_prepare` (bind, place) → card → apply →
   verified receipt (V61, V64).
4. A denied card: no apply, no binding (V64); the manual route reaches the
   same state with no provider (V67).
5. The solve through the ordinary run route (not chat); wait reads the run.
6. `site_scene_results` explains a value; the test compares it with an
   independent read of the network (V69); then an input edit makes it stale
   and the next answer says stale.
7. Session loss and reload: `my_previews` and Resume, nothing re-executed
   (V66); a project switch clears the `[site 3d]` block.
8. Both modes (`mode='guided'`, `mode='expert'`) assert the same tool calls,
   cards and receipts.

### S6c: results entry points in Site 3D (UX)

Frontend only, `site3d/SiteCompanion.tsx` and its test, plus shots.

- In the Results part, "Explain Results" becomes "Explain these results". Its
  prefill names the Site, the selected building's label and the snapshot words
  the panel already shows (`snapshotWords`), no ids (U4).
- "Ask for help" on the results line only when `/flow` reports a results
  blocker (U3). Its "›" link goes to the section through `withReturn` (U2).
- Guided and Expert render the same component and words (EXPERT-GUIDED-1).
- Shots (U7): current, stale, unsolved, building selected, no provider; both
  modes.

### S7: journey evidence and Status done (UX)

- Guided/Expert same-task shots of the whole native journey, including a
  denied card and the manual fallback, from the S6b fixture through the
  route-mocked Playwright harness (SHOTS-1040-1).
- Route parity: `scene/proposals` POST declared served by
  `site_scene_prepare`; `scene/adopt` given a `not_via_chat` fragment entry
  (GK B ruled adopt route-only in S1). The held declarations are removed.
- `sc-07/record.md`: V61–V72 and V91–V98 with the test or shot per row, the
  exact commands and head, and the rows proven elsewhere. The manifest's three
  SC-07 units move to accepted with that record. Status done.

## The SC-06 slice (c) asks

1. Where the Blender-return review sits. Recommended: a card in the Generate
   part (where building corrections live), above the correction panel, shown
   only while a return is open. The companion's Resume line points to it.
2. Guided and Expert. Recommended: the same card and words in both; Guided
   adds no second summary.
3. Chat and a pending return. Recommended: chat describes it, never applies.
   `site_scene_read`'s `my_previews` lists an open `blender_return` with the
   moved/turned/height counts and the named unknowns in words; the Resume line
   says "A Blender return is waiting for review (N buildings changed).
   Review it in Generate ›". Apply stays route-only (`ROUTE_ONLY_ACTIONS`).

The card itself is SC-06 (c)'s build after #1619. The chat description is a
small follow-up in `services/site_scene/chat.py` once #1619 merges; I would
take it as S6d unless the coordinator gives it to SC-06 (c).

## Order and files

S6a (new module first) → S6b → S6c (parallel with S6b) → S7. S6d after #1619.
No change to `harness/events.py`, `loop.py`, `change_set.py`, `confirm.py`,
`compose.py` or `navigation.py` is planned.
