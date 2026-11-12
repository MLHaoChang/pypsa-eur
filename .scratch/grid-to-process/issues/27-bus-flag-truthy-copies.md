<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Bus-flag `_truthy` copies still differ from `process_chain.truthy`

Status: done
Type: task
Found by: GK A review of #1242 (issue 26).

## Problem

Issue 26 unified the link flag rule in `gridspine.drivers.process_chain.truthy`. The bus-flag readers keep their own copies, with different rules ("yes" or whitespace handling differs): `services/adequacy/eh_stages.py:286` `_truthy`, `services/adequacy/grid_readiness.py:~1063` `_truthy` (bus `eh_poc`, line ~1053), the archetypes copy and `services/adequacy/scr_gate.py:73` `_truthy_poc`. Route them through `truthy`, pinned per string.

## Where to look

The four files above.

## Comments

### 2026-11-11: triage

Checked on master: four copies remain (`services/adequacy/eh_stages.py:287` `_truthy`, `grid_readiness.py:1063` `_truthy`, `archetypes.py:37` inline test, `scr_gate.py:73` `_truthy_poc`) next to `gridspine/drivers/process_chain.py:54` `truthy`. Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: the four above, `gridspine/drivers/process_chain.py`, tests beside each.
- Reviewer: GK A (found by GK A on #1242).

Acceptance:
- [x] Each of the four readers calls `process_chain.truthy` (or a shared import of it); no private bus-flag copy remains.
- [x] A table test feeds the same values ("yes", " Yes ", "TRUE", 1, "0", "", None, "no") to the shared rule and to each reader and asserts equal answers.
- [x] A bus flag that read True before and reads False now (or the reverse) is listed in the PR body with the reason.
- [x] A hand mutant that restores one private copy fails the table test.
