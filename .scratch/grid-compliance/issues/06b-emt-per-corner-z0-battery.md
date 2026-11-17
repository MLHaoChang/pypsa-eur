<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-06b Per-corner Z0 in the GC-04 test battery

Status: needs-triage
Type: task
Parent: `06-pscad-emt-pack.md` (GC-06 follow-up, GK A2 on #1596)
Gated: **yes** (GK A, gridspine)
Size: S
Likely files: `gridspine/handoff/test_battery.py` (GC-04), `gridspine/handoff/emt_pack.py`

## What

The GK A2 plan verdict on 06 left the battery with a single z0; the EMT pack lists each schedule row whose per-corner source Z0 differs from it. If GC-04 adds per-corner z0 to the battery, the pack drops that list and reads the battery's value, with a test per corner (max/min). Owned by GC-04; GC-06 follows.
