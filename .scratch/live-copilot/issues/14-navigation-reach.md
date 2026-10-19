<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-14 — navigation reach: the pages a human can open that chat cannot

Status: ready-for-agent; small, independent PRs
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
