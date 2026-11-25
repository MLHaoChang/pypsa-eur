<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Raw parameter, option and internal names shown to the user (RAW-NAMES-1)

Status: ready-for-agent; audit 2026-11-25: #1631, #1738 merged (plain names for load-profile fields, control modes, Set-in-Expert lines); left: raw ids still allow-listed in uiCopyGuard.allow.json, template-wide snake_case scan test
Owner: expert-catalogue slices 01/02 (lead session_01G7qCuo4TtQpLUWDE5GKpny)
Size: M
Gated: UX
Decided: TEMPLATE-GAPS-1 Q2 (a), component ids count as raw names; templates ship plain display names, ids stay internal
Likely files: pypsa-gui/frontend/src/layout/PropertiesPanel.tsx; pypsa-gui/frontend/src/components/series/ (generator advanced fields); pypsa-gui/frontend/src/pages/CampusElectricalPanel.tsx and the Scenarios "Grid study" panel; pypsa-gui/backend/project_templates/eh_concentrator.py (`__lost_concentrate`); price-series library labels
Journey fit: every set-up step (sections 1–8) in Expert, plus the Guided drawers that reuse the same editors.

## What (seen in the running app, 2026-11-17)

| Where | Raw text shown |
|---|---|
| Load property panel → Profile → advanced (Guided Edit drawer and Expert) | `holidays`, `timezone`, `seasonal_amplitude`, `noise`, `seed`, `shifts`, `base_share`, `saturday_operation`, `shutdown_weeks` as field labels; archetypes in lower case ("ev charging", "ai training dc") |
| Expert Scenarios → Grid study | checkbox labels `ups_bridged`, `seamless`, `planned`; control-mode options `gfl_pq`, `gfl_qu`, `gfl_pf`, `gfl_fw`, `gfm_droop`, `gfm_vsm` |
| Expert Connection & grid → Campus file | a raw YAML stub (`campus:` / `pcc: …`) as the editing surface |
| Assets (eh_concentrator_staged) | the internal unit `__lost_concentrate` listed as a generator |
| Commercial → Import price series | library ids `ipp_da_base v1`, `ipp_da_cannibalised v1` |
| Everywhere | template component ids as the only name: `it_load`, `genset_new`, `bess_new`, `grid_import`, `dc_mv`, `site_transformer`, `hospital_load` (Q2) |

## Acceptance

- Each raw label above has a plain-language label in both modes (glossary: pypsa-gui/CONTEXT.md). Internal units such as `__lost_concentrate` are hidden or labelled as what they mean ("Concentrate shortfall").
- A test scans the rendered Guided sections and Expert panels of each template for `/\b[a-z]+(_[a-z0-9]+)+\b/` outside allowed component names (per Q2).
- (UX, Q2 (a)) Templates ship plain display names. Existing projects keep their ids (no renames). The names follow the expert-catalogue plain-name table (session expert-catalogue).

## Progress (session for PR on branch claude/template-e2e-05-raw-names)

Done in this slice (screens neither #1686 nor #1634 touch), via `frontend/src/plainNames.ts`:
- Load profile advanced fields and archetype/preset names (Guided and Expert share `LoadSeriesEditor`).
- Grid study island scenarios and control-mode options.
- Commercial price-series picker and its provenance line (`Ipp da base (version 1)`).

Waiting on other PRs / later slices:
- Scorecard raw names: DC walk DW-C (#1686).
- Model names and the plain-name table for template component ids (Q2): expert-catalogue 01a-1 (#1634). The ids in `Control mode of <unit>` and `it_load`, `genset_new`, ... stay until that table lands.
- `__lost_concentrate` in the Assets list: the list is built by `services/equipment_table.py`, which #1686 edits; hide or relabel after it merges.
- Campus file raw YAML stub: needs a form editor, a separate slice.
- Template-wide snake_case scan test of every Guided section and Expert panel: needs the Q2 names first.


## Comments

- Seen again 2026-11-20 (template walk, master 1f6905e3): DC Guided step 3 prints 'grid_import, 40 MW', 'BESS new is not sized yet'; H2 step 1 says 'Fmea top ranks them'.

- Seen again 2026-11-21 (template walk, master 6b421b06): IPP, Guided Connection & grid: "Grid strength: data missing — eh_sk_mva (and IBR capacity) required on ALL eh_poc buses for SCR gate; missing/invalid at: grid". Ammonia: Guided Sized design lists `wind_new_2027`, `electrolyser_new_2030`, `h2_storage`, `nh3_tank`; the Expert investment case says "not established (no_discharge:bess_new_2027; overnight_cost_missing:bess_new_2030; overnight_cost_missing:bess_new_2035)". Expert Key capacities lists "concentrate shortfall 486 MW" and "NH3 shortfall 32 MW" as capacities.
- 2026-11-21, expert-catalogue 02c: the raw-name guard (`uiCopyGuard.test.ts`) now scans every `.ts`/`.tsx` under `src/` for catalogue model names, template component ids and Library ids. One allow row names this issue: `layout/properties/cardKit.tsx` `grid_import` (the Energy Hub role tip; the role dropdown still lists raw role ids, `EH_LINK_ROLES`). Ids built at run time (asset names from the network) are not seen by a source scan.

- 2026-11-23 status sweep: landed in #1738 (6c32315c); #1631 (f713f49a): plain names for load-profile fields, island control modes, price-series picker, "Set in Expert" lines. Remains: raw ids still allow-listed in uiCopyGuard.test.ts and the template-wide snake_case scan test.

## Audit (2026-11-25)

- 2026-11-25 audit: PR merge state checked against origin/master log; acceptance items grepped on master code.
