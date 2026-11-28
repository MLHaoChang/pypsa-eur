<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Guided risk table follow-ups from te 24

Status: needs-triage
Type: task
Review: GK C1 + UX
Source: te 24 hand-off (#2092, #2097, #2117) (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- GK C1: the stored EH report carries no network fingerprint, so a report cannot be matched to the network it was computed on.
- Guided ResultsCard "Biggest risks" and the headline read `fmeaTopRows`, which is class-B only (`pages/hubDesign/cards/ResultsCard.tsx`, `headline.ts`; checked on master 2026-11-28, still so). Overlaps te 18 (headline figures); decide there or here.
- UX 33: LCOE notation reads "413.2 €/MWh" while the rest of the page uses another form of the unit.
- NPV shown without `money()`.
- The "rarely runs" wording has no stated threshold.
- Zero-cost genset nit (wording when a genset has no fuel cost).

## Comments
