<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06a EMT pack: a 'connected' channel once GC-04 bumps the battery contract

Status: needs-triage
Type: task
Parent: `06-pscad-emt-pack.md` (GC-06 follow-up, GK A2 on #1596)
Gated: **yes** (GK A, gridspine)
Size: S
Likely files: `gridspine/handoff/emt_pack.py`, `gridspine/handoff/test_battery.py` (GC-04), `tests/gridspine/test_emt_pack.py`

## What

GC-07's readback (issue 07, J1) judges stays-connected only from a `connected` channel; without one the row is unknown. The EMT pack takes its channel list from the GC-04 test battery (`CHANNELS`). When GC-04 bumps the battery contract to add a `connected` channel, the pack, its schedule CSV and the mhi.pscad stub carry it too, with a test that the channel reaches the manifest. Blocked by: the GC-04 contract bump.
