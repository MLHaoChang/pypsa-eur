<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-14 — navigation reach: the pages a human can open that chat cannot

Status: done 2026-11-10 (items 1, 2, 3, 6: #700; item 4: #1308; item 5: #1328; follow-up: already on master)
Type: task
Baseline: master `d006f4beaa302b97432c65757762ae180222731a`
Dependencies: none (each item is its own PR); inside LC-03 scope, feeds LC-11

Found while checking the owner's "show me something else". Each is a
reachability defect, not a new capability.

1. `ui_set_snapshot` opens Results but does not set the snapshot
   (`frontend/src/components/uiNavigate.ts:121-126`), although the tool says it
   drives the picker (`harness/catalogue.py:1825-1826`). Set the index from the
   ISO/period, or refuse when it does not exist.
2. `ui_select_component` leaves an open slide panel in front, so Properties
   stays hidden (`uiNavigate.ts:92-96`, `App.tsx:752`). Close the slide panel
   or say the selection is behind it.
3. No chat path opens a component's edit card; only canvas double-click and the
   tagging tour call `requestPropertiesEdit` (`store/uiStore.ts:921`). Add an
   `open_properties` navigation kind (read tier, no write).
4. Guided steps and tiles cannot be targeted: `ui_open_panel('hubDesign')` has
   no step or tile (`pages/hubDesign/hubDesignStore.ts:10`,
   `chapters.ts:48-182`). Add an optional `guided_step`.
5. Campus electrical, island and GridSpine sub-sections cannot be targeted
   (`pages/CampusElectricalPanel.tsx:182-193`). Add an optional section id.
6. Canvas views `hybrid` and `site` are unreachable from `topology/map`.

Each PR: a Tier A test that the chat action shows the target, the inventory and
control-matrix regenerated, and no new authority (all read tier).

Outsourced: items 1, 2, 3 and 6 are outsourced to Codex as task I
(`.scratch/live-copilot/outsource/I-navigation-reach.md`, branch
`outsource/navigation-reach`). Items 4 and 5 wait for PR #691.

Follow-up (GK condition 8, 2026-10-23): plain `ui_select_component` (without
`open_editor`) still has no existence check, so selecting a component that is
not in the network emits a `select_component` event that shows nothing. Task I
adds the check only for `open_editor=true`; a later small PR gives plain
selection the same `component_not_found` refusal.

## Comments

- 2026-11-10: item 4 merged as #1308 (`guided_step`). Item 5 is PR #1328 (`panel_section`), built by the
  LC-14 session under owner decision LC14-R-1; the Codex task R was withdrawn.
- 2026-11-10, GK B (41), plan GO for item 5, no trims (all 16 ids), conditions:
  - G1: `PANEL_SECTION_ENUM` may be a literal in `catalogue.py`; pinned both ways to the union of
    `_PANEL_SECTIONS`, and no id belongs to two panels (a test for each).
  - G2: a static test that every enum id has exactly one `data-panel-section` marker in its own file and that
    no marker exists outside the enum.
  - G3: each dispatcher guard (panel membership, non-string, unknown id, spelling normalisation, no event
    before refusal) is mutated, and a test kills it.
  - G4: the refusal names the panel by its on-screen title; one message per panel is pinned, ids joined with
    "or", no backticks; kind stays `invalid_navigation`; the error-kind manifest tests run at the trial merge.
  - G5: hook cases: absent section (cleared, no later scroll), data that never arrives (cleared when the
    panel goes away), a second request replaces the first.
  - G6: `loop.py` adds only the two keys; the acknowledgement carries the request as sent and never claims the
    section was shown; `navigation.py` unchanged (ACK option a).
- 2026-11-10, outcome: every item is merged. Item 4 #1308 (`guided_step`), item 5 #1328 (`panel_section`), items
  1, 2, 3, 6 #700. The `ui_select_component` follow-up (GK condition 8) was already done: plain selection
  refuses a component not in the network with `component_not_found` (on master before #1142, the oldest
  merge in the first-parent history checked; tested in `tests/test_lc06_d24_ui_events.py`), so no PR was needed.
- Known and not part of this issue: in Guided, a jump can show both "Back to Results" (header) and "Back to the
  cards" (floating); UX logged it as its own follow-up.
