<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# UI modernisation for an energy-system tool

Owner decision UI-MODERN-1 (2026-11-08). Goal, in the owner's words: "improve user interface, to make it look prettier and more modern, considering that it is a tool for the energy system".

Constraints for every issue here:
- DENSITY-1: the dense 9–11px text and Inter stay.
- Tokens come from `pypsa-gui/frontend/src/index.css` and `public/brand.css`. Never add a new palette.
- `docs/agents/review-ux.md` applies, with shots of each state in Guided and Expert, dark and light.
- The `ui-ux-pro-max` skill is for lookups only (`--domain ux|chart`).
- VISUALS-1 and DASHBOARD-1 set the chart and dashboard rules.
- The `.scratch/workspace-redesign` visual system (graphite/steel, Manrope) is a separate package. Don't mix it in without asking the owner.

Order: 01 first. Its ranked findings re-scope 02–04 before they open.
