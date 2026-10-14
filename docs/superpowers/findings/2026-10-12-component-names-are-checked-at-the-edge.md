# Component names are checked at the edge (OPEN-ITEMS item 8)

**Date:** 2026-10-12
**Status:** FIXED on `claude/item-8-component-name-validation`. Source: gap 3 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

## The gap

A component name flowed from a request body into the network, then into
download filenames, spreadsheets, log lines and model-facing text, and nothing
checked its characters. The only check was the reserved `ic:` bus prefix. Two
defects had already come from this, each fixed at its sink: the untrusted-fence
bypass (#18) and the `Content-Disposition` header (#7).

## The rule (`services/component_names.py`)

It is narrow on purpose. PyPSA-Eur's own names include `H2 pipeline A -> B`,
`gas pipeline new DE1 0 <-> DE1 1`, `way/123456:a-220`, `merged_17+3`,
`DE0 0 urban central water tanks charger-2050`, and the backend's own
`gas@2030`, `campus:TR_132_33_63#1` and `auto:3`. So spaces, `/`, `:`, `<`,
`>`, `+`, `@`, `#` and all letters stay legal. The rule refuses:

- Unicode Cc: C0 (including tab, CR and LF), DEL and C1.
- Unicode Cf: bidi overrides and isolates, zero-width characters, the BOM.
- Unicode Zl and Zp: line and paragraph separators.
- Unicode Cs: lone surrogates, which cannot be saved to netCDF.
- Empty or whitespace-only names, and names over 256 characters.

## Where it applies (owner decision, 2026-10-12)

The owner was offered three options and chose "edits and new imports":

- **Every interactive create.** `_create_component` (10 classes) and
  `apply_create_global_constraint`, over HTTP and through the chat tools that
  call them.
- **Every interactive rename.** `_update_component` (every `PUT /<class>/
  {name}`; checked before `ensure_carrier`, so a refused rename adds no
  carrier), `apply_rename_bus` and `apply_update_global_constraint`.
- **Carriers created implicitly by a `carrier` field.** `ensure_carrier`
  checks only when it CREATES. A carrier the network already has is referenced
  and not re-checked. `PATCH /_bulk` checks every carrier it would create
  before its first write.
- **Chat tools that bypass the handlers.** `batch_create_components` refuses
  the whole batch in its validation pass. `reconstruct_network_from_image`
  skips a refused bus or line name, as it already skips `ic:` names.
- **New imports.** netCDF, CSV and Excel are swept after import, next to the
  `ic:` sweep, then undone and refused. A project bundle is checked on its
  `network.nc` before the swap. MATPOWER is not swept, because every name it
  makes is built from an integer.

**Not applied:** opening an existing project, undo, snapshot restore and
templates. A network saved before the rule must stay openable. That is why
the sinks keep their own guards, and why `tests/test_download_headers.py` now
installs its hostile load name directly instead of creating it through the
route.

## Verification

`tests/test_component_name_validation.py`, 97 tests.

- Every refusal test first sends the SAME request with a good name and expects
  success, so a red result can only mean the name was the problem.
- Every refusal also asserts the property, not just the status: nothing was
  added, renamed or swapped.
- It was red for the right reason before the fix: a 2xx where 422 was
  expected, and `DID NOT RAISE` for the chat tools.
- Mutation pass: each of the 24 guards and rule branches was removed in turn
  (saved and restored with `cp`). All 24 were killed.
  - The bulk pre-pass first SURVIVED, because the flat form is one batch. The
    test was changed to the row form, where row 1 would land before row 2
    fails, and that kills it.
  - Two guards were dropped instead of tested, because nothing can reach them:
    a second check inside `_rename_component_safely`, which is only called after
    `_update_component`'s check, and the MATPOWER sweep.

## Found while fixing

- Names that start with `=` are legal, and the network CSV and Excel exports
  write them as live formulas. Recorded as OPEN-ITEMS item 15:
  `findings/2026-10-12-network-exports-write-a-formula-name-as-a-formula.md`.
- `jsonschema` is imported by master (`harness`) but is not in
  `requirements.txt`. CI gets it transitively; a clean venv does not, and 26
  test modules fail to collect. Not a product defect; noted for whoever owns
  the requirements.
