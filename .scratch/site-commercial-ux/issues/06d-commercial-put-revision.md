<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06d A commercial PUT to /solver_config carries no revision

Status: needs-triage
Source: GK B4 (4) residual on #1938 at 85e37644 (not blocking).
Review: GK B.

## Problem

`commercialApi.saveCommercial` does GET → merge → PUT `/simulation/solver_config` with no revision. A save whose window spans a market-pack apply (or any other commercial edit) silently reverts it. Hand entry on master behaves the same way, and K8a deviation 2 accepted "exactly as hand entry". finance, business_case and value_flows already carry a revision or digest.

## Done when

The commercial part of the PUT carries a revision (or `If-Match`) that the server checks under the lock, refusing a stale save with a typed 409. The card saves (prices, no-backup list, pack apply) send it, with backend and frontend tests.
