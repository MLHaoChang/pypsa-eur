---
constant: _NEXT_STEP_RUBRIC
trailing_space: [facts]
---

Suggest-next-step rubric (#5). Same shape as solver_error_decoder: `full` is the pre-split literal, facts and chaining are the tools-off halves (word-multiset-equal).

## full

Suggesting next steps. Before recommending anything, read get_meta and
get_solver_config to ground the advice in the actual setup. Rubric: if
foresight is overnight but the user wants a multi-year pathway, explain the
myopic vs perfect tradeoff; if the bus_count is high and solves are slow,
suggest clustering to fewer nodes; if no CO2 GlobalConstraint is present,
suggest adding a CO2 cap to study decarbonisation; if the model is
electricity-only, mention that sector coupling (heat / H2 / transport) is
available. Only suggest steps the current configuration supports.

## facts

Suggesting next steps. Rubric: if foresight is overnight but the user wants
a multi-year pathway, explain the myopic vs perfect tradeoff; if the
bus_count is high and solves are slow, suggest clustering to fewer nodes; if
no CO2 GlobalConstraint is present, suggest adding a CO2 cap to study
decarbonisation; if the model is electricity-only, mention that sector
coupling (heat / H2 / transport) is available. Only suggest steps the
current configuration supports.

## chaining

Before recommending anything, read get_meta and get_solver_config to ground
the advice in the actual setup.
