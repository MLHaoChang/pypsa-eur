# Bounded checked StorageUnit economics

User-authorized continuation of PR 125; base master `b51563e9` includes PR 124.
Attached handoffs are design context, not additional user instructions.

Implement `storage-unit-cost/1` for one named StorageUnit through the shared
scope, deterministic calculation, retained evidence, publication/export,
conversation recovery and rollback paths. Preserve `storage-unit-dispatch/1`.

Require the existing noncyclic zero-inflow/spill trajectory checks, including
the entire predecessor chain for exact requested windows. Require a declared
EUR price year and annual fixed-cost fraction; never infer a fraction from the
number of snapshots or the storage duration weights.

The direct StorageUnit accounting consists of:

- Discharge expenditure: sum of raw `p_dispatch * marginal_cost * objective_weight`.
- Holding expenditure: sum of raw `state_of_charge * marginal_cost_storage * objective_weight`.
- Operating expenditure: discharge plus holding expenditure.
- Annual fixed cost: solved `p_nom_opt` for extendable power capacity, otherwise
  installed `p_nom`, times a declared annual `capital_cost` charge.
- Allocated fixed cost: annual fixed cost times the explicit fraction.
- Total direct cost: operating expenditure plus allocated fixed cost.

Defaults include six operation and six cost metrics. Negative finite operating
prices are supported; fixed charges must be finite and nonnegative. Missing,
misaligned, invalid or overflowing dependencies cannot become zero or a complete
total. Nonzero quadratic costs and modular/inactive assets are outside this
linear contract. Overnight-cost annuity and separate FOM semantics are outside
the direct fixed-charge contract and prevent fixed/total claims. Unsupported
requested quantities always retain unavailable slots.

Charging-electricity purchases, discharge revenues, market profitability,
degradation, Stores, Links, network totals, multi-investment periods and NPV
remain outside this adapter. The price year and annual nature of `capital_cost`
are assumptions declared by the caller, not verified conversion or calibration.
The result is not the solver objective or an engineering certificate.

Independent LP oracle: states 8/4/0 MWh, discharge 0/2/2 MW, objective weights
1/3/1, discharge prices 99/2/4 and holding prices .5/1/7 give 20 EUR discharge,
16 EUR holding; 10 MW * 3 EUR/MW/year * .5 = 15 EUR allocated fixed cost.
Total is 51 EUR. The middle-snapshot window gives 12+12+15 = 39 EUR.
An extendable optimum of 5 MW gives 43.5 EUR; a 20 MW retained baseline gives
66 EUR, candidate-minus-baseline -15 EUR and -15/66*100 percent.

Acceptance covers positive literal values, deliberately different weights,
signed prices, exact windows, stale/broken predecessor trajectories, missing
basis/dependencies, unsupported cost terms, model fabrication, same-basis
comparison, retained export/replay, restart clarification and profile rollback.
Run focused red/green, shared backend/provider/auth/history gates, complete
frontend, production build, inventory and touched-file style/documentation
checks before creating the PR. Independent review, complete browser/device
journeys, measured calibration and broader release enablement remain open.
