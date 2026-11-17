<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-19: sequential .con labels with a label-to-id map

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A2, gridspine)

## Why

`gridspine/handoff/contingencies.py` `write_con` uses each contingency id as its `.con`
block label and refuses any id outside `[A-Za-z0-9_-]{1,32}` (GK ruling on GC-04 PR4).
PyPSA-Eur bus names often fall outside that set (spaces, dots, more than 32 characters),
so a contingency set built on them cannot be written at all today.

Issue number 15 was taken on other branches (15 to 18 are in use), so this is 19.

## What to build

- `write_con` labels blocks sequentially (for example `C0001`, `C0002`, ...) and writes a
  label-to-id map beside the `.con` in the pack.
- GC-03's contingency reader (`gridspine/ingest/contingency_list.py`) reads the same map,
  so a label goes back to its original id with nothing unmatched.
- The map is named in the pack manifest; a `.con` without its map is refused on read-back.

## Done when

- A set whose ids are PyPSA-Eur bus names (with spaces and longer than 32 characters)
  writes, and round-trips through the GC-03 reader to the same ids.
- A label missing from the map, or a map entry with no block, is refused by name.
- The current refusal test for ids outside the token set is replaced by the round-trip
  test only once the map exists.
