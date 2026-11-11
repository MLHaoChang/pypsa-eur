<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-20 — should chat confirm unrecorded series?

Status: ready-for-agent (owner decision RP20-CHAT-1; GK B plan first)
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
