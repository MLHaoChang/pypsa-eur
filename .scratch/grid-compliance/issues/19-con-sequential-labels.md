<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-19: sequential .con labels with a label-to-id map

Status: done

Done in #1743 (3b9ea4f9).
Model tier: sonnet
Size: S
Blocked by: #1607 (writes `contingencies.py`), #1608, #1609, #1589 (changes `contingency_list.py`)
Likely files: `gridspine/handoff/contingencies.py`, `gridspine/ingest/contingency_list.py`, `gridspine/handoff/psse_pack.py`, `tests/gridspine/`
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

## Comments

**2026-11-17 triage (SCALE-1).** Still needed: `write_con` still refuses ids outside the token set, so a bus-name contingency set cannot be written. It shares files with the GC-04 PR4–6 stack and GC-03 PR2, so it waits for them; bundle with GC-18 only if both start at the same time (they share no files).

**2026-11-19 GK A2 (5) plan GO, conditions G1-G7.** G1 the .con carries a first-line mark (`COM gridspine conmap <file>`); a marked .con needs its map (sibling file for a path, `conmap=` for text) or is refused naming the map file; an unmarked hand-written list reads as before. G2 the map is UTF-8 csv through the csv module; refuses by name a duplicate label, a duplicate id, an empty id, a wrong header. G3 labels are deterministic, the width grows past 9999, numbering follows input order and skipped compound kinds take no number. G4 the manifest and file list name the map only when a .con block is written. G5 the pack README says the map turns a label back into its id. G6 each new refusal has a killing test; mutants.py on both modules; test_psse_import.py unchanged. G7 release-note fragment, about 600 lines, PR body lists G1-G7 with tests.
