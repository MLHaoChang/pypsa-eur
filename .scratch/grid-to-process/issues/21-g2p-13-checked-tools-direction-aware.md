<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: make `checked_tools` direction-aware so an enum widening is accepted by `--write`

Status: done
Type: task
Found by: G2P-13 (#1194) PR body, "Follow-up".

## Problem

#1194 added the `grid_risk` value to `ui_open_panel`'s results-tab enum. The frozen input-schema hash had to be edited by hand because `--write` does not accept a widening of a checked tool's input enum. Make the check direction-aware: a widening (additive) is accepted by `--write`, a narrowing or removal is still refused. Needs a plan to GK B (AA-V1-CHANGE-1 applies).

## Where to look

`pypsa-gui/backend/harness/assurance_contracts.py`, the `checked_tools` handling (`"checked_tools": "returns"`, ~line 56 and ~125).

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Likely files: `pypsa-gui/backend/harness/assurance_contracts.py` (`checked_tools`, ~56, ~125).
- Blocked by: none.
- Model tier: opus.
- Gated: GK B; send the plan to GK B first (AA-V1-CHANGE-1).
- Size: M.
- Widening of a checked tool's input enum accepted by `--write`; narrowing or removal still refused; test both directions.

### 2026-11-07: GK B (34) plan verdict, GO WITH CHANGES (recorded verbatim)

- G1. `default` is not a note inside checked_tool_inputs. Once the hash is skipped, a default that is added, changed or removed changes what an old call that omits the argument does, and today `_NOTES` hides it. No checked tool's input carries a default today (I walked the 8 schemas), so this costs nothing now. Each of the three cases breaks, with a test. `title` and `description` stay notes.
- G2. Plan item 4, the open-map rule, applies to every keyword branch, not only the _NOTES skip. Inside `properties` / `$defs`, a property named required, enum, maximum etc. is a property and never a keyword. Today, adding a property named "required" reads as "adds required <names>". Test: a property named title / description / default / required removed → breaks; added → additive.
- G3. The hash skip covers only `$.checked_tools.<tool> changed`. A removed tool, or a tool missing from checked_tool_inputs on either side, still breaks. Test each case.
- G4. Tests in both directions, beyond your list:
  - a nested enum widening (inside `items` or a nested object) is accepted;
  - an added pattern / format / const and a true→false additionalProperties each break;
  - the current 8 schemas, run through --write on master's tree: only the new section appears, and every checked_tools hash is byte-equal (pin it).
- G5. Hand mutants with killing tests named in the PR body: drop the fail-closed branch (old has no schema → skip anyway); skip the hash unconditionally; restore the _NOTES skip for `default`; drop the open-map guard on the notes skip.
- G6. Over-strict is fine. Removing additionalProperties:false, adding an anyOf branch or widening a type still breaks (fail closed). Say so in the PR body as a known limit, not a defect.

### 2026-11-07: done (G2P-21)

`checked_tool_inputs` (an `accepts` contract) stores each checked tool's input schema. A moved `checked_tools` hash is skipped only when both snapshots store that tool's schema and hash it to their listed hash; `_accepts_breaks` over the section then judges the direction. The v1 hashes and `V1_INPUT_ADDITIONS` are unchanged.
