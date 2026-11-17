# Family-manifest fragments

A pull request that adds a chat tool, a variant, or evidence for one adds one
file here, `<branch>.json` (as in `doc/release-notes.d`), instead of editing
`smoke/family_manifest.py`. So two pull requests never edit the same lines
(RETRO-FRAG-1). Changing or deleting an existing row stays an edit where it
lives.

```json
{
 "tool_families": {"get_thing": "projects"},
 "variants": [{"id": "LC-D01.thing", "status": "covered", "oracle": "the hand number",
               "tools": ["get_thing"], "tests": ["pypsa-gui/backend/tests/test_x.py::test_y"]}],
 "settles": [{"id": "LC-D05.some_pending_variant", "status": "covered", "oracle": "...",
              "tools": ["..."], "tests": ["pypsa-gui/backend/tests/test_x.py::test_z"]}],
 "variant_additions": [{"id": "LC-D02.topology", "tools": ["get_thing"],
                        "tests": ["pypsa-gui/backend/tests/test_x.py::test_w"]}]
}
```

Every section is optional; sections apply in that order.

- `tool_families`: a tool not yet in any family joins one of `FAMILIES`.
- `variants`: a new variant id. Its keys are those of its status: covered
  `id status oracle tools tests` (no tools means the checked stream), pending
  `id status reason`, unavailable `id status reason pointer`.
- `settles`: a variant that is `pending` becomes covered or unavailable, in
  place. Only a pending variant, and only once: settling a covered or
  unavailable variant, an unknown id, or one another fragment settles fails.
  `--check` then prints `stale: <id> ...` until someone deletes the old pending
  row.
- `variant_additions`: more tools or tests for a covered variant. A tool or
  test it already lists fails.

`manifest_problems` (`python smoke/family_manifest.py --check`) runs on the
merged view, so every tool must be an inventory tool and every test must
collect.
