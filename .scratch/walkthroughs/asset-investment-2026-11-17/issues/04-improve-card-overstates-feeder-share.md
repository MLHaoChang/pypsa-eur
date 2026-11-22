<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Improve says the feeder is "100% of the outage risk" and offers a remedy that cannot fix it

Status: done (#1729)
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J2 verdict → Improve (Guided)
Severity: medium
Send to: Guided Improve (`pages/hubDesign/plainWords.ts`) and the EH recommendation text
Duplicate check: none in #1618.

## What happens

1. Create "Island Microgrid" and run the Guided study.
2. 10 › Improve › WORTH DOING reads "Hospital feeder accounts for 100% of the outage risk" and "Aim: compare the cost with and without a spare unit or extra storage" (`shots/Island_Microgrid_Improve.small.png`).
3. The FMEA table on the same study ranks:
   - `hospital_feeder` at €657,000/yr;
   - each of `diesel_1`, `diesel_2` and `diesel_3` at €302,114/yr.

## Hand check

The feeder's share of the ranked criticality is 657,000 / (657,000 + 3 × 302,114) = 657,000 / 1,563,342 ≈ 42 %, not 100 %.

The backend sentence is "carries 100% of ranked Link risk". The feeder is the only Link, so 100 % is true of Links. `plainWords.ts:11` rewrites it to "accounts for 100% of the outage risk" and drops the word Link, which makes it false.

A spare unit or extra storage on the island bus can't serve the hospital while its only feeder is out. The remedy for a single feeder is a second feeder or local backup at the hospital bus.

## Expected

The plain-words text keeps the scope: "accounts for all of the risk from links (42 % of all ranked risk)". The suggested remedy fits a Link outage: a second feeder or backup on the critical bus.

## Breaks

RAW-NAMES-1 plain words must stay true. review-ux.md: a recommendation must follow from the result it cites.

## Done when

The Island Microgrid Improve card states the feeder's share with its scope and a Link-appropriate remedy. A plainWords test covers the Link-risk sentence.
