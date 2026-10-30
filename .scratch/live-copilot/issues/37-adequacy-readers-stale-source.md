<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-37 — Adequacy readers serve pre-edit numbers with no freshness marker

Status: done
Type: bug

Found by LC-36 (#954). The manifest row is `LC-D13.stale_source_unflagged_readers` (`_pending`).

The EH study reader carries `edited_since_study` (`services/study_state.edited_since`). The
`margin_loop`, `frontier` and `mc` (ELCC) readers of `get_adequacy_results` carry no freshness
marker, and neither does `campaign_status`. LC-36 checked this through the chat tools on
master 60d8332d6. After a finished study, an `update_component` edit (a Load's `p_set`, or a
Generator's `p_nom`) leaves each reader's full output with no key or text matching
fresh/stale/edited/revision/current/changed/outdated. The reader still serves the pre-edit
numbers. Example: the ELCC of LC-33's `elcc_network` is 20 MW at load 100. It is still read as
20 MW after the Load is edited to 95, where the hand answer is 15 MW.

`get_results` has the equivalent (`inputs_changed_since_solve`, `freshness_note`; LC-12).

## Acceptance

- [x] Each of the four readers says whether the network changed since the study ran, on the
      same basis as `edited_since_study` (the revision captured on the record).
- [x] A test through the chat tools per reader: False after the run, True after
      `update_component`, cleared by a re-run whose number moves by the hand delta.
- [x] `LC-D13.stale_source_unflagged_readers` becomes `_covered`.

## Comments

- GK A (plan, 6): GO with conditions, all met in the PR. (1) The flags are asserted with `is
  False` / `is True`; dropping the capture in each runner, and in `_campaign_gated`, fails its
  test with `None is False`. (2) A record or campaign entry without `network_revision` reads
  `None`, one test per reader. (3) A study's own solves and restore never bump the revision: the
  flag `is False` after each run, read after `base_restored is True` for the margin loop and
  the frontier. (4) The campaign flags each entry, its top-level flag follows the latest entry
  per study, no entries read `None`, and the revision is read before `start()` so it can only
  over-report. (5) Gaps have `_pending` rows with tickets: the HTTP readers
  (`LC-D13.stale_source_http_readers`, LC-39; `routers/results.py` was RW-04's) and
  solver-config changes (`LC-D13.stale_source_solver_config`, LC-40; EH has the same limit).
  The frontier's edit raises the dear plant's `marginal_cost` by 10 EUR/MWh, because
  `capital_cost` is a derived field `update_component` refuses; the hand delta is the same
  +400 EUR.
