<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-20 — should chat confirm unrecorded series?

Status: done (RP20-CHAT-1: `confirm_unrecorded_series`, carded; #1396)
Type: task
Raised by: RP-18 (`18-rp-14-old-projects-native-series-unrecorded.md`), GK B condition R3.

RP-18 added `POST /api/projects/{name}/profiles/unrecorded/confirm`: the user says a series with no provenance
record is the network's own (writes only the record, one undo). Chat has no tool for it today; the route is
declared held by this issue, and chat points to Time Series, Renewables.

To decide: build a chat tool for it? If so it is carded with the user deciding, never auto-approved, and
lists every series it stamps with their digests (RP-18 R3, R4).

## Comments

### 2026-11-11: triage

Checked on master: `POST /api/projects/{name}/profiles/unrecorded/confirm` exists (`routers/profiles.py`), is listed in `services/not_via_chat.py`, and chat has no tool for it. Verdict: needs-info.

Exact question, sent to the owner through the coordinator: build a chat tool that confirms unrecorded series (carded, the user deciding, every series and digest listed), or keep it route-only and wontfix this issue?

### 2026-11-11: owner decision RP20-CHAT-1

The owner chose "Yes, chat tool with a card": build a chat tool that confirms unrecorded series. It is carded, the user decides, it is never auto-approved, and the card lists every series it stamps with its digest (RP-18 R3, R4). GK B reviews a plan first. The route leaves `services/not_via_chat.py` in the same PR.

### 2026-11-11: GK B plan GO, conditions C1–C10

Plan: a write-tier tool `confirm_unrecorded_series` with the route's input; the body shared with the route in `services/profile_gen/unrecorded.py`; in `CARD_ONLY_TOOLS`; edit-card adapter listing every series and digest; route out of `not_via_chat.py`. GO on these conditions:

- C1 one predicate: `readiness.profile_kind(...) == "unrecorded"` decides in the service, the precheck and the card; a malformed (non-dict) meta is not confirmable, never a 500. Test through the route and the tool.
- C2 no check-then-act: the service re-reads kinds and digests and writes in one critical section under the profile writers' lock. Test an edit landing between card and write; show it red with the service digest check removed (mutate each layer, then both).
- C3 the approval binds the exact item set: item added, removed or a digest changed after the card each refuse with nothing written. Duplicates and an empty list are refused at precheck, each with a test.
- C4 same gates as the route: edit lock held by another user, `project_not_open`. Tests through the tool.
- C5 never admitted without the card: every auto-approve tier, policy, task, change set and run-profile or envelope grant. Probe `_refusal` with the grant.
- C6 no cap: about 250 series on the card and in the result; counts come from the true total.
- C7 one `undo_last` restores the empty meta; exactly one snapshot is pushed per approval.
- C8 values byte-equal before and after; state whether the network revision or input fingerprint moves.
- C9 route unchanged apart from the new duplicate refusal; master's route tests run unchanged.
- C10 the old goal phrase reaches the tool; no other not_via_chat entry claims it; policy suites, inventory, route parity and control matrix checks pass at the trial merge. Mutants on the service guards and the lock.
