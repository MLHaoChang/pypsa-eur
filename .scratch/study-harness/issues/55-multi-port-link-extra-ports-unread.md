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

## Comments

**GK A (28) plan verdict, 2026-11-17: GO with conditions.** Issued under the earlier numbering "SH 50 + 51" and
renumbered by the coordinator to SH 54 (unmarked internal Links) and SH 55 (multi-port Link ports), with SH 56 the
zero-availability supply. GK A (29) confirmed: "The plan GO with C0–C5 stands. C0 is resolved." All six are built on
`claude/sh-54-55-tie-link-follow-ups`.

- **C0** Issue-number collision: confirm the numbers with the coordinator before filing. Resolved: 54/55/56.
- **C1** One island set, used by both the warning and the port check, pinned by a test with a Link between a unit bus
  and a non-unit island bus. Built: `island_buses()` equals the units' buses, so they are one set, and a test pins it.
- **C2** One port reader: `_ports(df, name)` replaces the inline busN read in `_ties` too, with no second copy. The
  pinned per-template tie_state table stays unchanged. Blank/NaN/"" ports are ignored in both callers.
- **C3** The warning lists at most 5 links, then "and N more" with the true total; `unmarked_links` carries the full
  list.
- **C4** "Can deliver" uses `_can_deliver` (size only), not `_carries`, so a normally-open unmarked internal Link still
  warns. Tested.
- **C5** An `eh_poc` match on bus0 or bus1 behaves exactly as in #1567. A 3-port case with bus2 `eh_poc` and the Link
  tagged `grid_import` names bus0 by the role and the `eh_poc` port as well, each with its own words
  (`test_a_grid_import_link_with_a_third_port_on_eh_poc_names_both_grid_sides`).
