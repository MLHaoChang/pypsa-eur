<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The 220/110 kV class-rule losses miss Ecodesign Tier 2 and need a sourced no-load/load-loss split

Status: ready-for-agent
Type: task
Blocked by: None

Filed by campus-library-gaps 02 (2026-11-11), from issue 02's check.

## What was seen

All five units TR_220_110_125/160/200/250/315 reach PEI 99.654 %, below the Tier 2 minimum (99.780 to 99.797 %, Regulation (EU) 2019/1783 Annex I Table I.7) and below Tier 1 (99.737 %). PEI bounds only sqrt(P0 x Pk): at the rule's uncited Pk of 0.3 %, P0 is 2.5 to 2.9 times what Tier 2 allows, so the rule overstates losses against a compliant unit with that Pk (conservative for loss cost), but a compliant unit with a higher Pk can lose more at high loading, so the rule bounds losses neither way. See issue 02 ## Comments.

## What to do

1. Find a public datasheet, tender document or standard giving P0 and Pk for 220/110 kV transformers at 125-315 MVA.
2. Propose the values as a reviewed change to `gridspine/templates/data/campus_assets.yaml` (`pfe_kw`, `vkr_percent`, `i0_percent`). A new value changes the library's sha256, so it needs a new defaults pack version. Reviewer: GK A.
3. Until a source exists, the class rule stays, tagged `assumed`.

## Done

A sourced split is merged, or the owner accepts the `assumed` class rule with its Tier 2 miss.

- 2026-11-13 owner decision CLG03-SOURCE-1: source first; if no public P0/Pk source is found, derive the split from Tier 2 and tag it derived. GK A reviews.
