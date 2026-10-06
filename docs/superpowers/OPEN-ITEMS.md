# Open items

**Verified against the tree on 2026-09-12.** Every entry below was reproduced or
re-read in source on that date, not carried forward on trust.

**The High authorization family was re-verified on 2026-09-30** (against
`a9a1f5b`). Items 2, 3 and 4 were found already fixed — see "Closed since the
2026-09-12 pass" — and the re-verification turned up a fourth instance of item
2's defect, entered below as item 12.

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

## Critical

### 1. The user-timeseries store is a process global shared across tenants

`services/user_timeseries.py:39` — `_user_ts` is keyed `(component, attribute,
column)` with no org, project or session. It is authoritative, not a cache: the
timeseries GET prefers it over the network's own data, and every foreground save
serialises it into that project's `user_ts.json` and reapplies it onto the
network before the netCDF export.

Reproduced: org A uploads a profile for `L1`; org B activates its OWN project and
reads A's values, then saves them into B's storage. Cross-tenant read AND
cross-tenant write. The desktop build is affected too, as a multi-project
data-integrity bug.

The code mitigated only the BACKGROUND case, on the stated basis that the store
"belongs to the FOREGROUND ctx" — true for one desktop user, false once one
process serves many signed-in sessions, where every session is a foreground.

NOT patched: ~230 references across 13 modules; per-`ProjectContext` isolation is
the real fix and needs its own plan. A narrow containment exists (restore-or-clear
on activate, as `load_project` already does) but closes only the demonstrated
path, not the class — two concurrent sessions on different projects still share
one dict. Full analysis, reproduction and fix criteria:
`findings/2026-09-12-user-ts-is-a-process-global-shared-across-tenants.md`.

---

## High

### 13. Chat tools that write a project's upload store bypass its lock check

`services/chat_tools.py`. The HTTP upload routes (`post_upload`,
`delete_upload_route`) check the edit lock. Seven chat tools write the same
store with no check of their own and are not gated at the chat seam:
`clear_uploads` (destructive: deletes every upload of the active project) and
the six `export_*` tools (additive, via `_save_agent_export` →
`upload_service.add_upload(kind="agent_export")`). All seven are routeless, and
`_LOCK_GATE_SERVICE_CALL_MUTATORS` only lists **network** mutators.

They were excluded by a docstring justification that went stale: "upload /
export / chat-history tools write ... on surfaces the middleware does not gate
either". That stopped being true for uploads when the HTTP upload routes gained
in-handler checks. Reproduced 2026-10-05: with the same non-holder identity,
HTTP `DELETE .../uploads/{id}` was refused 409 while `clear_uploads` wiped the
holder's files (`[...] -> []`), and HTTP `POST .../uploads` was refused 409
while `export_to_csv` added a file (1 → 2). `uploads/` is in `_BUNDLE_DIRS`.
Found while doing item 6. `tests/test_write_surface_lock_policy.py` lists all
seven as `KNOWN_GAPS` and fails when one is fixed until its entry is deleted.
Full analysis, reproduction and fix criteria:
`findings/2026-10-05-chat-tools-bypass-the-upload-lock-check.md`. Server only.


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
given a written reason. Mutation-tested seven ways; undoing item 12's fix turns
it red naming that exact route. Writing it found item 13.

**Still open:**
* the **ACL** dimension ("may this caller SEE this project") has the same shape
  and no omission test. Nothing has been found there, and nothing has looked
  systematically either;
* three product questions the policy table now states rather than settles:
  should gridspine studies, chat-history import/clear, and adequacy campaigns
  respect the edit lock?

Plan and ground-truth counts:
`plans/2026-10-05-item-6-write-surface-lock-policy.md`.

### 7. Node positions revert on the blank canvas

