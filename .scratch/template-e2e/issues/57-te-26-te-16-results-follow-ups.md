<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Results follow-ups from te 26 and te 16

Status: needs-triage
Type: task
Review: UX
Source: te 26 hand-off (#2127), te 16 (#2129) (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- te 26: a per-row Edit link in `AssumptionsBehindResult.tsx` (reuse `editFor` from `WhyThisNumber.tsx`).
- te 26: no group for a project without a template marker (`useHubTemplate` null); the synthetic line is gated on ledger rows, not `LoadProfileInfo.synthetic`.
- te 26 UX: em-dash chains in the Adequacy intro and firm-capacity note; mono "Reliability goal: could not be read" (`AdequacyTab`).
- te 16 UX: show the study run time near the exports.

## Comments
