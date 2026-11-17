<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 54 — An untagged internal Link is read as the island's own feeder without a word

Status: ready-for-agent
Gated: GK A
Type: bug
Parent: SH 48 (#1567) review follow-up

## Problem

#1567 decided that a Link with both ends on the island's buses and no marker (no `eh_role = grid_import`, no end
on an `eh_poc` bus) is the island's own feeder: topology cannot tell it from a mainland feed, and four shipped
templates rely on it. When such a Link really is the grid feed, `tie_state` reads `no_tie` and nothing says why.

## Ask

Keep the decision, but never silently: when the tie reads `no_tie` and such a Link can deliver, name each one and
say how to mark a grid feed. The island-tie gate row carries it as detail. No shipped template changes its answer.
