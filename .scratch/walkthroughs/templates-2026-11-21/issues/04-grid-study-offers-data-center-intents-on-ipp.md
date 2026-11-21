<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Grid study panel offers data-center intents on a wind/solar plant and a mine

Status: needs-triage
Impact: low
Templates: IPP, Concentrator (same panel; Ammonia in README)
Found by: template walk, 2026-11-21 (master 6b421b06)
Shot: ../shots/ipp-e-gridstudy.png

## What

IPP, Expert › Grid study: "Intent comparison" lists "A. Replace diesel", "B. Keep diesel + MV smoothing BESS", "D. No UPS (BESS-EPS in parallel)", "E. MV UPS (series)"; the fields say "No bus is marked critical", "The project has no DC load envelope"; "Island" asks for an island config on a grid-connected export plant. The IPP has no diesel, UPS or load. The concentrator shows the same five intents and the same legend ("the DC load's dynamic envelope"); its Campus link list offers crusher, flotation, mill_1…mill_3. "Pick at least two intents" is the only guidance. In the dark theme the panel is readable.

Related: `asset-investment-2026-11-17` issue 05 (the intent comparison refuses on the DC template); this is the opposite case, the panel is offered where it cannot apply.

## Why it matters

The panel suggests studies that cannot apply; a user may run one and read a meaningless result.

## Suggested fix

Hide or disable intents for a site with no load, and say why ("this site has no load").
