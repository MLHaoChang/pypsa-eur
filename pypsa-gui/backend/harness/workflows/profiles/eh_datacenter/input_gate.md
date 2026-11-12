Input loop L1, data centre study run (study harness §6.1).

The run's input gate failed. The rules that failed, with the facts they read,
and the next tools `project_readiness` names follow below. Fix the inputs so
those rules pass.

- Change only what the failed rules name: their tunable attributes, or a data
  gap listed below. The data centre's load is the user's demand: never change
  or reshape it.
- The grid frequency and the reliability goal are the user's own, set on the
  run's card: never change them. When the grid-code frequency rule fails,
  make no change and say which frequency the user must confirm.
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
