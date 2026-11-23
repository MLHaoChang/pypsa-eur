<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Sealed branch-loading facts count an assumed rating as a stated one

Status: needs-triage
Owner decisions: IMPORT-ZERO-RATING-1
Review: GK B (app backend).
Blocked by: none (issue 05 landed the marker reads in the four result views).

## Problem

Issue 05 made the loadflow summary, AC power-flow loading, per-asset results and the checked branch-loading chart treat a branch with `s_nom_source` = "assumed" as having no stated rating. Three places still read that rating as real:

- `harness/branch_calculations.py`: the sealed `branches_above_rating` and `hours_above_rating` facts count an assumed branch.
- `services/compare/loading.py`: the what-if loading comparison ranks an assumed branch's loading as if the file stated the rating.
- Links: no reader marks a Link, so the Link `p_nom` arm of `assumed_rating.is_assumed` is untested against real imports.

## Expected behaviour

The sealed facts and the comparison leave an assumed branch out of their above-rating counts and name how many were left out, as the loadflow summary does.

## Acceptance criteria

- [ ] On an imported case14 with one assumed line, the sealed above-rating facts exclude it and say so.
- [ ] A network without the marker seals and compares as before.
