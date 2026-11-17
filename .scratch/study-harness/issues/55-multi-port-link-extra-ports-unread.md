<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 55 — A multi-port Link's extra ports are left out of the mainland-feed check

Status: ready-for-agent
Gated: GK A
Type: bug
Parent: SH 48 (#1567) review follow-up

## Problem

`island_survival._mainland_links` reads only `bus0` and `bus1`. A Link whose `bus2` (or later port) sits on the
`eh_poc` bus is not read as a mainland feed, and a `grid_import` Link with a further port off the island is read
as a feed instead of a tie.

## Ask

Read every port, as the tie check does: a Link is a mainland feed only when all its ports are on the island's
buses, and an `eh_poc` match on any port names that port as the grid side. Blank ports are ignored.
