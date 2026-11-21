<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07: The asset import's receipt and left-out rows use field keys and Python quotes

Status: done
Type: task
Impact: low (wording; the preview itself is in words)
Templates: any (walked on eh_datacenter, Expert)
Found by: DC grid-readiness walkthrough, 2026-11-17 (master 9491878d)
Breaks: RAW-NAMES-1; review-ux.md "No Python-repr quotes", "Text the backend builds and a card renders follows the same wording rules". Extends data-to-model issue 02 (#644), which fixed the commercial receipt only.

## Repro

1. Expert › Assets › Generators tab › Import values from a file…
2. Upload a CSV with columns `name,p_nom,marginal_cost` and rows genset_1 (12, 95), genset_2 (12, 95), rooftop_pv (6, 0) and genset_9 (10, 90). genset_9 doesn't exist.
3. Map p_nom to Capacity (MW) and marginal_cost to Marginal cost (EUR/MWh), set currency EUR and money year 2025, Preview, then Apply 5 changes.

## Actual

- Preview row for the missing asset: "No Generator named 'genset_9'." (`backend/services/equipment_table.py:390`, `{nm!r}`).
- Receipt after Apply: "genset_1 p_nom 10 → 12 MW; genset_1 marginal_cost 165 → 95 EUR/MWh; genset_2 p_nom 10 → 12 MW; … Read back from the project." The preview table above it said "Generator “genset_1” · Capacity".

## Expected

Preview: "No generator named “genset_9”." Receipt in the preview's words: "Generator “genset_1”: Capacity 10 → 12 MW, Marginal cost 165 → 95 EUR/MWh; …", as issue 02 did for tariffs.

The values themselves are right: the energization limit moved from 69.8 to 71.8 MW once saved (see 04).
