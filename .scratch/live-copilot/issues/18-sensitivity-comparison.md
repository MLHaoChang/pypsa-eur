<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-18 — compare a base with several sensitivities

Status: done (#812, fb7876f6)
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

## Rules folded in from #567 HV-D1 (decision robustness: comparison)

Source: draft PR #567 `.scratch/high-value-ai-study/` (commit e8d53410), research only; owner decision AI-567 (2026-10-21). Adopt the listed cases into this ticket's own numbering; each must name its token/packet limit.
- Refuse incomparable cases (reuse `compare_project_runs` refusals); show a qualified preference or "no defensible preference", never a bare winner.
- Native calculations only; at most 12 admitted facts in any explanation (H2 rules).
- Robustness verdicts stay in the existing decision study (services/study/findings.py) and adequacy engines; no new evidence map (reuse card-workspaces §2.3 states, Run evidence, S8).
- Cases: #567 D-series.

## Build notes (2026-10-28)

Branch `claude/lc18-sensitivity-compare` from master `21cef975`. GK B plan GO
(total cost, 0.1 % tie tolerance, access, registrations, basis reuse); UX GO
(assistant table, tool-given labels and plain-word reasons, offer-only
Compare with Back).

- `harness/sensitivity_comparison.py`: `compare_sensitivities` and
  `export_sensitivity_comparison`; basis checks reuse
  `experiment_comparison.basis_digests` (extracted from `_basis`).
- `compare_scenarios.focus` gains `loading`; `ui_open_panel(compare)` keeps
  "Back to <origin>" (`uiNavigate.ts`).
- Total cost = annuitised CAPEX + OPEX. The engine already pro-rates CAPEX to
  the modelled horizon (`services/compare/support.py` `_safe_capital_cost`,
  nyears), so a short horizon's note says both terms cover the modelled hours,
  not "CAPEX is annual".
- Tests: `tests/test_sensitivity_comparison.py` (OJ-29, OJ-30, LC-D19 zero
  base / incompatible basis / partial failure, LC-T19, access, guards,
  packet at maximum, tool selection); `uiNavigate.compare.test.ts`.

Kept: no in-chat card (UX: a follow-up if the shots show the table drifts);
multi-period total cost is unavailable rather than mixed.
