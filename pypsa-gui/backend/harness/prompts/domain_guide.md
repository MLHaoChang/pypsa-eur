---
constant: _DOMAIN_GUIDE
trailing_space: [facts]
---

Domain-intelligence guide (#1): definitions, plausible ranges, foresight modes; chaining names the result tools. `full` = facts + chaining.

## facts

Domain knowledge — interpret results, do not recompute from raw tables.
capacity factor = time-average of p / (p_nom * p_max_pu); curtailment =
available VRE energy minus dispatched VRE energy. LCOE / LCOH = annualised
CAPEX (in €/yr) divided by delivered energy — WARNING: fleet aggregation
mixes single-year CAPEX with horizon-total OPEX across investment periods,
so a fleet LCOE/LCOH can read low by a horizon-length factor; treat fleet
values as approximate and prefer per-asset numbers. market value =
revenue-weighted average price a generator captures. CO2 shadow price = dual
of the CO2 GlobalConstraint (€/t; a tighter cap raises it). Plausible ranges
(flag values far outside as suspect): onshore/offshore wind capacity factor
~0.2–0.45, solar PV ~0.1–0.25, LCOE ~€30–150/MWh, CO2 price ~€0–300/t.
Foresight modes: overnight = one target year solved in perfect hindsight;
myopic = rolling year-by-year with no lookahead; perfect = all years
co-optimised with full foresight.

## chaining

Multi-period quirk: n.statistics() puts (metric, period) in the COLUMNS, not
the rows, and the horizon total needs investment_period_weightings applied —
so to read per-period results use the by_period field from get_results,
never re-sum the raw statistics columns yourself. To interpret a solved
network, CHAIN get_results carrier_kpis + get_results cost_breakdown +
get_results emissions and reconcile the three before narrating. Time-series:
NEVER paste full-year hourly CSVs (~8760 rows) into upload_timeseries /
upload_load_profile / upload_generator_profile — that blows the turn output
budget and freezes the chat UI with no tool progress. For synthetic
exemplary year profiles call generate_exemplary_timeseries (load_daily for
loads p_set, pv_solar for generators p_max_pu). Only use upload_* when the
user supplied a real file or a short series.
