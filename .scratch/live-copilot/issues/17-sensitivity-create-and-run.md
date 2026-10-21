<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-17 — create and run sensitivities by conversation

Status: ready-for-agent inside LC-05, after LC-12
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-12 (job completion into chat), LC-16 (multi-asset card)
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-27, OJ-28

## Problem

The tools exist; no layer owns the conversation:

- `run_sensitivity_sweep` (`harness/catalogue.py:1873`) creates and
  enqueues 1–4 saved child projects from a saved baseline, each with up to 20
  changes across Generator, Load, Line, Link, StorageUnit and Store. It needs
  a saved baseline and refuses unsaved edits.
- The experiment tools (`start_experiment` … `compare_experiments`) give
  receipts and no-replay for one or two network cases; larger plans and other
  domains are not admitted (`harness/skills/network-experiment/SKILL.md`).
- `create_scenario` branches a project without running it.
- Nothing posts N finished jobs back into chat; LC-12 covers one job.
- The ScenariosPanel shows sensitivities in a tree (`ScenariosPanel.tsx`);
  chat cannot point at a case there.

## Scope

1. **One path.** Chat sensitivities use the experiment receipts where the case
   count allows (≤ 2) and `run_sensitivity_sweep` for 3–4, with the same
   card: per case, every `entity · field · base → case · unit` row, the
   case name, and "queues N solves". Decide in the PR which path is the
   default and record why; do not build a third.
2. **Baseline precondition.** Unsaved baseline: say so and offer to save (its
   own card) before the sweep; never save silently.
3. **Naming.** Case names derived from the change ("battery-capex-70pct") and
   shown on the card; same-name conflict asks.
4. **N jobs.** Extend LC-12's continuation to a set of jobs: one continuation
   when the last job of the set ends, with each case's status. A failed case
   does not block reporting the others.
5. **Show.** Open the Scenarios panel with the new cases highlighted.

## Done when

OJ-27 and OJ-28 pass in Tier A, including one failed case among three; no
second solve on restart; LC-D19's creation cases pass.

## Rules folded in from #558 ROI-C (owner decision AI-558, 2026-10-21)

Source: `.scratch/project-management-redesign/06-AI-PRIORITIES-AND-COST-SPEC.md` (#558 commit a0831729), research only.
- Suggest 2–3 sensitivity cases (hard cap 4), at most 2 declared changes per case, ranges sourced or chosen by the user (no invented ±20%).
- Editing a suggested case never needs a new model call.
- Build on `start_experiment` / `prepare_experiment` or the edit-card preview; do not add a `sensitivity_preview.py` (no third path).
- Execution only through the existing approvals; no model maths.
- Test cases to adopt from #558: RI13, RI15–RI18.

## Also from #567 HV-D (suggest cases)

Rules above apply to robustness case suggestions from #567 (D-series), research only.
