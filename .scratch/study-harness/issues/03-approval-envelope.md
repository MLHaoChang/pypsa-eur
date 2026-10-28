<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — Approval envelope: approve once, run unattended inside it

Status: ready-for-agent
Type: task
Blocked by: 01
Gated: yes (GK B; UX for the card in 08)
Model tier: opus
Owner decisions: SH-ENVELOPE-1 (amends COP-1 for harness runs only), COP-1, SH-RESEARCH-1
Design: [design.md](../design.md) §6.0, §7
Plan verdict: GK B GO, 28 Oct (B2)

## What to build

1. `<task_id>.envelope.json` beside the task, the data model of design §7, with its own `audit` rows.
2. Proposal: the profile's `envelope` block → the intake's plan-review card detail; the user may narrow, never widen.
3. Grant: the plan-review card's "Approve and run unattended" verdict, recorded via `approvals.decided`; "Approve, step by step" grants nothing.
4. Admission in `harness/confirm.py`: on a run session only, a call that matches (tool listed, target and bounds, not `destructive`, native, envelope `granted`, unexpired, budget left, ACL/lock unchanged) goes the `policy` path with `approvals.decided(by="envelope:<id>")` and an audit `used` row; anything else takes the card path with an audit `refused` row. A write of a researched value never matches.
5. Expiry and revoke: `expires_at`, budget exhaustion, `cancel_task`, and a revoke from the plan card. The run pauses and notifies.

## Acceptance criteria

- [ ] A listed edit within bounds runs without a card; the approvals log says `by=envelope:<id>`.
- [ ] Out of bounds, unlisted tool, `destructive` tier, MCP/external, unknown tool → card path, audit `refused` with the reason.
- [ ] An ordinary chat session with a granted envelope on the same project still cards every write (COP-1 untouched).
- [ ] After `expires_at` the next call cards and the run is `paused`.
- [ ] If the approval record cannot be written, the call is denied (fail closed).
- [ ] Narrowing at grant is kept; a model-supplied envelope wider than the profile's is `invalid_envelope`.
- [ ] `brief_stale` or an ACL change after the grant → no envelope match.
- [ ] Refusal matrix, one test per row: destructive tier, delete, MCP/external, unknown tool, outside the entry, out of bounds, expired, revoked, ACL loss, wrong project, foreign drift.
- [ ] Bounds are absolute or relative to the value at grant: three rounds of +40 % with `rel: 0.5` are refused at the second round.
- [ ] Freshness chain: a user edit between two runner changes pauses the run; an unreadable fingerprint pauses it too.
- [ ] Grid-code publish is never admitted, even when listed.
- [ ] The grant is recorded as `approvals.decided(by="user")` with the envelope id; each admitted dispatch as `by="envelope:<id>"`.
- [ ] Tool-error kinds added here go in a `tool-error-kinds.d` fragment once #798 lands.
- [ ] Glob targets: `at_grant` is recorded per matched instance; an instance created after the grant (including by the run) gets a card; a glob never matches another component class.
- [ ] An edit entry without numeric `bounds` or an enum of allowed values (e.g. `carrier`, `bus`) is `invalid_envelope`.

## Likely files

`pypsa-gui/backend/harness/confirm.py`, `pypsa-gui/backend/harness/approvals.py`, `pypsa-gui/backend/services/run_envelope.py` (new), `pypsa-gui/backend/services/assistant_tasks.py`, `pypsa-gui/backend/tests/test_harness_envelope.py` (new)

## Not in scope

The envelope section on the card (08).

## Comments
