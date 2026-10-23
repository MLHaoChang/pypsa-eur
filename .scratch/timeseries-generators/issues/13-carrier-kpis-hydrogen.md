# Carrier KPIs: hydrogen is not renewable

Status: ready-for-agent (outsourced: H)
Type: task
Blocked by: 11 (#676)
Gated: yes (G1-G4 in the brief)
Model tier: opus

This issue is the sibling of issue 11. It was noted in task F's brief as out of scope.

## Gap

`services/results/carrier_kpis.py::_is_renewable_carrier` tests `'hydro' in carrier`, so a hydrogen Generator counts as renewable. Its unused capacity then shows as curtailment in Load Flow's Carrier KPIs table, often above 50 % and in the warning colour. The CSV export and the chat assistant's `carrier_kpis` numbers show the same figure. The table is keyed by carrier `nice_name`, so this also hits any `H2` Generator, whose catalogue nice name is "Hydrogen".

## What to build

Blank hydrogen words before the renewable substring test, the same way as F's `_gen_category`. Pin the two rules together with a test. Brief: `.scratch/timeseries-generators/outsource/H-carrier-kpis-hydrogen.md`.

## Done when

A test shows that a `hydrogen` Generator and an `H2` Generator with nice name "Hydrogen" report 0 curtailment, that `hydro` and `solar` keep theirs, and that the router/service seam tests still pass.

## Follow-ups (not H)

- `services/results/curtailment.py` `RENEW_KEYWORDS` has the same `hydro` substring (Curtailment, Dispatch and by-cause).
- PyPSA-Eur nice names `Run of River` and `Reservoir & Dam` get no curtailment figure in Carrier KPIs.
