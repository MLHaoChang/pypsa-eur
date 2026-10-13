# Open items

**Verified against the tree on 2026-09-12.** Every entry below was reproduced or
re-read in source on that date, not carried forward on trust.

**2026-09-29:** four items closed and removed per the convention below — the
user-timeseries tenancy item, and the three follow-ups fixing it uncovered (an
obsolete `persist_user_ts` predicate at three call sites, a hydrate that ignored
its project's `user_ts.json`, and an adequacy-sweep test that could not fail for
its own regression). Their findings carry the detail. Nothing else was re-verified
on that date, so the entries below still date from 2026-09-12.

**The High authorization family was re-verified on 2026-09-30** (against
`a9a1f5b`). Items 2, 3 and 4 were found already fixed — see "Closed since the
2026-09-12 pass" — and the re-verification turned up a fourth instance of item
2's defect, entered below as item 16.

**2026-10-13:** that item is renumbered **16**, and this pass's 13 and 14 become
**17** and **18**. Master assigned 12–14 to the 3D site view on 2026-10-06
while these branches were unmerged. Commits, findings and tests written
before 2026-10-13 cite them as 12, 13 and 14.

**Renumbered 2026-10-13 (later):** the 3D site view's 12 and 13 become **19**
and **20**. The P33b items 12 and 13 (closed by PR #170) keep their numbers
because they were entered first. Text written before this date that names
the scratch-slot network reset or the `login.html` credential as "item 12" or
"item 13" means 19 or 20; text about the io-import pointer or the reload of
the open project means the closed 12 or 13. Another item 13 (the hydrate that
ignored `user_ts.json`, cited by `findings/2026-09-28-*` and
`test_hydrate_restores_user_ts.py`) was closed and removed on 2026-09-29.
`test/test_open_items_numbering.py` now fails on any reused number.

**2026-10-06:** items 12–14 (now 19, 20 and the closed 14) added from the 3D site view branch's full-product
browser E2E (`notes/2026-09-29-3d-site-view-phase2-qa.md` §7), re-verified in
source on master that day. Its defect D (a second project from the same
template → 409) is not listed: master's `_unique_project_name` already fixes it.

**2026-10-06 (later):** two items closed and removed per the convention —
item 7 (blank-canvas node positions reverting; the load now takes the newest
of server / memory / localStorage by `savedAt` and a failed `PUT /layout`
surfaces once, `findings/2026-07-31-blank-canvas-node-drags-revert.md` carries
the detail) and item 14 (a three-port Link dropping `bus2` / `efficiency2`;
`_drop_unknown_extras` admits PyPSA's per-port columns by regex). Both are
increments A1 and A4 of `plans/2026-10-06-visual-layers-1-abstract-canvas.md`.

**2026-10-13:** the P33b-era items 12 and 13 closed (see "Closed since the
2026-09-12 pass"; the 3D-site-view items that shared the numbers are now 19
and 20). Item 12
(an io import under a session pointer landing in a context the next request
never reads) was already fixed on master by `e049c25e`: `routers/io.py::_unbind_session`
clears the pointer on a successful import, as `/network/reset` does. The only
test covered NetCDF; `test_every_http_import_is_what_the_next_request_sees`
now covers all four imports and fails with the un-pointing removed. Item 13 (a
re-load of the open project writing the discarded edit to disk): `load_project`
now registers with `write_back=False`. When the displaced copy is the
caller's own, the UI has already confirmed "discard and reload". When it is
another resident project's copy (the clone wizard's `load(src)`), its unsaved
work is saved before the disk read, so memory and disk agree and nothing is
lost. Tests: `tests/test_reload_same_project.py`.

This file exists because GitHub Issues is **disabled** on this repository, so
there is nowhere else to keep a queue. It is deliberately thin: one entry per
open item, with the anchor and the source document. The analysis lives in
`findings/`; this is only the index. **If Issues is ever enabled, move these
there and delete this file** — an index that outlives its purpose is how the
`findings/` directory drifted in the first place (statuses said open for things
fixed weeks earlier; see the 2026-09-12 triage commit).

Closed items are not listed. Each finding in `findings/` now carries its own
truthful status line, so a file that is not named here is either closed or a
verification record.

---

## High

### 18. The campus-electrical chat tools bypass the edit-lock check

`services/chat_tools.py`, added by #79 (merged 2026-10-06). The HTTP routes in
`routers/campus_electrical.py` call `_check_lock` before the service. The chat
tools `campus_draft_campus` and `campus_run_study` call the service directly,
and `_gridspine_project` resolves access but not the lock. Reproduced on
`af69613`: the HTTP draft returned 409 for a non-holder, while
`campus_draft_campus(overwrite=True)` and `campus_run_study` both reached the
write path. #84 added a third, `campus_extract_grid_code` (it can add a grid-code draft but not overwrite one), reproduced the same way. #91 added a fourth, `campus_set_library` (it replaces the project's asset library), also reproduced. This is the fifth instance of the "a second caller skips the
handler" shape. `tests/test_write_surface_lock_policy.py` caught it on the day
it landed, by going red on #80 merged with the new master. Note for the fix:
these tools take a `project_id`, so the seam's active-project predicate would
test the wrong lock. Check the lock of the resolved project. Full analysis:
`findings/2026-10-06-campus-chat-tools-bypass-the-lock-check.md`. Server only.

## Medium

### 6. Authorization denies by omission: lock dimension done, ACL dimension open

**Narrowed 2026-10-05.** The original entry ("`ProjectAccessDep` is adopted by
6 of 23 routers") was partly stale and partly mis-framed: `/api/results/` has
since been lock-gated; the count missed `ProjectDep`, a second ACL dependency
on the same `resolve_project`; and the five large routers act on the ACTIVE
project, which no path-param dependency can resolve. The item's own remedy was
the right one: make the omission fail loudly.

**Lock dimension: done** in `a266dd5`. `tests/test_write_surface_lock_policy.py`
fails on any write route or non-read chat tool that is not gated, not
verifiably holder-checked (calls are followed, not source-scanned), and not
given a written reason. Mutation-tested seven ways; undoing item 16's fix turns
it red naming that exact route. Writing it found item 17.

**Still open:**
* the **ACL** dimension ("may this caller SEE this project") has the same shape
  and no omission test. Nothing has been found there, and nothing has looked
  systematically either;
* three product questions the policy table now states rather than settles:
  should gridspine studies, chat-history import/clear, and adequacy campaigns
  respect the edit lock?

Plan and ground-truth counts:
`plans/2026-10-05-item-6-write-surface-lock-policy.md`.

### 8. Component names are not validated at the edge

Names flow from request bodies into the network, into filenames and into
model-facing text with no character-class check. This is the shared root cause
behind the untrusted-fence bypass (fixed in #18) and the
`Content-Disposition` defect (fixed in #7) — both were symptoms treated at the
sink. `services/upload_service.py`'s `_FILE_ID_RE` shows the right pattern,
anchored and narrow, and it is not applied to names generally. Source: gap 3 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

---

## Low

### 9. A refused `/stream` request still switches the session model

`routers/chat.py:1166` sets `session.model` (and `profile_id`, `bound_wire`)
before `run_turn` reaches the `_turn_in_flight` guard that refuses the request
(`services/chat_service.py:3495`). So a caller who cannot get a turn can still
change which model the *next* turn uses — a rejected request mutating persistent
state. Two fix options in the finding, neither applied; it is a design choice
about where the guard belongs. Source:
`findings/2026-09-10-a-refused-stream-request-still-switches-the-session-model.md`.

### 10. Cookie policy is hardcoded to a preview vendor's hostname

`routers/auth.py::_cookie_flags` returns `SameSite=None; Secure` for any host
matching `.cursorusercontent.com`. `SameSite=None` widens CSRF surface, and here
it is granted by a hostname suffix compiled into the product. Defensible for a
preview environment, not as a permanent rule — the deployment should decide its
own cookie policy through configuration. The CSRF double-submit check
(`main.py:645`) currently carries the load for those sessions. Source: gap 5 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

## Closed since the 2026-09-12 pass

Listed once, with the closing commit, because the drift this file exists to
prevent was statuses reading *open* for things fixed weeks earlier. Numbering is
NOT reused or compacted — other findings and assessments cite these numbers.

* **2** — `worksheet` / `stress_scenarios` PUTs ignore a foreign edit lock.
  Closed by `68e5f62` (+ `2ede7a4`, tolerating a project with no real uuid).
  Tripwire: `tests/test_worksheet_foreign_lock.py`, which asserts the two
  subjects AND two lock-checked controls. Mutation-verified 2026-09-30: removing
  both guard blocks turns exactly the two subject tests red. **The class is not
  closed — see item 16.**
* **3** — `/api/chat/{id}/rewind` and `/abort` perform no authorization. Closed
  by `be4d5ed`, which gave `ChatSession` an `owner_user_id` and a fail-closed
  `session_owner_allows`. Tripwire: `tests/test_chat_session_ownership.py`.
  Mutation-verified 2026-09-30: stubbing `session_owner_allows` to `return True`
  turns the `/rewind`, `/abort` and owner-less tests red — but NOT the `/confirm`
  test, which posted a fresh `uuid4()` and so was satisfied by token lookup
  failing before ownership was ever consulted. The guard was real; its tripwire
  was not. Repaired 2026-09-30 to mint a live token and assert the property
  (still pending, no decision recorded, owner can still spend it).
* **4** — `/api/changelog/` is unscoped for a caller with no OrgMembership.
  Closed by `6f5e170`, which made the predicate `is_super_admin` rather than
  "has no membership", and kept read and destroy at different privileges: a
  super-admin still reads across tenants, and nobody clears across them through
  this route. Tripwire: `tests/test_changelog_scoping.py`, six tests including
  both halves of the super-admin distinction.
* **16** — `PUT /{name}/asset_health` ignores a foreign edit lock. Opened and
  closed on 2026-09-30: recorded first as a defect outside items 2-4, then fixed
  by `4eca8d2` on a separate instruction. The fourth instance of item 2's defect
  and the second in that file; ungated for 20 days because it landed two days
  before the commit that fixed its two siblings, so it was never in the audit's
  route list. Tripwire: `tests/test_asset_health_foreign_lock.py`, written
  before the fix, red on the property rather than a status code, and
  mutation-verified after. **This closes the class in this router family — all
  three sidecar PUTs now carry the check** — but not the shape that produced it,
  which is item 6.
* **17** — chat tools that wrote a project's upload store bypassed its lock
  check (`clear_uploads` and six `export_*` tools). Found and reproduced
  2026-10-05 while doing item 6. Fixed independently by #76 (`e36c041`), which
  merged on 2026-10-07: it gates the tools at the seam and checks every export
  through `_check_foreign_lock("export")` in `_save_agent_export`. On merge,
  the write-surface ratchet failed on exactly the 7 `KNOWN_GAPS` entries and
  on the 3 `TOOL_POLICY` entries #76 also gated (`clear_chat_history`,
  `start_campaign`, `end_campaign`). That is the same 10 a trial merge
  predicted two days earlier, and all are now deleted. #76 also settled two of
  the three product questions item 6 recorded.
* **5** — chat sessions have no owner, so the confirmation gate rests on id
  secrecy. Closed by the same commit as item 3: `owner_user_id` plus one
  comparison is exactly the fix this item asked for, so `/confirm` no longer
  depends on the session id being unguessable.

* **12** — `/api/io/import/*` under a session pointer imported into a context
  the next request never read. Already closed on master by `e049c25e`
  (`routers/io.py::_unbind_session` clears the pointer on a successful import).
  Tripwire widened by `9c99daa0` (PR #170):
  `test_every_http_import_is_what_the_next_request_sees` covers all four
  imports, and all four fail with the un-pointing removed.
* **13** — re-loading the open project with unsaved edits left memory and disk
  disagreeing. Closed by `9c99daa0` (PR #170): `load_project` registers with
  `write_back=False`, and a displaced copy belonging to a project the caller is
  not on is saved before the disk read. Tripwire:
  `tests/test_reload_same_project.py`, red on the old code in server and local
  mode.
* **19** (the 3D site view's 12 until 2026-10-13) — a network reset while a
  project is open landed in the session's scratch slot: undo, raw imports,
  snapshot restore, and template create / bundle import. Every route was
  already fixed on master when this was verified on 2026-10-13: `e049c25e`
  for the imports, and P33b's `rekey_context` in undo, snapshot restore and
  `_register_created_context` for the rest. Tripwire:
  `tests/test_routes_with_a_project_active.py`, seven tests run with a session
  pointer SET. They are the five kept in `notes/2026-09-30-full-e2e/` plus a
  bundle-import test and a test that undo and restore are refused (409) while
  the queue solves the active project. Each was mutation-checked: removing
  the matching `rekey_context` call, or the solver-in-flight gate, turns it
  red.

---

## Verification / CI

### 11. Three CI signals that cannot be trusted

**Three jobs are path-filtered and read green while running nothing.**
`Gridspine` ("Skip - no gridspine changes") and BOTH `Integration` jobs
("Skip - no source changes") report success with every meaningful step skipped —
on `83d50f0` that was 3 of 5 checks. A green check mark does not mean those tests
ran. I got this wrong myself on PR #16 and had to correct it, which is the point:
the signal looks like coverage.

**`GUI backend` is the only job that validates this backend, and a follow-up
push cancels it.** The workflow uses `cancel-in-progress`, and its backend-test
step takes ~22 minutes. On `83d50f0` it was cancelled at 16:36:38 by the next
push, so CI reached **no verdict at all** on that commit — which is the commit
that carried a regression (a missing `tool-error-kinds.json` entry). The local
failing-set run caught it; CI could not have. Two consequences worth keeping:
the local full-suite comparison is the PRIMARY gate on this repo, not a
belt-and-braces extra; and pushing again while `GUI backend` is in flight
destroys the only signal in flight. Note also that a `check_suite.completed`
event is silent about this — its own text excludes cancelled suites.

One thing this does NOT mean, tested rather than assumed: a docs-only push does
not make `GUI backend` skip. `dorny/paths-filter` on a `pull_request` event
diffs against the BASE branch, not the previous commit, so the whole PR's
changed paths decide the filter and earlier backend commits keep it true. The
cost of pushing mid-flight is the ~22 minutes, not a green check that ran
nothing. (I predicted the opposite here and was wrong; the run on `b3f8829`
disproved it.)

**`dev-env` has been red since `14eae4d`.**

Related but *not* a defect, recorded so nobody re-investigates it: `Run
validation` declares `runs-on: self-hosted` and this repo has no self-hosted
runner, so it queues for GitHub's 24h limit and is auto-cancelled on every PR
(confirmed on #16: queued 2026-09-10T19:08:20 → cancelled exactly 24h later,
and #16 merged in that state). Nothing to fix inside a PR.

Also worth knowing when reading any test claim about this backend: the local
suite carries **124 pre-existing failures**, all from `No module named
'gridspine'`. "The suite is green" is false locally; the only sound gate is
*the failing set is unchanged*. Source: gap 6 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

### 20. `login.html` prefills and prints a review credential

`frontend/public/login.html:391-402` ships `admin@example.com` /
`admin-pass-123` as the input values and in a visible "Review account" line, and
auth mode serves that page (`static_gate.decide_route` passes `/login.html`
through). Fine for a review build; a credential in a shipped page otherwise. One
decision (strip before release, or gate the prefill on a build flag) and a test
that the served page carries no password value. Source:
`notes/2026-09-29-3d-site-view-phase2-qa.md` §7, "Other findings". Numbered 13
until 2026-10-13.
