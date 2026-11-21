# 04c: load growth, malformed-input follow-ups

Status: ready-for-agent
Blocked by: 04 (04a, PR #1604, merged)

Two non-blocking items from GK B (64)'s OK on #1604 @cd32ad7f.

1. **The chat card raises on a malformed stored rate map.** `harness/edit_card.py` (the `update_solver_config` card and the time-axis tool cards) reads the stored `load_growth_pct_per_year` as a dict. A stored map that is not an object (a hand-edited or bundled `solver_config.json`) makes the card preview raise instead of showing the route's typed refusal. Fix: read a non-dict old map as `{}` on the card, so the card shows the change and the route's own refusal (`load_growth_invalid`) speaks. Test: the card for a rate change on a stored `[1]` / `"2"` map returns rows or the typed refusal, never an exception.
2. **A null or non-object rate map sent to the PUT gets pydantic's list-shaped 422.** `load_growth_pct_per_year: null` (or a list) in the request body is refused by the schema before the route's check, so `detail` is a list and the field shows nothing. Fix: accept any value at the schema (as the entries and the start year already do) and refuse a non-object map in `load_growth_problems` with `load_growth_invalid` in plain words. Tests: a route test for `null` and `[1]` asserting the typed kind and that nothing is stored.

Files: pypsa-gui/backend/harness/edit_card.py, models/schemas.py, services/solver/load_growth.py, tests/test_edit_card.py, tests/test_load_growth.py. GK B area; send GK B the plan.

## Comments
