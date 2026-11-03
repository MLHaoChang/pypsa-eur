<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-40b — One helper captures the network revision for every study runner

Status: needs-triage
Type: refactor (no behaviour change)

Owner decision LC40-SPEC-1 ("Spec LC-40 now"). LC-40 (LC40-COUNTER-1, "same counter") made a
solver-config write bump `network_revision` and skipped a shared helper; LC-41b repeated that
skip ("Out of scope"). Each runner still captures the revision its own way, with its own
copy of the same three-line comment. This issue specs the helper. Docs only: no code here.

## Survey: every capture site (master `a3239f48f`, paths under `pypsa-gui/backend/`)

Record captures (write `record["network_revision"]`):

| # | Site | Kind | When / under what |
|---|---|---|---|
| 1 | `services/adequacy/frontier_loop_runner.py:95` init `None`, `:107` capture | start | worker, under `lock`, before first network read |
| 2 | `services/adequacy/eh_study_runner.py:397` init, `:413` capture | start | worker, under `lock`, before `run_eh_study` |
| 3 | `services/adequacy/margin_loop_runner.py:720` init, `:976` capture | start | worker, under `lock` |
| 4 | `services/adequacy/margin_loop_runner.py:782` | hold | `restore="final"`: `bump_revision(study_ctx)`, record re-captures the bumped value |
| 5 | `services/adequacy/coupling_loop_runner.py:419` init, `:640` capture | start | worker, under `lock` |
| 6 | `services/adequacy/coupling_loop_runner.py:475` | hold | same as #4 |
| 7 | `services/adequacy/mc_loop_runner.py:138` (stored `:219`) | start | no worker: read inside `PyPSAService.get_lock()` while snapshotting the active ctx |
| 8 | `services/study/runner.py:463` | start | decision study / tornado base: read in the record literal, in the caller thread, **no lock**, "before the study starts" |

Revision values passed on (not captures, but they carry a captured value):

| # | Site | What |
|---|---|---|
| 9 | `services/study/runner.py:517`, `services/study/tornado_runner.py:156` | `campaign.record(..., network_revision=record[...])` re-reads the record's value |
| 10 | `services/chat_tools.py:4055` | the chat entry reads `dirty_state.revision()` before `start()` and passes it to `campaign.record` (`:4057`); a second, independent capture of the same study |

Bump sites (hold writers, the other half of the contract; unchanged): `services/dirty_state.py:80`
(`bump_revision`), #4, #6, the chat edit seam (`harness/catalogue.py:5598`), the HTTP
middleware (`main.py:1173`).

