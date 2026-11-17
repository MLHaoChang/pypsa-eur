<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Carriers & processes: every Edit in Guided jumps to Expert

Status: ready-for-agent
Size: M
Gated: UX
Likely files: pypsa-gui/frontend/src/pages/hubDesign/SectionView.tsx (GenericSectionCard, "Add a process unit in Expert"); pypsa-gui/frontend/src/pages/hubDesign/SectionHoldings.tsx (Edit buttons ~l.28)
Journey fit: set up site (section 2). Carriers and recipes hand on to the pickers in sections 3–5 and 7.

## What

Guided section 2 lists the carriers (and, on eh_concentrator_staged, ore, slurry and concentrate). Each **Edit ›** and **Open carriers & processes in Expert ›** switches to Expert's Carriers table with a "Back to Carriers & processes" button. There is no Guided way to add a carrier or a process unit, or to change a recipe. "Add a process unit" is labelled "in Expert". This is the jump that §2.2 rules out.

For the electrolyser (H2 hub) and Haber-Bosch (ammonia) templates, the recipe is a pre-filled value the user should be able to confirm or change (§2.3: "electrolyser recipe", "Haber-Bosch recipe (kWh/t, H2 t/t)").

## Acceptance

- Section 2's Edit opens the carrier, or the process unit's recipe, in a drawer (FieldDrawer pattern, as section 3 does) without leaving Guided.
- A process unit's recipe (inputs per unit of product) can be read and changed from section 2 in Guided and in Expert, with the same labels and units.
- (UX) Guided edits recipe values (inputs per unit of product) in a drawer. Adding a carrier or a process unit stays an Expert action, reached by a user-clicked "›" link with `withReturn` (owner question Q5: (a) values only, recommended; (b) Guided can also add). The card names its Essentials (≤3) and puts the rest behind "More settings".
