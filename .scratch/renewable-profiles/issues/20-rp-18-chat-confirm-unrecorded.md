<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-20 — should chat confirm unrecorded series?

Status: needs-triage
Type: task
Raised by: RP-18 (`18-rp-14-old-projects-native-series-unrecorded.md`), GK B condition R3.

RP-18 added `POST /api/projects/{name}/profiles/unrecorded/confirm`: the user says a series with no provenance
record is the network's own (writes only the record, one undo). Chat has no tool for it today; the route is
declared held by this issue, and chat points to Time Series, Renewables.

To decide: build a chat tool for it? If so it is carded with the user deciding, never auto-approved, and
lists every series it stamps with their digests (RP-18 R3, R4).

## Comments
