Input loop L1, eh_site study run (study harness §6.1).

The run's input gate failed. The rules that failed, with the facts they read,
and the next tools `project_readiness` names follow below. Fix the inputs so
those rules pass.

- Change only what the failed rules name: their tunable attributes, or a data
  gap (a load with no demand series, a missing tariff or weather choice).
- Fill a gap, never replace a value that is there, and only from the source
  listed for it under "Data gaps you may fill": copy its archetype, year and
  params, its weather choice, or the tariff it names. Never pick a number of your own; a call that does not
  copy the listed source is refused. Apply a demand series only from the
  pending profile you generated in this round.
- Never delete a component, and never change the objective, the horizon, a
  gate rule, a grid-code limit, the demand or the solver settings.
- Every write goes through the run's approval envelope. A write it does not
  cover is refused and counted; do not retry it, and do not ask the user here.
- Read before you write: name the component and value you change and why.
- When nothing within these limits fixes a rule, stop and say which rule and
  what data the user must supply.
