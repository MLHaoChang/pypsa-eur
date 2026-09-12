# Open items

**Verified against the tree on 2026-09-12.** Every entry below was reproduced or
re-read in source on that date, not carried forward on trust.

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

## Medium

### 1. The `exec()` gate is a process-wide flag, and `/simulation/run` has no admin gate

`services/solver_service.py:1446` justifies the gate with "the GUI has no auth
layer" and "single-user / localhost". Both were true when written and neither is
now: there is a global 401 gate (`main.py:658`) and the product is multi-tenant.

The real shape: `PYPSA_GUI_ALLOW_USER_CODE` is **process-wide**, so it cannot be
granted per project or per org; `extra_functionality_code` is an ordinary
`SolverConfig` field set through `PUT /api/simulation/config`'s permissive
`merged.update(submitted)` (`routers/simulation.py:349`); and
`POST /api/simulation/run` (`routers/simulation.py:586`) has no admin gate —
`routers/simulation.py` contains no `Depends` at all, while every privilege check
lives in `routers/admin.py`. With the flag on, any authenticated **member** gets
in-process arbitrary Python with full FS and network access.

Minimum: correct the docstring, and gate the field on org-admin rather than on a
process-wide variable. Source: gap 2 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

### 2. Node positions revert on the blank canvas

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

### 3. Component names are not validated at the edge

Names flow from request bodies into the network, into filenames and into
model-facing text with no character-class check. This is the shared root cause
behind the untrusted-fence bypass (fixed in #18) and the
`Content-Disposition` defect (fixed in #7) — both were symptoms treated at the
sink. `services/upload_service.py`'s `_FILE_ID_RE` shows the right pattern,
anchored and narrow, and it is not applied to names generally. Source: gap 3 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

---

## Low

### 4. A refused `/stream` request still switches the session model

`routers/chat.py:1166` sets `session.model` (and `profile_id`, `bound_wire`)
before `run_turn` reaches the `_turn_in_flight` guard that refuses the request
(`services/chat_service.py:3495`). So a caller who cannot get a turn can still
change which model the *next* turn uses — a rejected request mutating persistent
state. Two fix options in the finding, neither applied; it is a design choice
about where the guard belongs. Source:
`findings/2026-09-10-a-refused-stream-request-still-switches-the-session-model.md`.

### 5. Cookie policy is hardcoded to a preview vendor's hostname

`routers/auth.py::_cookie_flags` returns `SameSite=None; Secure` for any host
matching `.cursorusercontent.com`. `SameSite=None` widens CSRF surface, and here
it is granted by a hostname suffix compiled into the product. Defensible for a
preview environment, not as a permanent rule — the deployment should decide its
own cookie policy through configuration. The CSRF double-submit check
(`main.py:645`) currently carries the load for those sessions. Source: gap 5 of
`assessments/2026-09-10-backend-hardening-assessment.md`.

---

## Verification / CI

### 6. Two CI signals that cannot be trusted

**The `Gridspine` job is path-filtered and reads green while running nothing.**
On PR #18 it "succeeded" in 9 seconds with its steps skipped. A green check mark
therefore does not mean those tests ran. I got this wrong myself on PR #16 and
had to correct it, which is the point: the signal looks like coverage.

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
