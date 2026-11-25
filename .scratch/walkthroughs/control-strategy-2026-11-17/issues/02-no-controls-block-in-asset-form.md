<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02: Converter control modes have no home in section 5; Guided cannot see or set them

Status: done
Impact: high
Templates: Island Microgrid, Data Center Energy Hub, every template with a battery, PV, wind or UPS
Found by: control-strategy walkthrough, 2026-11-17 (master 9491878d)

## Repro

1. Island Microgrid, Guided, section 5 › Storage › island_battery › "Edit ›".
2. Read the drawer: carrier, power, duration, discount rate, efficiencies, cyclic state of charge. There is no Controls block.
3. Press Expert › 8 Scenarios & optimisation › Grid study › Island. The control modes are only here: "Control mode of island_battery: gfm_droop", "Control mode of pv_plant: gfl_qu", and so on, with a raw JSON editor below.

## Actual

- The control mode, droop, current limit and inertia of each converter-coupled unit can be edited only in the Grid study panel (section 8, Expert).
- Section 5 lists them only as one assumed ledger line ("Island unit settings: … batteries droop 2 %, …"), whose Edit link is broken (issue 01).
- Guided has no route to them.

## Expected

Per the approved design, every converter-coupled asset (BESS, UPS, PV inverter, electrolyser rectifier) has a **Controls** block in its section-5 asset form. The island panel shows the preset read-only, with a link back. Guided shows the block at its depth (≤3 decisions per card) or names the field with a route.

## Breaks

- `.scratch/project-workflow/design.md:302` (DC-04 control presets: "A Controls block in the asset form of every converter-coupled asset. It is not edited in the island panel").
- `design.md:381` (B3 asset form: General › Sizing › Controls › Reliability).
- `design.md:402`, rule 7.
- `.scratch/project-workflow/workspace-reconciliation.md:303`: the Control strategy work group's inputs are "5: the Controls block of each converter-coupled asset; 8: island requirements".
- EXPERT-GUIDED-1.

## Note

This is a design slice that has not been built, not a regression. It needs a plan before building (it touches the asset form and `CampusIslandSection.tsx`). Issue 01's link fix can land first and point at the island panel.

## Comments

### Plan GO, CS-B PR 3 (2026-11-25)

Plan: Controls block in the asset form (Guided and Expert), the island panel read-only with "Edit in asset ›", the raw editor narrowed to requirements; on the server, `get_island` adds `controls`, `control_fields` and `unit_controls`, and `merge_island_patch` makes the mode switch. The electrolyser rectifier part moves to issue 14 (coordinator, option 1).

GK B3 (6) session_01G7qCum6HUJsEnApmq7PQMr, 2026-11-25, verbatim:

