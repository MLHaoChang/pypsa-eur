<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: N-1 rows show the line only, with no per-transformer sub-rows

Status: ready-for-agent
Type: task
Found by: G2P-13 (#1194) PR body, "Skipped / risk".

## Problem

The N-1 line is one number. In the Expert table and the report it should list each site transformer as a sub-row, so the user sees which transformer outage costs the tonnes. Guided keeps the single line. Needs the per-Link split from `expected_outage_loss` (check whether it already returns it).

## Where to look

`services/results/grid_risk_production.py` (N-1 line, from `process_loss.expected_outage_loss` on the site-transformer Links), `GridRiskTab.tsx` Expert table and `chapters.GRID_RISK` in the report.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `services/results/grid_risk_production.py`, `GridRiskTab.tsx`, `chapters.GRID_RISK`.
- Blocked by: none.
- Model tier: opus.
- Gated: GK A (engine rows), UX (12) (Expert table).
- Size: M.
- First check whether `expected_outage_loss` already returns the per-Link split; Guided keeps the single line.

### 2026-11-07: plan GO, GK A (17) and UX (13)

Plan: `expected_outage_loss` already gives the per-Link split and `_n_1` keeps one row per site transformer; each row gains a per-period cell, and the Expert table and report list the rows as sub-rows, as they do for the island. Guided keeps the single tile.

GK A (17):
- G22-1. One fold, not a re-association: one helper folds a product iterable into (t, eur, flag) with today's rules exactly (the last None product's flag wins; eur None when t is None). The line calls it once over every product of every row in today's order; each row over its own. Test: rows == their own fold, line ≈ Σ rows, line equal to master's in a JSON compare, with values whose float sum depends on order.
- G22-2. The row's per-period cell adds `t_per_year`, `eur_per_year`, `eur_flag` beside the existing keys, which stay untouched. Test a row with t None but EUR priced: the row's EUR is None.
- G22-3. A row's lower bound goes in that row's `by_period[k]["flags"]`, never the row-level flags; never mutate `res["flags"]` or `v["flags"]` in place (copy). Test two periods, lower in one only.
- G22-4. The PR body lists every reader of the n_1 line's `rows` / `by_period` and whether the added keys change its output; byte-equal pre-existing fields where a digest or export hashes it.
- G22-5. Report sub-rows only when the line is not missing, the row's own cell under the line's EUR/flag rendering, no new magnitude path; a missing n_1 line gives no sub-rows.
- G22-6. Mutants on the fold helper and the row-cell build; t-None, eur-None, flag-precedence and lower-bound-placement each killed by a test of its own.

UX (13):
- Label "<link> out of service" (built in the backend; table and report read it).
- Reuse the island sub-row path in ExpertTable unchanged; no new styling.
- One fixed order, the backend's, in table and report; no frontend sort.
- A skipped transformer (no MTTR, no outage rate, no units cut off) gets no row; the N-1 line's basis says in plain words that it covers only the transformers listed when any were skipped (colon, not an em dash).
- Guided keeps the single tile; Expert shows more, never less.
- Shots: Expert table with two transformers before/after, light and dark; the N-1 line missing (no sub-rows); the report table's rows. Real API with route mocks.
