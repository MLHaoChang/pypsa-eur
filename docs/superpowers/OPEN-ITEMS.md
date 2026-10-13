# Open items

**Verified against the tree on 2026-09-12.** Every entry below was reproduced or
re-read in source on that date, not carried forward on trust.

**2026-09-29:** four items closed and removed per the convention below — the
user-timeseries tenancy item, and the three follow-ups fixing it uncovered (an
obsolete `persist_user_ts` predicate at three call sites, a hydrate that ignored
its project's `user_ts.json`, and an adequacy-sweep test that could not fail for
its own regression). Their findings carry the detail. Nothing else was re-verified
on that date, so the entries below still date from 2026-09-12.

**2026-10-06:** items 12–14 added from the 3D site view branch's full-product
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

### 2. `worksheet` and `stress_scenarios` PUTs ignore a foreign edit lock

`routers/adequacy_worksheet.py:43-51` and `:65-71` carry `ProjectAccessDep` (ACL)
only — no lock check. They are mounted under `/api/projects`, which is
deliberately absent from the middleware's prefix list because that family is
covered by in-handler enforcement; these two handlers never got it. Verified: with
A holding the lock, `PUT .../layout` and `POST .../uploads` are refused 409 while
`PUT .../worksheet` and `PUT .../stress_scenarios` return 200, replacing the
sidecar wholesale and bumping `version` so A's client treats B's content as
authoritative. Both files are in `_BUNDLE_FILES`, so the write propagates into
bundles and snapshots. Fix: `_check_project_lock(db, _lock_target(project), user)`
on both, as `routers/uploads.py:182,308` does. Server only.

### 3. `/api/chat/{id}/rewind` and `/abort` perform no authorization

`routers/chat.py:1299-1332` declare no user dependency and consult no owner, org
or project; `_SESSIONS` is a process-global whose `ChatSession` has no owner
field. Verified cross-org: a different organization's user called
`POST /api/chat/<victim>/rewind {"turns":2}` → `200 {"dropped":4}`, emptying the
victim's session, and `/abort` → 200 with the victim's `abort_event` set. The
session id is not secret either — `GET /api/chat/history` returns
`last_session_id` to any co-member who activates the project. (`/confirm` is the
same family, practically shielded by a uuid4 token: defence by accident.)
Server only.

### 4. `/api/changelog/` is unscoped for a caller with no OrgMembership

`routers/changelog.py:15-17` returns `None` for a membership-less caller, and
`change_log_service` reads `org_id=None` as "no filter" and, on DELETE, "clear
EVERYTHING" — contradicting both its own docstring and the route's. Verified: an
orgless user reads every tenant's entries and `DELETE /api/changelog/` → 204
destroys org1's audit trail. Membership-less users are the normal case for the
shipped first user: `tools/bootstrap_super_admin.py` creates it with no
OrgMembership. The predicate should be "is a super-admin", not "has no
membership". Server only.


## Medium

### 5. Chat sessions have no owner, so the confirmation gate rests on id secrecy

`ChatSession` (`services/chat_service.py:474`) has no user field and
`get_session` (`:745`) is a plain lookup in a process-global dict, so nothing
compares the caller to the session. `POST /api/chat/{session_id}/confirm` can
therefore supply the approval for **another account's** pending
destructive-tool confirmation — and that approval is the whole protection the
safety tiers give destructive and execution tools. `abort` and `rewind` are
reachable the same way.

**Not currently exploitable:** `session_id` is `uuid.uuid4().hex` (122 bits),
and greps found it neither logged nor persisted into `chat.jsonl`. It is a strong
capability-style secret.

The problem is that this is load-bearing and undeclared. The day a session id
becomes visible — a support endpoint, a debug log line, an error body, a
screenshot — the gate is forgeable with no code change and no review step that
would catch it. An owner field and one comparison removes the dependency.
Source: finding 2 of the same audit.

### 6. `ProjectAccessDep` is adopted by 6 of 23 routers

`routers/deps.py:100` defines the right primitive — a per-route dependency that
resolves the project named by the request and checks the caller's access. It is
used by `compare`, `adequacy_worksheet`, `uploads`, `snapshots`, `gridspine` and
`deps`, and NOT by the five routers carrying most of the surface: `network` (81
routes), `results` (48), `chat` (20), `simulation` (14), `io` (8) — 171 of 267.
Those rely on the path-prefix middleware instead.

This is the structural cause of the route-scoping defects above, and it is a
shape rather than a one-off:
a prefix list denies by omission, so a new router under a new prefix is ungated
by default, silently, with nothing failing. A dependency declared on the route
has the opposite default. Worth a test that fails when a route is mounted under
a prefix no mechanism covers — the omission is what needs to become loud.
Source: finding 3 of the same audit.

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

### 12. A network reset while a project is open lands in the session's scratch slot

**Undo reports success and changes nothing; a raw import is silently lost or
ignored.** `PyPSAService.reset_network()` publishes the new, unbound network
context into `scratch:<sid>`, but while `sessions.active_project_id` is set the
next request re-resolves the *project's* registry slot
(`services/active_project.resolve_for_session`) and never sees it. Affected: raw
imports (`.nc`, CSV, Excel, MATPOWER in `routers/io.py`), undo
(`services/network_undo.apply_undo`), snapshot restore (`routers/snapshots.py`),
and template create / bundle import (`routers/projects.py`), which re-bind but
leave a stale bound copy in scratch that is written back over the project's
saved edits when evicted. Reproduces through the API alone. The backend tests
miss it because `install_network` (`tests/conftest.py`) clears
`active_project_id`, so every test runs with the pointer unset. **Data-loss
class.** A proposed fix (clear the pointer after a raw import; `rekey_context`
after re-binding; pass the acting session through the chat import wrappers) and
five regression tests sit unapplied in `notes/2026-09-30-full-e2e/`; the diff
targets the 2026-09-30 tree and needs porting to today's `routers/io.py`.
Source: `notes/2026-09-29-3d-site-view-phase2-qa.md` §7, defects A–C.

### 13. `login.html` prefills and prints a review credential

`frontend/public/login.html:391-402` ships `admin@example.com` /
`admin-pass-123` as the input values and in a visible "Review account" line, and
auth mode serves that page (`static_gate.decide_route` passes `/login.html`
through). Fine for a review build; a credential in a shipped page otherwise. One
decision (strip before release, or gate the prefill on a build flag) and a test
that the served page carries no password value. Source: the same E2E, "Other
findings".
