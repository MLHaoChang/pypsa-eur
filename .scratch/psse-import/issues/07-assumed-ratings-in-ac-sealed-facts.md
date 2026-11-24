<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The sealed AC branch-loading facts count an assumed rating as a stated one

Status: needs-triage
Owner decisions: IMPORT-ZERO-RATING-1
Review: GK B (app backend).
Blocked by: none (issue 06 landed the LOPF sealed facts, #1934).

## Problem

`harness/ac_branch_calculations.py` seals `ac_hours_above_rating` (and its branch counts) with no read of `assumed_rating.is_assumed`, so a branch with `s_nom_source` = "assumed" is judged against a rating the file never stated. Issue 06 fixed the LOPF facts in `harness/branch_calculations.py`.

## Expected behaviour

The AC sealed facts leave an assumed branch out of their above-rating figures, name how many were left out, and never turn an all-assumed population into a 0.

## Acceptance criteria

- [ ] On an imported case14 with one assumed line, the sealed AC above-rating facts exclude it and say so.
- [ ] With every branch assumed, the AC above-rating fact reads unavailable (`rating_assumed`), not 0.
- [ ] A network without the marker seals as before (literal pin).
