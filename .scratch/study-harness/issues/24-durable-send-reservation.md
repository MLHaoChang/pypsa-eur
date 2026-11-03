<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 24 — A durable reservation before each model send, settled across restarts

Status: needs-triage (waits for 04 and for #1059 to merge)
Type: task
Blocked by: 04; PR #1059 (LC-07 PR2, in review; it changes files under `pypsa-gui/backend/harness/`, so build after it merges)
Gated: yes (GK B: persistence and failure semantics)
Model tier: opus
Owner decisions: HARNESS-970-1, SH-FOLD-1, SH-RESEARCH-CAP-1, AI-558
Folded from: draft PR #970, HH-03A (`.scratch/harness-reliability-upgrade/issues/03-durable-call-budgets.md`)
Design: [design.md](../design.md) §6.5

## What to build

Today a harness run's model cost is charged after the call (issue 02's total cost cap, `harness/runner.py`; issue 04's `run.budget.used` from `harness/budget.py`). A crash between the provider call and the charge, or a restart that forgets an in-flight call, can spend past a cap. Make admission durable:

1. **Reserve before the send.** Before each model call of a harness run (agent turns from 04, the reviewer, research from 09), the server writes a reservation (an upper bound in the cap's unit) and a dispatch-intent record; it admits only if `settled + open holds + this hold <= cap`. After the call it settles to actual usage; a lost call leaves its hold until a restart reconciliation settles it as `uncertain`, which counts at the hold's bound, never as zero.
2. **Reuse, do not rebuild.** LV-09 already keeps `reserve_budget` / `settle_budget` (`services/durable_conversation.py:631`, `:791`) with `budgets.reservations` / `final_charges` on the parent thread, idempotent by key. A harness run reserves under its own key (`run:<task_id>:<call>`) through those functions. `harness/budget.py` supplies the upper bounds (output limit, tool-call limit, `TaskCap`); the existing caps (`_turn_budget_block`, session and daily limits, `TaskCap`) stay as independent AND conditions and are not charged a second time.
3. **One mode switch, off by default.** #970's `PYPSA_GUI_CHAT_DURABLE_BUDGET_MODE` (`off`, `output_tokens`, `total_tokens`, `money_eur`) is the proposed shape; a missing price or unbounded input refuses monetary admission instead of assuming a number. Never call an estimate a hard cap.
4. **Research keeps its own cap.** SH-RESEARCH-CAP-1 (€2 default per run, raisable on the envelope to a €10 hard cap) is a separate scope in the same ledger: a reservation counts against both the run's total and the research cap, and this issue must not lower the cap, raise the €10 limit, or block a research step that fits under it.
5. **Out of the first slice** (as in #970): STT/TTS, provider probes, model sub-calls inside tools, and legacy report generation.

## Acceptance criteria

- [ ] **Failing test first:** kill the process between the provider call and the charge (fake provider); on restart the run is `needs_user` and the call's hold still counts toward the cap.
- [ ] Two concurrent sends in one run cannot together exceed the cap: the second is refused, and the refusal is the existing cap error.
- [ ] A retried send under the same key reserves once.
- [ ] A research step that fits under €2 (and one raised to €10) is admitted with the mode on; one over the cap stops mid-stage as in issue 09.
- [ ] With the mode `off`, behaviour and tests are unchanged.
- [ ] A provider reply with no usage settles at the hold's bound, never zero.

## Likely files

`pypsa-gui/backend/services/durable_conversation.py`, `pypsa-gui/backend/harness/budget.py`, `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/harness/providers/`, `pypsa-gui/backend/tests/test_harness_budget_reservation.py` (new)

## Not in scope

#970's HH-03B (a shared broker for HH-06–09 stages): one reservation path is enough for this plan. Media spend. Raising any existing cap.

## Corrections to the #970 text

#970's "Proposed v2 parent budget schema" predates LV-09; the live schema is `budgets = {reservations, final_charges, cached_usage}`. Extend it only where a field is missing, and migrate nothing that exists. It also says runner cost gates are "unmetered"; issue 02's cost cap is built and its test stays green.

## Comments