> B2 option 1 is GO: the server makes the mode switch. B1 is GO. Both are subject to K1–K13, the #1913 rules, and K14–K21 below. UX 29 rules on F2/F3 placement and the words.
>
> K14 (B1, K7). `controls` and `control_fields` are the schema's own objects. A new public alias in gridspine/schema/island.py is a gridspine change: name that hunk in the PR body for GK A, and keep it alias-only (validate is unchanged, `_CONTROL_FIELDS is CONTROL_FIELDS`). Re-exporting through services/adequacy/island_config.py is fine, if you'd rather leave gridspine untouched. The parity test compares whole dicts with `==` against the schema module, and `is` against the alias. Run test_packaging_requirements at the trial merge either way. `unit_controls` comes from the same `raw` bytes as `config` and `revision` (get_island's one read, #1913). An invalid sidecar gives `unit_controls: None` with no 500, and is tested.
>
> K15 (B2 detection). A switch is `patch.control` != the unit's EFFECTIVE (preset-expanded) control. Test both sides: a same-mode patch, or a patch that changes only another field, leaves `preset` and every other field byte-equal (no materialisation, no opt-out); a real switch materialises the block and sets `"preset": null` on that unit only, and the project `preset` and the other units stay byte-equal. `"preset": null` stays refused when the client sends it. Only the server's switch path sets it.
>
> K16 (B2 fill). (c) uses `preset_block(name, kind)`, which respects the kind, so `grid_forming_support` fills bess only. When no applicable preset has the new mode (e.g. gfm_droop, or pv → gfm_vsm), nothing is filled, and the missing fields are a 422 naming them, unless the same patch supplies them. Test that case. For gfl_fw, the rating-scaled FFR_SIZED fields are never in `preset_block`. Either: fill them through `ffr_sizing` with `_fixed_ratings` read BEFORE `_lock(sidecar)` (as `select_island_preset` now does), tagged `assumed`; or leave them as a 422 naming them. Say which, and test it. Never read the network under the sidecar lock.
>
> K17 (B2 sources). Materialised and filled values carry the schema's `assumed` tag. Values the patch sends take `EDITED_SOURCE`. The client never sends a source at any depth (#1913 ask 5 still holds on this path). Kept fields keep their stored source byte-for-byte: test pf_rated and neg_seq `{value, source}` unchanged across gfm_droop → gfl_pq.
>
> K18 (B2 drops, K10). Dropped fields are visible: the chat edit card for a switch (it previews through `merge_island_patch`) shows each dropped field as removed and each filled one as old → new, with a test. The ControlsBlock shows, before Save, which values the switch will drop.
>
> K19 (F3, K4, and #1913's nested-source rule). The raw editor narrowed to `requirements` must not send stored `{value, source}` entries back: the server now refuses them (422). Send only the leaves that changed, as plain values: a diff against the loaded config. Show a source edited in the raw text as an inline refusal (sources are set by the server). Tests: an unchanged raw save writes nothing, or sends an empty patch, with no 422; one edited value lands with source `datasheet`; a units edit survives a later requirements save. The same goes for the ControlsBlock: plain values only, with `base` from the same GET as `unit_controls`.
>
> K20 (lock, base, parity). The whole switch, (a)–(d), runs inside `patch_island`'s lock after `_check_base`. The route and the tool give a byte-equal config for a switch (extend K12's test). A 409 is detected only with `isIslandConfigChanged` (import it; no new 409 check of your own).
>
> K21 (mutants, PR size). Targeted mutants on: K15 detection (effective vs raw control); (a) the opt-out; (b) the drop list, one mutant per class (CONTROL_FIELDS[new], pf_rated, PF_CHOICE, neg_seq); (c) the fill and its kind check; K19's diff, in vitest. Put the kill list in the PR body. If the diff (excluding shots and fixtures) goes over about 1,500 lines, split it backend-first (B1+B2 with tests), then the frontend (PR-SIZE-1).
>
> Out of scope agreed: the electrolyser kind (gridspine, GK A), Reliability, and issue 01's ledger link.

UX 29 session_01G7qCujp5NnatvvnLMux1fy, 2026-11-25: GO, with Q1 (1) "Save controls" as its own section after the asset card, with the discard guard; Q2 (1) Guided shows the preset line and the mode, fields under "More control settings", the fold auto-opens on a required-empty or error field, and a Save that can't succeed is disabled with a reason; Q3 (1) the project preset picker stays in the island panel, with the line "From the project preset: Grid forming support ›" linking there through withReturn. Conditions: (1) no engine keys in user text, words from one islandWords.ts map with a parity test against control_fields, and the F3 row "island_battery: Grid forming, droop · Edit in asset ›"; (2) the existing assumed tag style, with the key shown once; (3) curve fields get a format hint and a field error in words; (4) the 409 reads "The island settings were changed elsewhere. Reload to see them before saving." with Reload in a role="status" region (ask first when there are local edits), a 422 shows on its field by label, and no raw backend text in a toast; (5) "Edit in asset ›" goes through withReturn, and a unit missing from the network says so with no link; (6) no ControlChapter link; (7) shots in Guided and Expert, light and dark, on Island Microgrid and DC hub, before and after (the list in the message).
