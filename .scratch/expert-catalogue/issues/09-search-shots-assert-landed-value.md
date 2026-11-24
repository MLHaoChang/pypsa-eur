<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 catalogue-search-shots.mjs should assert the value that lands

Status: needs-triage
Source: GK B3 (6) carry-forward on #1804 (properties-blank-form 01 / expert-catalogue 02); its builder is no longer active. Non-blocking.

## What

`catalogue-search-shots.mjs` takes shots of a search-and-open flow but does not assert the value that lands in the field. Add the assertion: for `mainland_supply`, the field reads 100 MW after the flow.

Suggested: Sonnet, GK B3 review.
