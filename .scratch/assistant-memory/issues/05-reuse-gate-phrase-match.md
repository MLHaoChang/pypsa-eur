<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Follow-up: phrase-match the saved-value chat gate

Status: needs-triage
Blocked by: 03
Type: task

## Problem

`services/not_via_chat.py` sends a chat goal to the `memory_parameter_suggestions` entry ("Saving a field value for reuse, or applying a saved one: out of chat") through `_asks_for_a_saved_value`. The gate sees only the goal's word set, so it cannot tell a value kept for reuse from model time or plain workflow. GK B (46) found these false hits at #1347 head 9aa8b3e8:

- "Save the capacity and check the next time step"
- "Save the cost and solve it later"
- "Remember the price rises later in the year"
- "Remember that the capex is lower in another project"

These real asks miss (they get an ordinary tool search):

- "Save this capex so I can use it again"
- "Keep this value for next time"
- "Store this capex for future projects"
- "Save the capex as a default"

## What to do

Match phrases in the goal text, not single words: "for later", "for next time", "for my next project", "for another project", "to reuse", "so I can reuse", "so I can use it again". `matches()` takes the word set from `assistant_tools` (line ~178), so it needs the goal text as well.

## Done when

- The four false hits above are in `test_ordinary_goals_get_no_refusal` and miss.
- The misses above (or a chosen subset, with the rest named) hit.
- Every current refusal and ordinary goal in `tests/test_not_via_chat.py` keeps its result.
- Hand mutants of each phrase are killed.
