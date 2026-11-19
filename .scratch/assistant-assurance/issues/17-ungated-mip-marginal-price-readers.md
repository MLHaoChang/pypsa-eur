<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Readers outside checked answers still show a MIP's 0.0 bus prices

Status: needs-triage
Type: bug
Found by: GK B (65) on #1680 (issue 10, S2).

## Problem

On an integer (MIP) solve, HiGHS returns no duals, and PyPSA writes 0.0 to
`buses_t.marginal_price`. S2 (#1680) marks those prices unavailable
(`mip_duals_not_marginal`) in checked answers and in the checked bus-price
chart. Every other reader still shows the 0.0 as a price: the Results price
views, Compare, reports, headline KPIs, LCOH and `prices_digest`.

## Acceptance

- Each reader of `buses_t.marginal_price` either reads the solve kind through
  `harness/solve_validity.classify` (or a shared service equivalent) and
  shows "not available on an integer solve", or is listed with a reason why it
  is safe.
- A committable-unit test per reader shows that no 0.0 is presented as a price.

## Comments
