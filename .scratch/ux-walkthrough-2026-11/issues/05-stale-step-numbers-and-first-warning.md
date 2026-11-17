<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Verdict text points to 'step 3 (Goal)'; every template opens with a warning

Status: needs-triage
Type: finding (keyed walkthrough audit, UX-NEXT-1)
Journey steps: J3 Readiness, J6 Results
Severity: medium
Send to: Guided results legibility; next-step flow

## What happens

- Industrial Hydrogen (staged) and Green Ammonia (staged): "No reliability goal is set for this site, so the study did not certify it: set an allowed shortfall in step 3 (Goal) to get a verdict." Goal sits in section 8 "Scenarios & optimisation"; section 3 is "Connection & grid". The goal-less staged templates therefore end in a non-verdict.
- All hub templates open section 1 as "to check" with "The site has no location: no bus is placed. (Project & site › Bus)". Section 9 repeats it as "Ready to run. 1 note". A template that ships ready to study starts amber.
- The Island Microgrid template opens on section 3, not section 1.
- Wording: em dashes still join clauses ("Strong — import whenever needed", "Subsea tie — normally open; the study runs the site as an island"), against the review-ux colon rule (audit finding 11 still open).

## Done when

Verdict text names the section by its current name with a link, and a template that is ready to study is not amber on its first screen.

## Journey fit

J3 to J6.
