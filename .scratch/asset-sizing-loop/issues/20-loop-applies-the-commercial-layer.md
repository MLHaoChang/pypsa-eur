<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-20 — The sizing loop and intent comparison apply the commercial layer per solve

Status: needs-triage
Type: task
Blocked by: none
Reviewer: GK A

Spec: [spec.md](../spec.md). Filed from #1711 (owner ruling, option 3).

## Problem

A project solve applies the commercial layer at solve time and undoes it afterwards
(`solver_service.apply_commercial_for_solve` → `lp_bindings.materialise_poc_prices`: a tariff's
energy items add EUR/MWh to the import links' marginal cost; contracts and the connection agreement
act too). The sizing loop and the intent comparison never apply it, so `sizing_loop_runner.refusals()`
refuses any project whose `commercial` holds a price-affecting key (allowlist
`COMMERCIAL_NO_LP_KEYS`: poc_link, site_party, timezone, value_flows).

Consequence, shipped in #1711: the single-year data-centre template carries no network tariff, and
the DC architecture comparison on a new DC project has no assumed default energy price: the user
enters one (`dc_architecture_no_energy_price`).

## Wanted

The loop and the intent comparison apply the same commercial layer per solve (with undo, and
`refuse_windowed_terms` as the project solve does), so a tariff-only block can be admitted and the
single-year DC template can ship its tariff again (and the architecture comparison its assumed
default price).

## Acceptance

- Parity test: on the DC template with its tariff, the loop's solve equals the project solve's
  objective and import cost.
- `refusals()` still refuses what the loop cannot reproduce; the allowlist test names each key.
- The plan goes to GK A before any code.
