<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: lines 1–2 are priced in EUR only with exactly one shortfall product

Status: ready-for-agent
Type: task
Found by: G2P-13 (#1194) PR body, "Skipped / risk".

## Problem

With several shortfall products EUR is null because the campus CSVs do not name the product the lost tonnes are of. Either have the campus CSVs carry the product, or let the user choose the product the line is priced at, so EUR is shown with several products too.

## Where to look

`services/results/grid_risk_production.py`, the flag `several_shortfall_products`; the campus dip/restart CSVs (`campus_dip_trips.csv`).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `services/results/grid_risk_production.py` (`several_shortfall_products`), `campus_dip_trips.csv` writer.
- Blocked by: none.
- Model tier: opus.
- Gated: GK A; plan to GK A first.
- Size: M.
- The plan picks CSVs-carry-the-product or user-chosen product, with the reason.

### 2026-11-07: plan GO, GK A (16)

Plan: the campus CSVs carry the product (the unit Link's bus1, as `process_loss.outage_loss` has it); never a user pick. Conditions:

- G23-1. Adding `product` changes nothing else in either CSV: columns, row order and values byte-equal apart from the new column, tested on the existing fixture. Class/event/total rows leave it empty, never NaN-as-text.
- G23-2. The product is read from the network the campus run solved, at write time, never the live network at read time. A CSV from before the column reads EUR None + `several_shortfall_products`.
- G23-3. The Σ-units = total guard runs per table (ride-through, island outside_blocks, island in_block), each with its own test that fails for it alone.
- G23-4. A unit row with tonnes 0 and no product does not poison the sum; tonnes > 0 and no product gives None. per_t never defaults to 0 for an unpriced product.
- G23-5. Single-product output byte-identical to master's: run master's test file against the change.
- Correction (builder, sent to GK A): the product is the bus the mass-pull walk ends at (`process_chain.mass_pull`: bus1 → the shortfall Generator's bus), since the CSV tonnes are t of that product; not bus1. The island line has no written per-table total, so its G23-3 guard is that every row entering the sum has a priced product.

### 2026-11-07: correction accepted, GK A (16)

- G23-1b. `mass_pull`'s return value and callers are unchanged; the end bus is a new, separate output (`process_chain.shortfall_product`). A two-stage chain's unit row carries the downstream product. When the walk reads unknown, `product` is empty and the tonnes are unknown too.
- G23-3 for the island tables: every row whose tonnes enter the sum has a priced product, each table tested on its own. Accepted.
- Reviewer from here: GK A (17).
