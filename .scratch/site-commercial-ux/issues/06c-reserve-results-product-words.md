<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06c Reserve results name products by id, while the Market pack card names them in words

Status: needs-triage
Blocked by: K8a UI (#1938)
Source: UX 3 on #1938 (point c, not blocking); the K8a real-app run.
Review: UX.

## Problem

Results → Storage cycling (`pypsa-gui/frontend/src/pages/results/ReserveCommitments.tsx`) shows the product ids `fast_symmetric`, `secondary_up` and `tertiary_up` in its table and chart legend, with the kind as "Fast, symmetric". The Market pack card (`pages/results/investment/marketPackWords.ts` `productWords`) names the same products "Fast frequency reserve (up and down)" and "Secondary reserve (up)". A user who follows the card's "Results → Storage cycling" link meets different names for the same product.

## Done when

The results table, the chart legend and the DC backup line name each product as the card does, through one shared helper, with no raw id or kind code on screen. The id may go in a title. A vitest pins the words.
