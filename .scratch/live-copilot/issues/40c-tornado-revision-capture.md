<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-40c — The tornado study captures its network revision

Status: needs-triage
Type: behaviour change (gated: GK A)

Found while building LC-40b (`40b-revision-capture-helper.md`). The LC-40b spec assumed the
tornado shared the decision study's capture (`services/study/runner.py`, site #8); it does not.
`start_tornado` (`pypsa-gui/backend/services/study/tornado_runner.py`, record literal ~:275)
builds a record with no `network_revision` and never calls `dirty_state.capture`. Each variant's
`campaign.record(..., network_revision=self.record.get("network_revision"))` (~:156) therefore
passes `None`, and the campaign reader (`services/adequacy/campaign.py:155`) reads every tornado
entry as `edited_since_study: null` (Unavailable), never stale.

LC-40b stayed a pure refactor (owner decision LC40B-SCOPE-1, coordinator ruling on option 1), so
the fix is this issue. Until it lands, the LC-40b guard test lists the tornado runner in
`NO_CAPTURE` (`tests/test_lc40b_revision_capture.py`); that test fails once the capture exists,
so the fix drops the entry.

## Change

- Add `"network_revision": None` to the tornado record literal and call
  `dirty_state.capture(record, ctx)` on the line after it, in the caller's thread, before the
  Thread, the lock acquire and the registry insert (the decision study's rule, LC-40b condition 3).
- Drop `study/tornado_runner.py` from `NO_CAPTURE`.

## Tests

- A tornado stale-banner test: run a tornado (fake solver as in `tests/test_study_runner.py`),
  read its campaign entries: False after the run; edit through a real seam (HTTP edit
  middleware, chat edit seam or `update_solver_config`, never a direct `bump_revision`), read
  True; a re-run reads False.
- A `Thread.start` patch shows the tornado record holds an int when its thread starts.

## Acceptance

- [ ] The tornado record captures through `dirty_state.capture` before its thread starts.
- [ ] Its campaign entries read True after an edit through a real seam, False after a re-run.
- [ ] LC-37/39/40/41 and LC-40b test files unchanged apart from the `NO_CAPTURE` entry.
