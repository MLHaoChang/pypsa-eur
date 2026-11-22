<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 22 — Improve and the investment case have no manual path

Status: ready-for-agent
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: blocks decision
Journey fit: results → decide (compare options, investment case).

## What

Improve cards offer only 'Let the assistant do this' (disabled without a key, KEY-LATER-1) and 'Ask'; the tooltip says it can be done by hand but there is no button. Guided Investment, Grid plan and Operation read 'Not run yet' and Expert says 'No value-flow result yet' on every template; the investment case needs a separate click in Expert (IPP: NPV, IRR only after 'Run the investment case'; 'Lifecycle-cost NPV' equals equity NPV). Lever table: identical EUR 34.2m for 4/24/72 h storage and 34.2m differs from the base 34.7m (DC); on staged DC the lever cost differs from base without explanation. Expert results tabs say 'Not run yet' on populated views.

## Acceptance

- Every Improve card has a manual action that runs without the assistant.
- Guided shows how to run the investment case, and it is labelled with what it compares.
- Lever costs reconcile with the base or say why not.

## Comments

Extended by the investment/business-case walk (PR #1668, its issue 02): a manual investment path meets value_flows_not_configured, then cod_missing, then tax and degradation blockers on the data-centre template, and Guided section 7 reads "complete" while finance inputs are empty. Keep this issue's acceptance in step with that issue.

## Triage (2026-11-19, master 9f850d36)

Still true on master (Improve cards: `CardShell` has only 'Ask about this' and 'Let the assistant do this'). Decision (owner, 2026-11-19, IMPROVE-MANUAL-1): each Improve card opens the matching Expert panel with the lever pre-filled; no assistant needed. Blocked by: none. Acceptance above stays; the manual investment path must also meet the blockers in walk issue 02 (#1668), so keep the two in step. Overlaps that issue; slice if over 1,500 lines (PR-SIZE-1). UX reviewer needed.

## Plan verdicts (2026-11-20)

Slices: S1 Improve cards' manual path (UI); S2 Guided investment-case run and plain-word refusal checklist (UI + finance app side); S3 lever costs reconcile with the base (engine, GK A).

GK B3 (3), S1 GO with conditions:
- E1. Nothing is applied on click: a test spies the API client and shows "Open in Expert" sends no write (no run, no PUT, no solver save). VOLL is pre-filled but unsaved.
- E2. The args→PackForm mapper is tested on the actions eh_review.py really emits (a generated fixture), with a parity test against the run_eh_study input schema, so an argument the form cannot show is named on the card, never dropped.
- E3. The prefill is used once and belongs to one project: cleared once consumed, on a project switch and on a user switch; another project's never applies. Each case tested.
- E4. Units: VOLL args and the Solver-settings field are both EUR/MWh; each pack-form field uses its action arg's unit. Tested with the real args.
- E5. A viewer can open Expert; its run or save is refused in plain words by the existing gate (project_view_only), never a raw code.
- E6. Hand-mutate the mapper guards, the one-shot consume and the project key; the kill list goes in the PR body.

GK B3 (3), S2 GO with conditions:
- F1. The refusal checklist is keyed on error_kind values the backend really emits (file:line), with a parity test against the backend's list; an unknown kind gets a generic plain sentence. Guided and Expert render the same component, or a test shows the same words.
- F2. The lifted run hook keeps InvestmentCaseView's lock/409 and refusal handling and guards a double click: one POST however often it is clicked.
- F3. Before a run the counterfactual label states what the engine compares (cite the backend definition); after a run, the report's own counterfactualStatement.
- F4. "Lifecycle-cost NPV" equal to equity NPV: diagnose from the backend first; if different quantities come out equal, it is an engine question for GK A; relabel only if they are the same quantity.
- F5. The "Not run yet" pill fix gets a test on a populated view.

GK B3 (3), S3: route to GK A (levers.py, eh_study.py are engine code). Lead: both costs are total_system_cost_eur = objective − shed_cost (levers.py:434, eh_study.py:294, report.py:515); the gap comes from separate solves (lever at the ENS cap vs the study's bisection). #1752's cheaperDuration sentence reads cost_at_target_eur: if S3 changes that field's meaning, update cheaperDuration and its tests in the same PR.

UX 2 (4): S1 GO (withReturnFrom / Back pill; visible "Not saved yet" for VOLL, never lost or saved silently; the "Filled in from the Improve card" note clears on edit or leave; "Open in Expert" secondary, before the assistant button, always enabled, tooltip and aria name the destination; neutral "This change: …" copy). S2 GO with a change: the counterfactual line in plain words; one ordered plain-word checklist component in Guided and Expert; the pill says what is missing ("Investment case not run yet") and shows only on the tab where that result is absent; wording to UX 2 before building. S3 GO: one plain-word line under the lever table saying why lever costs differ from the base; shot. Shots dark+light per slice, and one of the refusal checklist.
