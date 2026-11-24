<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Sealed branch-loading facts count an assumed rating as a stated one

Status: done (#1934; AC sealed facts: issue 07)
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

## Comments

GK B4 plan verdict (GO with conditions), recorded:
1. Compare: an assumed entry's `binding_hours` is None, never 0. `types.ts` and `CompareView.readPV` still type it non-null; the null-aware change is a follow-up for #1838 (CompareView).
2. All branches assumed: network `branches_above_rating` is unavailable (reason `rating_assumed`), never 0. With some assumed, the notes give the true count (no name list).
3. Marker-less sealed facts, slots and notes pinned to a literal. Stored raws are recalculated, not trusted: a raw without the marker recalculates unchanged; one carrying it that now leaves nothing answered is refused as `unavailable_conversation_snapshot`, not a 500.
4. `assumed_rating_count` and `rating_assumed` default; `LoadingComparison` is not persisted or hashed.
5. Mutants run on both modules; survivors listed in the PR body.
6. Trial-merge test list: harness manifest/inventory, branch-loading assurance (LOPF and AC), compare suites.
