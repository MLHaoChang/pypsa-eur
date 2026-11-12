<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A sensitivity case shows the plain-network headline cards, not its base's

Status: done (UM07-KIND-1; #1410 merged 2026-11-12)
Type: bug (found while taking the 03 S3 shots)
Reviewer: GK B.

## What is wrong

A sensitivity case made by `run_sensitivity_sweep` from an energy-hub project
has no `eh_template.json` in its folder, so `headline_kpis.project_kind` reads
it as a plain network. The base shows LCOE, system cost, new-build capital
cost, curtailment rate and unserved energy; its case shows system cost,
new-build capital cost, curtailment rate and "Peak line loading: Not
applicable". Shots: `.scratch/ui-modern/shots-03/s3-base-expert-dark.png`
beside `s3-case-expert-dark.png`.

The deltas are right for the cards both show (they are matched by card id);
the case simply never gets an LCOE or unserved-energy card.

## To decide

Whether a case should take its base's project type for the strip (and for
whatever else reads the template marker), or the sweep should copy the marker.

## Decided (UM07-KIND-1, owner 2026-11-11 23:52Z)

A case takes its base's project type, read-side: one helper resolves a
project's template marker, following a sensitivity case to its base, and
`headline_kpis.project_kind` and every other reader of the template marker
use it. Existing cases are fixed with no migration; the sweep does not copy
`eh_template.json`. GK B reviews (TDD plan first).

## Also seen, not this package's

- A template project solved once through `/api/simulation/queue` reads as out
  of date after it is reloaded from disk (the recorded solve fingerprint
  differs from the saved network's); a second queued solve clears it. Every
  case of such a base then reads "the base has no current results to compare
  with". Script: `.scratch/ui-modern/shots-03/s3_resolve.py`.
- Sensitivity cases are not listed on the Projects page.
- In a multi-period run the source line shows the snapshot window
  ("2030-07-01 → 2030-07-07 · all periods") while the trend spans the
  investment periods (2027 to 2035).

## Comments

GK B plan GO (2026-11-12), conditions:

- U1 access: a case resolves to its base only when the case row is a direct
  child of the base row and in its organisation; otherwise none.
- U2 unreadable is not absent: a case record or base marker that exists but
  cannot be read behaves, for each reader, as a malformed own marker does
  (no silent plain, no 500); typed outcome; marker writers replace atomically.
- U3 undo: `restore_meta(None)` removes only a case copy the undone edit
  made; a copy the case had before stays byte-equal.
- U4 solve capture stays in this PR: a job reads only its captured copy;
  pin byte-equality for a non-case project and a case with its own copy; the
  PR lists the LP/result inputs the marker changes for a case solve and
  whether the input fingerprint or the investment-case digest reads it;
  existing cases' capture identities go stale after deploy.
- U5 per-reader tests use a real `run_sensitivity_sweep` case and the real
  helper and reader.
- U6 the guard also catches concatenation / f-string names, or the PR says
  where it cannot; shown red with one reintroduced literal reader.
- U7 copy-on-write under the case's own project lock; the base's bytes and
  mtime never change from a case; a later base change no longer reaches a
  case with its own copy.
- U8 mutants on `template_marker.py` against all the new tests; hand
  mutants of the recursion, visited set, depth cap, org check and own-wins
  order, each with its killing test.

GK B U3 ruling (2026-11-12): option A, accept as is. On undo a case behaves
as a template project does; the loss of a non-undoable marker write
(`save_dc_backup`) is pre-existing and is filed as issue 10
(`10-undo-drops-a-non-undoable-marker-write.md`). The unreadable-snapshot
fix (a case snapshot that cannot be read raises; the undo snapshot is
skipped) stays.
