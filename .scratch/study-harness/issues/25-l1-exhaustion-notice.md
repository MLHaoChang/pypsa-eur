<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 25 — Notify the user when an agent loop ends without a pass

Status: needs-triage
Type: task
Blocked by: 04
Gated: yes (GK B)
Model tier: sonnet
Owner decisions: SH-1, SH-ENVELOPE-1
Design: [design.md](../design.md) §6.1 "Exhaustion", §7 "Scope and audit" (`notify`)

## Why

Design §6.1 says that when L1 is exhausted, the user is notified with the failing rules and the change ledger. Issue 04 (#1072) ends the loop in state `needs_user`, and the run view shows the ledger, but no notice is sent. Sending one needs a channel, email or push, which #1072 does not add (coordinator ruling, 3 Nov).

## What to build

1. When an agent loop ends (`no_progress`, `exhausted`, `unverified_write`, `error`) and the envelope's `notify.email` is set, send one notice. It names the run, the failing rules with their facts, and the ledger rows (target, old → new, receipt).
2. Send it once per ending; a resumed loop's next ending is a new notice.
3. When the notice cannot be sent, the run still ends `needs_user`; the failure is logged and shown in the run view.

## Acceptance criteria

- [ ] An exhausted L1 with `notify.email` sends one notice carrying the failing rules and the ledger (tested with a fake sender).
- [ ] Without `notify.email`, nothing is sent.
- [ ] A sender failure leaves the run `needs_user`, with the failure in the run view.

## Comments
