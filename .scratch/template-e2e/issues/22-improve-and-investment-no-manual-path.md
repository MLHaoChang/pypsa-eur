<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 22 — Improve and the investment case have no manual path

Status: needs-info
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

Still true on master (Improve cards: `CardShell` has only 'Ask about this' and 'Let the assistant do this'). Open question sent to the coordinator: what should the manual Improve action do? Options: (1) Recommended: each card opens the matching Expert panel with the lever pre-filled, no assistant; (2) a Guided 'Run the investment case' button only, Improve stays assistant-only until a key is set; (3) split into three issues (Improve manual action, Guided investment-case run, lever-cost reconciliation) and triage each. Overlaps walk issue 02 (#1668); keep acceptance in step.