User-reported 2026-07-31; diagnosed, never fixed. Two facts still hold:
`PUT /layout` 404s until the project directory exists on disk, and on load the
server wins unconditionally — `TopologyCanvas.tsx:2004` still resolves
`ps ?? layoutMemCache.get(...) ?? loadDiagramState(...) ?? null`. `persistLayoutFor`'s
`.catch()` falls back to localStorage, which is *third* in that chain, so a
failed PUT leaves a newer local layout that the next load discards for an older
`layout.json`. Both ends are silent. `PersistedState` carries `savedAt` and
nothing compares it.

The user's exact sequence was never reproduced, so the trigger for the failing
PUT is still unidentified — the finding lists the candidates in the order worth
testing. Source: `findings/2026-07-31-blank-canvas-node-drags-revert.md`.

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

### 10b. An edit after a hub study is not tracked (owner-approved: P33b)

Added 2026-09-30 (P28 gate, owner decision O1). The EH review's `stale`
flag is true only when a later foreground solve cleared the stored report
(`services/adequacy/eh_review.py:455-460`). An edit to the network after a
finished study leaves the Guided greeting and the Hub design Results card
unaware. P28 softened the greeting's wording so that it claims nothing about
the current network. The fix is a backend network-revision marker on the
study record (e.g. `network_changed`), read by both surfaces. It is scheduled
as P33b in `plans/2026-09-28-guided-mode-deferred.md` §3.

### 10c. Two narrow project-lock races have correct guards but no test

Added 2026-09-30 (P28 re-gate, mutants R4 and R7). In `moveProjectLock`, a
stale 409 re-acquire that fails after a fresh acquire of the same project
(R4), and a stale failed acquire in an X→Y→Z switch (R7), are both ignored
by the generation guard. Removing either check leaves every test green. Add
one test for each so the guard stays pinned.

### 10a. Re-activating a project does not restore its hub study record

Added 2026-09-30, from the P28 smoke. A project whose Energy Hub study
finished, and which is then left and re-activated
(`POST /api/projects/<p>/activate`), answers `GET /api/results/eh_study`
with no study. The Guided hub rail opens at Site again, and the greeting says
"No study has run yet". The Guided "study done" state is lost for a user who
re-opens a project. Where the record is stored was not traced. The P28 smoke
part (C) works around it by re-running the study. Sources: the plan's "P28
phase note" (contract drift 3) and
`qa/2026-09-30-guided-mode-deferred-gate-P28.md`.

---

## Closed since the 2026-09-12 pass

Listed once, with the closing commit, because the drift this file exists to
prevent was statuses reading *open* for things fixed weeks earlier. Numbering is
NOT reused or compacted — other findings and assessments cite these numbers.

* **2** — `worksheet` / `stress_scenarios` PUTs ignore a foreign edit lock.
  Closed by `68e5f62` (+ `2ede7a4`, tolerating a project with no real uuid).
  Tripwire: `tests/test_worksheet_foreign_lock.py`, which asserts the two
  subjects AND two lock-checked controls. Mutation-verified 2026-09-30: removing
  both guard blocks turns exactly the two subject tests red. **The class is not
  closed — see item 12.**
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
* **12** — `PUT /{name}/asset_health` ignores a foreign edit lock. Opened and
  closed on 2026-09-30: recorded first as a defect outside items 2-4, then fixed
  by `4eca8d2` on a separate instruction. The fourth instance of item 2's defect
  and the second in that file; ungated for 20 days because it landed two days
  before the commit that fixed its two siblings, so it was never in the audit's
  route list. Tripwire: `tests/test_asset_health_foreign_lock.py`, written
  before the fix, red on the property rather than a status code, and
  mutation-verified after. **This closes the class in this router family — all
  three sidecar PUTs now carry the check** — but not the shape that produced it,
  which is item 6.
* **5** — chat sessions have no owner, so the confirmation gate rests on id
  secrecy. Closed by the same commit as item 3: `owner_user_id` plus one
  comparison is exactly the fix this item asked for, so `/confirm` no longer
  depends on the session id being unguessable.

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
