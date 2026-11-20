<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — IPP preset: capacity payment has no de-rating row; price hints are in another currency

Status: ready-for-agent
Severity: medium (overstates capacity revenue unless the user knows to scale the price)
Group: Investment and business case. Walk 2026-11-17, master 9491878d.
Breaks: `.scratch/business-case/spec.md` §6.6 ("`capacity:bess` × de-rating (typed)"); `.scratch/ipp-renewables-bess/spec.md` (hints with source and year); G5 (price unit = currency per unit, no conversion in the model); owner decision HOLISTIC-1 (results trace to inputs).

## Repro

1. IPP template, solve, apply "IPP hybrid".
2. Capacity payment line: set price 40 (EUR per kW-year, the project currency). Run the business case.
3. Read the hint beside the price (`GET /results/business_case/preset?variant=ipp_hybrid`, `hint`).

## Actual

- The line is `linked:capacity.StorageUnit.bess@1` with no de-rating factor. 2030 capacity revenue is 1 289 622 = 32.2405 MW (the whole built battery) × 1000 × 40. A 2-hour battery in a capacity market is paid on a de-rated share of its power; the spec has a typed de-rating on this row.
- The hint is `GBP/kW_year 20–60 (2025)`, the project currency is EUR, and nothing on the row says so or converts. The data-centre lease hint is the same trap (`USD/kW_month 154–205`). A user who types 60 gets 60 EUR.

## Expected

A de-rating row (typed share, empty until the user enters it, with the market's range as a hint) multiplies the capacity quantity, as the spec says. Every hint shows its own currency and the project's, and says "not converted".

## Acceptance

- Preset row for the de-rating; capacity revenue = MW × de-rating × 1000 × price (hand check in the test).
- The hint text names its currency and, when it differs from the project's, says so in the row, in Guided and Expert.
