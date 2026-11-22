<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Slack grid strength: minimum never above maximum

Status: ready-for-agent
Type: task
Gated: yes (GK B)
Depends on: DGS-EXTRAS-1 (claude/dgs-extras-fields); lands with 01a-3 (ranges and one validator in every write path).

## What

A slack Generator's `sk_min_mva` must not exceed `sk_max_mva`. This is a cross-field check, so the single-field type checks added in DGS-EXTRAS-1 can't make it (GK B X8). `rx_min` and `rx_max` are the R/X at the weakest and strongest grid, not a pair with an order, so they get no such check.

## Acceptance

- Every write path refuses an edit that leaves `sk_min_mva > sk_max_mva` on one generator, with a typed 422: "Minimum short-circuit power: it is above the maximum short-circuit power (… MVA)." This covers POST, PUT, bulk/preview, the chat edit tool and experiment copy.
- An edit that sets only one of the two is checked against the stored other. A blank on either side skips the check.
- Imports (DGS, legacy, PSS/E) load a reversed pair and flag it in readiness; they never refuse it or swap the values (C9).
- One test per path, and one mutant per path that skips the check, killed by that path's test (C12).

## Comments
