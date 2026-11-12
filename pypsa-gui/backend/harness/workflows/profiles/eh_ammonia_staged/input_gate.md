Input loop L1, staged ammonia study run (study harness §6.1).

The run's input gate failed. The rules that failed, with the facts they read,
and the next tools `project_readiness` names follow below. Fix the inputs so
those rules pass.

- Change only what the failed rules name: their tunable attributes, or a data
  gap listed below. The NH3 delivery, the process load and the process
  units (the Haber–Bosch loop) are the user's inputs: never change or
  reshape them.
- Fill a gap, never replace a value that is there, and only from the source
  listed for it under "Data gaps you may fill". Never pick a number of your
  own; a call that does not copy the listed source is refused.
- Never delete a component, and never change the objective, the horizon, a
  gate rule, the grid connection, the demand or the solver settings.
- Every write goes through the run's approval envelope. A write it does not
  cover is refused and counted; do not retry it, and do not ask the user here.
- Read before you write: name the component and value you change and why.
- When nothing within these limits fixes a rule, stop and say which rule and
  what data the user must supply.
