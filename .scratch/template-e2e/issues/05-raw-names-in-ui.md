<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Raw parameter, option and internal names shown to the user (RAW-NAMES-1)

Status: ready-for-agent
Size: M
Gated: UX
Blocked by: owner question Q2 for the component-name part only
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
