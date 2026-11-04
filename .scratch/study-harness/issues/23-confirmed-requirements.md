<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 23 — Typed, user-confirmed requirements enforced on assistant edits

Status: done (landed in #1079)
Type: task
Blocked by: 04 (the agent loop whose edits it checks); LV-09's durable conversation (landed: #1034, #1049, #1051)
Gated: yes (GK B)
Model tier: opus
Owner decisions: HARNESS-970-1, SH-FOLD-1, SH-ENVELOPE-1, AI-567b
Folded from: draft PR #970, HH-02 (`.scratch/harness-reliability-upgrade/issues/02-persistent-requirements.md`)
Design: [design.md](../design.md) §6.0 (what the agent never changes), §7

## What to build

A requirement the user states and confirms ("keep the model horizon", "the BESS energy capacity must be at most 20 MWh") survives message trimming, a provider switch and a restart, and the server refuses an assistant edit that breaks it.

1. **Store on LV-09's parent thread.** `services/durable_conversation.py::_empty_thread` already holds `goals` and `constraints` (untyped strings, `_view` and `checkpoint_thread` carry them). Add typed requirement records next to them, in the same file under the same generation and CAS rules; no second store, memory service or sidebar. The untyped `constraints` strings stay readable and are never promoted to a blocking predicate.
2. **Closed predicates** (extra fields refused, versioned): `preserve_horizon` (the snapshot index and weights, by server-captured digest) and `scalar_bound` (`eq`/`ge`/`le`/`within` on one exact component, field and unit). #970 bound `Generator.p_nom` only. For eh_site@1 the bounded fields are its tunables (`Generator.p_nom_max`, `StorageUnit.p_nom_max`, `StorageUnit.max_hours`, `run_profiles.py:186-206`); the unit comes from the native field spec, never a guess. `advisory_note` stays informational.
3. **Confirm through the existing Choice / confirmation card**, with the value and unit shown. No auto-activation of a model-proposed requirement; a contradictory or unit-less one asks again.
4. **Enforce at dispatch.** Before an edit's card or the envelope's `admit`, the server evaluates the requirement against the edit's native effect (the edit card's old→new rows). A violating edit is refused with a typed error and counted as a refusal in the run's budget; inside an envelope it falls back to a card, never a silent write. The model sees the requirements as per-turn context (logged, not in the cached prompt).
5. **A study run inherits them.** The brief records the requirement revision at approval; a requirement changed mid-run pauses the run `brief_stale` like any other brief change.

## Acceptance criteria

- [ ] **Failing test first:** a confirmed `scalar_bound` on `StorageUnit.max_hours`, two trims, a provider switch and a restart; the assistant then proposes a value above the bound and the edit is refused with no write.
- [ ] The same edit inside a granted eh_site@1 envelope is not run unattended; it gets a card and counts as a refusal.
- [ ] `preserve_horizon`: a snapshot change from the assistant is refused; a manual UI edit is not blocked, but it marks the requirement's last evaluation stale.
- [ ] An unconfirmed or unit-less proposal never becomes active.
- [ ] Existing `goals` / `constraints` tests (`test_durable_conversation_*`) pass unchanged.
- [ ] Not an edit lock: a requirement never blocks a manual edit and never authorizes a tool.

## Likely files

`pypsa-gui/backend/services/durable_conversation.py`, `pypsa-gui/backend/harness/confirm.py`, `pypsa-gui/backend/harness/edit_card.py`, `pypsa-gui/backend/harness/compose.py` (per-turn context), `pypsa-gui/backend/services/assistant_tasks.py` (brief revision), `pypsa-gui/backend/tests/test_durable_conversation_requirements.py` (new)

GK B note (3 Nov): the check must cover bulk and preview writers too, not only single-edit calls.

## Not in scope

Predicates on solver outputs, finance or reliability (a requirement on `p_nom_opt` is not a bound on configured capacity). A requirements panel. Issue 22 (the lock-level check) and 24.

## Corrections to the #970 text

#970 builds a parent schema before LV-09 landed and does not mention the `budgets` block LV-09 added to the same thread (`reserve_budget` / `settle_budget`); share one write path and one `checkpoint_rev` with it. Its "HH-01 must come first" dependency is dropped: this issue needs only the agent loop (04), because the edit check runs at dispatch, before 22's lock-level check.

## Comments

**GK B plan verdict (4 Nov): GO WITH CHANGES.** Interactive violation: refused with no card (`requirement_violated`); run violation: card path, counted as a refusal, never a silent write. Conditions, checked in the PR:

1. Every writer of `p_nom` / `p_nom_max` / `max_hours` on Generator / StorageUnit is listed in the PR body (execution-tier, workflow, optimised-capacity, undo/restore included), each shown to emit card rows `evaluate` checks or to be refused while a bound covers its target. Fail closed: a `complete: False` card or a row-less write touching a bounded component is a violation. One non-`update_component` writer is tested.
2. preserve_horizon's tool set is listed the same way (writers of snapshots, snapshot weightings, investment-period weightings, incl. aggregation/clustering, templates, restore); a writer left off is a bypass.
3. A `scalar_bound` binds to the component's identity (`services/component_identity`): a rename keeps it, rename-then-edit is refused (tested), a deleted target marks it stale, never dropped.
4. Retire/replace goes through the same confirmed card; the model never retires alone; an envelope never admits `set_requirement`; a denied retire is tested.
5. Only activate/retire bump the rev `_stale` compares; `mark_evaluation` never pauses a run (tested).
6. `harness/loop.py` overlaps SH-22's `run_in_worker` seam; tell that builder which functions this PR touches.
7. The per-turn addendum frames requirements as data, never as instructions; logged, out of the cached system prompt.
8. Test 1 written first and shown red on master in the PR body; run the durable-conversation, edit-card, run-loops, route-policy, route-parity and control-matrix checks.
