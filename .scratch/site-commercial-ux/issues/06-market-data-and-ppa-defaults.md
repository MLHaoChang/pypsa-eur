<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 No market data: historical prices, PPA defaults per contract form and market bundles are unbuilt (S1, S2, K8)

Status: ready-for-agent
Owner: SCU06-PLAN-1.
Blocked by: none for S1/S2; K8 needs its own gated plan (with DC-06, one reserve model)
Design: `.scratch/project-workflow/integration-sensitivities.md` §1, §2 and §6; commercial-finance.md §7 row K8 (Q8 = A).
Source: gap review 2026-11-21, design map.
Model: opus. Review: GK A/B + UX.

## Problem

Prices come only from the pinned seed series (`ipp-renewables-bess/08`). The design promises:
- historical day-ahead prices from ENTSO-E, with the user's own token and the bidding zone taken from the site pin (S1);
- PPA defaults per contract form, such as pay-as-produced and baseload (S2);
- market bundles per country with reserve products (K8, `MarketPack`).

None is filed.

## User-visible impact

An IPP or BESS business case runs on one seed price year. The user cannot pick their zone's real price history.

## Question for the owner

1. **(Recommended) S1 first**: the ENTSO-E fetch with the user's token (stored like the API key), zone from the pin, cached per year, and a plain refusal with no token. S2 next. K8 stays a plan.
2. S1 + S2 + a K8 plan as one stack.
3. Not now: the seed prices are enough.

## Acceptance criteria (S1)

- [ ] With a token set, section 7 offers "Use <zone> day-ahead prices for <year>". The series lands as a time series with its source and fetch date. Without a token, the reason is shown.
- [ ] The fetch is mocked in CI, and one live probe is documented. A wrong token gives a plain error.
- [ ] End to end in the real app: on the IPP template in Germany, fetch DE-LU 2024 (or the mocked fixture in CI), run, and see the price series named in Results › Costs & prices and in the report's assumptions.

## Likely files

new `pypsa-gui/backend/services/market_prices.py`, the time-series routes, section 7 price card, app secrets.

## Comments
