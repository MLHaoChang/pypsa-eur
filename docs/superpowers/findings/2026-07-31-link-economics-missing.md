# Converters had no economics row — and why the costs read as zero

**Date:** 2026-07-31
**Reported by:** the user — "check the 4_nodes_system why the economics for
electrolyzers are all 0 or missing"
**Status:** FIXED. Both halves were real and independent.

## "Missing": the endpoint had no concept of a Link

`/api/results/asset_economics` returned exactly three collections. Measured
against the user's saved project:

```
TOP-LEVEL KEYS: ['currency', 'is_multi_period', 'periods',
                 'generators', 'storage_units', 'stores']
LINKS KEY PRESENT? False
```

`Economics.tsx` mirrored it, mapping those three and nothing else. An
electrolyser is a Link, so it had no row whatever its costs — and the hole hid
heat pumps and every other P2X converter with it.

Now there is a `links` collection and a `Converters` group in the table.

## "Zero": two separate data facts, only one of them a problem

**`capital_cost = 0` is correct and not the bug.** The user entered
`overnight_cost = €1,500,000/MW`, which is the right field; PyPSA derives the
annualised figure from it through `annuity(discount_rate, lifetime)`. The trap
is that `discount_rate` is **NaN in the saved network** — it lives in
`solver_config.json` and is only applied transiently, by
`with_periodized_cost_defaults` / `periodized_capital_costs`, around each
calculation.

So the annualised cost never exists on disk. Any caller that reads the raw
column gets 0, and raw `n.statistics()` returns **NaN** — measured:

```
capex()                     2027    2028    2029
Generator  Solar PV          NaN     NaN     NaN
Generator  gas               NaN     NaN     NaN
Line       AC          2.689e+09  ...     ...      <- capital_cost set directly
Link       Hydrogen          NaN     NaN     NaN
```

Injecting `discount_rate=0.07` turns every NaN into a real number. Lines
compute only because they carry `capital_cost` directly and no
`overnight_cost`. **Any new endpoint that reads capital cost must go through
one of those two helpers**; forgetting is silent and looks like "the user set
no cost".

**`marginal_cost = 0` was a genuine loss.** The parent `3_nodes_system` has
`10.0` on the same `Electrolyzer 1`. Cause not determined: the partial-PUT wipe
that used to do this is fixed (`_update_component` merges,
`update_link` sends `exclude_unset=True`) and that fix landed 2026-07-25, the
same day the project was created — so the history is inconclusive. Restored to
10.0 at the user's instruction.

Worth noting for the next investigation of this shape: `links_marginal_cost`
was **absent from the netCDF entirely**, because PyPSA omits all-default
columns and 0.0 is the default. A grep of the file for the attribute finds
nothing whether it was never set or deliberately zeroed — the same
"default indistinguishable from deliberate" trap as `(0,0)` bus coordinates
and `co2_emissions = 0`.

## Design decisions (user's call)

**Revenue is NET of the energy bought.** A Link buys at bus0 and sells at bus1,
so `revenue_eur = gross_revenue_eur - input_cost_eur` and `net_profit_eur`
means what it means for a generator. The gross halves stay on the row so the
netting is auditable. In the table they map onto the storage columns —
`revenue_eur` ← gross, `charge_cost_eur` ← input — because storage is already
two-sided and `sumGroup` already aggregates `(fixed + vom + charge_cost)`.

**The unit cost is ALL-IN.** First implementation used `(fixed + vom) / output`
and produced **€43.74/MWh** against the LCOH panel's **€246.02** for the same
asset. Two views of one converter disagreeing by 5.6x is worse than either
number alone. The energy bought belongs in the numerator, and the two now
agree term for term on the user's data:

```
                          Economics tab        LCOH panel
H2 output MWh                 2,764,630         2,764,630
electricity cost EUR        598,716,546       598,716,546
VOM EUR                      39,494,717        39,494,717
CAPEX EUR (horizon)          81,430,197        81,430,197
unit cost EUR/MWh                260.30            260.30
```

**A missing bus price does not drop the row.** The generator block `continue`s
when the bus has no dual. For a Link that would reproduce the very bug being
fixed — an H₂ or heat bus often carries no meaningful dual, and the asset would
silently vanish again. Absent price is treated as zero and the row still
reports capacity, energy and cost.

**Multi-output ports are counted.** bus2/bus3/bus4 each valued at their own bus
price. Counting only bus1 would drop half a CHP's product and inflate its unit
cost by the same factor. Mutation-tested: disabling the port loop fails
`test_a_multi_output_link_counts_every_port_it_delivers_to`.

## Left for the user

The dispatch on disk was optimised with `marginal_cost = 0`, so the €39.5M VOM
is applied to a solve that never saw that cost. The figure is arithmetically
right but the LP would have chosen differently — the electrolyser was built to
210.88 MW partly *because* running it was free. **Re-solve** for a
self-consistent answer.
