Explore stage, eh_site study run (study harness §3 stage 6, §6.7).

The base case passed its result gate. Now propose what-if cases: grid
connection against BESS against genset. The harness runs each case on its
own fork; the base project is never changed.

- Call `propose_cases` once, with at most 4 cases. Each case changes at most
  2 things. A fifth case or a third change is refused, and nothing is
  admitted.
- Change only the levers listed below, each within its bounds. Give every
  change its value and its range {low, high, unit} with a source: the
  lever's own `profile:` source, or the id of a confirmed research range
  (`research:<row id>`) for that lever, whose low and high are exactly the
  row's. A range in another unit is refused, not converted.
- An investment question (engine `study`) goes through the decision study
  named below: carry its intake, and change only ledger keys, each citing
  `ledger:<key>` with that row's sourced range.
- Each case costs solves from the run's budget; `solves_left` says how many
  are left. Propose fewer cases rather than more when the budget is tight.
- Never run a solve or a sweep yourself, and never edit the project: the
  harness runs the admitted cases, and the user approves the sweep.
- When no case is worth exploring, propose none and say why in one sentence.
