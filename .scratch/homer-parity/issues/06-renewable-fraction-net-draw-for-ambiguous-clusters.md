# 06 — Count an ambiguous conversion cluster by its net draw

Status: ready-for-agent

Follow-up from homer-parity 01a (#1424), GK B ruling 1 and R15.

## Problem

The renewable fraction (`services/solver/renewable_fraction.py`, R14) fails closed on an ambiguous conversion: an electricity→non-electrical Link whose outputs reach a sink (an active Load, or a Generator that withdraws, R16) and also have a path back to electricity. When such a Link draws, the KPI is None with a reason naming it as an "ambiguous conversion", and the LP row logs a warning and leaves the Link out.

The flagship H2 hub template (`eh_h2_hub`, `eh_h2_hub_staged`) has an electrolyser into an H2 bus with both an offtake and a fuel cell, so its renewable fraction reads None today, and the minimum-renewable-fraction row cannot see the electrolyser's draw.

## Ask

Count a cluster that has both a sink and a path back by its net draw: the electricity into the cluster minus the electricity it returns through non-source Links, per period. Do this in both the KPI (`renewable_fraction`) and the LP row (`_wrap_with_min_renewable_fraction`), so they agree.

## Notes

- A cluster is the non-electrical buses reached from the conversion Link (forward through Links, backward through Links that can run negative), as `_conversion` already walks it.
- Per period, not per snapshot: a store in the cluster shifts energy in time, so a per-snapshot net could go negative.
- When this lands, the R15 reason and the row warning for these Links go away; `test_an_h2_bus_with_an_offtake_and_a_fuel_cell_fails_closed` and the R16 negative-Generator tests change from "fails closed" to a counted net draw.
