<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-18 — compare a base with several sensitivities

Status: ready-for-agent inside LC-05, after LC-17
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-17; LC-03 adapter interfaces
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-29, OJ-30

## Problem

Comparison is pairwise everywhere:

- `compare_scenarios` reads A and B with software deltas; its `focus`
  enum lacks the Compare view's `loading` tab
  (`harness/catalogue.py:100-106`).
- `compare_project_runs` compares two runs of one project;
  `compare_experiments` two experiment receipts;
  `get_study_evidence(section='simulation', compare_to=…)` an economic
  comparison against one baseline.
- The Compare view is A/B only (`pages/CompareView.tsx`).

So "compare all sensitivities with the base" makes the model call a pairwise
tool N times and assemble a table itself, which LC-C3 forbids for derived
numbers, and "which is cheapest and why" has no ranked, software-computed
answer.

## Scope

1. **One read for base + N cases** (N ≤ 8): per metric, each case's value,
   the signed delta and delta % against the base, computed in software, with
   units; incomparable cases (different horizon, mode, solver settings,
   unsolved, stale) listed with the reason and no deltas. Reuse the
   `services/compare/` engines; no new metric formulas.
2. **Declared differences.** Each case lists its input changes against the
   base (from the sweep/experiment receipt), so "why" can cite the change and
   the delta together.
3. **Rank.** Optional `rank_by` metric returns the order; the model may not
   rank by itself.
4. **Show.** Open Compare at the matching tab for the pair the user picks
   (base vs best by default); add `loading` to `compare_scenarios.focus`.
5. **Export.** The same table exports through the existing export path
   (`export_experiment_comparison` or `export_to_excel`).

## Done when

OJ-29 and OJ-30 pass in Tier A with hand-computed deltas for three cases;
LC-D19 comparison cases (zero baseline, incompatible basis, partial failure)
pass; LC-T19 injection of an extra number is withheld.