Readers (all call `study_state.edited_since`, `services/study_state.py:164`; unchanged):
`routers/results.py` `/frontier` (`:1180`), `/mc` (`:1244`), `/coupling_loop` (`:1378`),
`/margin_loop` (`:1471`), `/eh_study` (`:1594`); `services/adequacy/campaign.py:155`
(decision study / tornado scope-campaign entries); `services/adequacy/grid_readiness.py:1765`;
`services/flow_service.py:881,939`; `services/adequacy/eh_review.py:475` (copies the route's field).
Not a study reader: the AC-PF `ac_pf_network_revision` (`ac_pf_service.py:512`, `flow_service.py:906`)
has its own capture and is **out of scope**.

Findings from the survey:

- Three different rules for the same fact: under lock in a worker (#1–3, #5), under lock in
  the caller (#7), without lock in the caller (#8, #10). The comments in #1–#3 and #5 each
  re-explain the "can only over-report" rule.
- #8 and #10 capture the same study twice (the chat value goes to the campaign, the record's
  value goes to the reader); they can differ by an edit landing between them.
- #4 and #6 are the same hold, copy-pasted, each with its own comment.

## Helper: interface

In `services/dirty_state.py` (it already owns `revision`/`bump_revision`; no new module):

```python
def capture(record: dict, ctx: ProjectContext | None = None, *, lock=None) -> int:
    """Store the current edit counter on `record["network_revision"]` and return it.

    With `lock`, reads under it (a worker's capture: immediately before the first network read).
    Without, reads the counter alone (an atomic int read). Either way an edit that lands later
    can only over-report "edited since", never miss one."""

def hold(record: dict, ctx: ProjectContext | None = None) -> int:
    """A loop's own `restore="final"` hold: `bump_revision(ctx)`, then re-capture on `record`
    so the loop's own verdict is not flagged stale by its own write; other studies read stale."""
```

`capture` needs no `new_record` variant: the `"network_revision": None` initialiser stays in
each record literal (a running record must read `None`).

## Call sites replaced

- #1, #2, #3, #5: `with lock: record["network_revision"] = dirty_state.revision(study_ctx)` becomes
  `dirty_state.capture(record, study_ctx, lock=lock)`.
- #4, #6: `record["network_revision"] = dirty_state.bump_revision(study_ctx)` becomes
  `dirty_state.hold(record, study_ctx)`.
- #7: stays an inline read (it feeds a local that is stored later, not a record write); it calls
  `dirty_state.revision()` as today. Listed so the reviewer sees it was considered, not missed.
- #8: `"network_revision": dirty_state.revision(ctx)` becomes `None` in the literal plus
  `dirty_state.capture(record, ctx)` right after, same thread, so the value is identical.
- #10 and #9: unchanged in PR 1; PR 2 (option C only) makes #10 read the study's record
  value instead of a second capture.

Comments in #1–#6 shrink to one line pointing at the helper's docstring.

## Test plan

Behaviour must not change, so the existing LC-37/39/40/41 suites stay green unchanged
(`test_lc37_d13_stale_readers.py`, `test_lc39_d13_http_stale_readers.py`,
`test_lc40_d13_solver_config_stale.py`, `test_lc41_d13_panel_stale_banner.py`,
`test_eh_study_edited_since.py`, `test_lc36_d13_stale_source.py`).

New, in `tests/test_lc40b_revision_capture.py`:

1. Unit: `capture` stores and returns the counter; with `lock` it takes the lock (a held lock
   blocks it); a `None` record value stays `None` until captured.
2. Unit: `hold` bumps the counter by exactly one and the record equals the new counter; a second
   record captured earlier reads stale.
3. One stale-banner test per reader, each: run (or seed via the runner's own capture path) the
   study, edit through the real seam (a network edit), read, expect `edited_since_study: true`;
   then a re-run reads `false`; a running record reads `null`:
   - `/api/results/frontier` (frontier_loop_runner)
   - `/api/results/mc` (mc_loop_runner)
   - `/api/results/margin_loop` (margin_loop_runner, plus the hold case: own record fresh, a
     sibling frontier stale)
   - `/api/results/coupling_loop` (coupling_loop_runner, plus the hold case)
   - `/api/results/eh_study` (eh_study_runner)
   - decision study and tornado via the campaign entries (`campaign.py:155`, runner #8)
   - the chat readers: grid-readiness (`grid_readiness.py:1765`) and flow (`flow_service.py:881`)
   Each test goes through the runner's real capture, not a hand-set `network_revision`, so it fails
   if a runner stops calling the helper. A source-level guard test greps the runners for a bare
   `["network_revision"] =` write outside `dirty_state.py` and the record literals.

## Risks

- **Lock order.** `capture(..., lock=)` must take the same lock the worker takes today
  (`lock` = the mutation lock); the margin and coupling runners also hold `solver_state_lock`
  elsewhere. Taking a second lock inside the helper would deadlock; the helper takes only the one
  passed in.
- **#8 timing.** Moving the read from the record literal to the line after it must stay in the
  same thread before `thread.start()`; a read after start would miss an edit landing in between.
- **Gated engines.** The runners are adequacy/study engine code: the plan goes to the gatekeeper
  first (steward "Before a plan"), and a mechanical change is still reviewed as gated.
- **PR conflicts.** Open PRs #1050 and #1059 touch `backend/services/` and `backend/tests/` (not
  these files, as of this survey); recheck overlap with `scripts/coord/premerge.sh` before the
  code PR.
- **Double capture (#8/#10)** stays until option C; it only over-reports today.

## Scope options for the owner

Decided: **B** (owner decision LC40B-SCOPE-1). Options kept for the record.

- **A. Helper plus the six worker sites** (#1–#6): smallest, one PR, no #8.
- **B. A plus #8** (decision study / tornado): all record captures on one helper. One code PR.
  Recommended.
- **C. B plus #10 and #9** (the chat entry reuses the study's record value): removes the double
  capture but changes the chat seam and `campaign.record`'s caller; two code PRs.
- **D. Spec only, defer the code**: file stays `needs-triage`.

## PR split

1. PR 1 (this issue file, docs only): `.scratch/live-copilot/issues/40b-revision-capture-helper.md`.
2. PR 2 (option A/B): `dirty_state.capture`/`hold`, replace sites #1–#6 (+#8), unit tests 1–2,
   the source-level guard.
3. PR 3 (the per-reader stale-banner tests 3, no production change) can go with PR 2 or follow it
   when review prefers the helper diff small.
4. PR 4 (option C only): #10/#9.

## Acceptance

- [x] Owner picks the scope option: **B** (helper + the 6 worker sites + `study/runner.py:463`), decision LC40B-SCOPE-1.
- [ ] Helper exists in `dirty_state.py` and every in-scope site calls it.
- [ ] One stale-banner test per reader listed above, all green; LC-37/39/40/41 suites unchanged.
- [ ] `study_state.edited_since`'s docstring names the helper as the only capture path.

## Comments
