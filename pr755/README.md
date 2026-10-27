# PR #755 (DC 15) UX evidence

Demo project from the `eh_datacenter` template; limits and quotes are generic test values.

- `after/01-extracted-unconfirmed-expert.png`: the unit curve and deadband as extracted, long quote, Confirm buttons.
- `after/02-confirmed-expert.png`: both confirmed (tag `code`).
- `after/03-blank-unset-expert.png`: a blank draft. Both limits are unset, so neither row shows.
- `after/04-results-{expert,guided}.png`: a campus run with the deadband set lists it under "Needs an RMS study".
- `before/` (master 670ab06): the same extracted draft has neither row, because master drops both keys, and the results have no RMS line.

Guided has no grid-code draft view of its own: it opens the Expert section (`openSectionInExpert`), the same on master. So the draft shots are Expert only, and Guided's DC 15 view is the results row.

Scripts: `scripts/` (setup_demo.py, run_study.py, shoot.mjs, shoot_results.mjs).
