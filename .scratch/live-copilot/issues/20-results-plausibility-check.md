<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-20 — "do these results make sense?"

Status: folded into the study harness (owner decision SH-FOLD-1, 2026-10-28): built as the result gate in `.scratch/study-harness/issues/05-result-gate-and-rerun-loop.md`; the rule list and done-when below stay its spec.
Previous status: ready-for-agent. Owner decision Q5 = A (2026-10-19): the first item
Progress: Outsourced task K has a draft plan awaiting GK B approval and alignment with study-harness issue 05 (SH-FOLD-1); no implementation or tests have been written. Only the interim rung is merged. K proposes the first five shared native rules; the remaining six rules, tab navigation and sensitivity checks stay follow-ups.
after LC-05 closes, ahead of the other LC-06 work, with the interim rung
(item 4) from LC-03 on.
Type: task
Baseline: master `ae95a94a866730c0fe67fad37e238dc281786bd6`
Dependencies: LC-03 result adapters (the rules read their facts); LC-18 for
the sensitivity variant
Catalogue: [feature catalogue](../feature-catalogue.md); journeys OJ-18, OJ-19, OJ-38

## Problem

The owner wants the copilot to say whether results make sense, spot what
looks wrong and explain why. Today:

- **Before a solve** the checks are real: `validate_network` (errors and
  data-defect warnings such as `timeseries_all_zero`/`spike`),
  `diagnose_network` (islands, unserved islands), `project_readiness`, and
  for Energy Hub designs `review_eh_study`/`suggest_eh_setup`.
- **After a solve** nothing checks results. `sanity_check_results`,
  `diagnose_results` and `generate_run_report` were once registered but
  never implemented and were removed (`services/chat_tools.py:7332-7341`);
  `smoke/qa_e2e.py:254` (S2.5) guards against stubs coming back. Any
  verdict today is the model's own judgement over raw numbers, which LC-C3
  does not allow for checked claims.

## Scope

1. **A native rule set over admitted facts**, each rule with an ID, the facts
   it reads, a threshold with its basis, a severity and a one-sentence
   explanation template. First rules (each needs an independent fixture):
   - lost load > 0, or any price at the VoLL slack;
   - curtailment share above a stated threshold per carrier;
   - branch utilisation > 100 % (active-power proxy, labelled as such);
   - extendable capacity ending at `*_nom_max` (bound-driven build);
   - storage charging and discharging in the same hour;
   - negative or zero prices with no stated cause;
   - generators dispatched above `p_max_pu·p_nom` or below `p_min_pu`;
   - energy balance residual (supply − demand − losses − storage) above
     tolerance;
   - emissions > 0 with a binding CO2 cap of 0, or a cap with no dual;
   - objective magnitude per MWh served outside a band derived from the
     input costs;
   - results stale against the solve-input fingerprint (LC-12).
2. **One read tool** (a new name; S2.5's list stays as is) returning
   `{rule_id, severity, verdict, facts[], explanation}` per rule, plus
   rules not evaluated and why. The model narrates; it adds no verdicts.
3. **Show.** Each finding names the tab that shows it and opens it on
   request (LC-11 per-tab reads).
4. **Interim rung (LC-03 onwards, no new tool).** The prompt chains the
   existing pre-solve checks and says plainly that post-solve plausibility
   rules are not available yet. No invented verdicts.
5. **Sensitivities.** For a comparison (LC-18), flag deltas whose sign
   contradicts the declared change (e.g. cheaper storage, higher cost).

## Not in scope

Domain certification; adequacy and island engines keep their own reviews
(`review_eh_study`, LC-D11–17).

## Done when

OJ-18 and OJ-19 pass in Tier A on `oj-grid` (clean) and `oj-grid-bad`
(seeded defects, one per rule) with every finding traced to a fact; a
fixture with no defects returns no warnings; LC-T19 extra-claim injection is
withheld.
