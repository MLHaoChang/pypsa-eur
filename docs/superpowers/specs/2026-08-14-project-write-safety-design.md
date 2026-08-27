# Project-write safety (slice 1) — design

2026-08-14. Scope chosen by user from the project-management review: (a) server-side
ProjectLock enforcement, (b) delete confirmation, (c) import-drop confirmation.
Confirm-only delete — no trash/soft-delete in this slice.

## Decisions

| # | Decision | Chosen by |
|---|---|---|
| D1 | Slice 1 (safety) only; integrity fixes (first-save compensation, queue guards, GET side effects) deferred | user |
| D2 | Delete safety = confirmation dialog only; soft-delete/trash deferred | user |
| D3 | Lock enforcement chokepoint = `_save_context` + shared helper in handler bodies + write middleware — **not** a route-decorator dependency (chat `_route` bypasses decorators; background writers bypass routes entirely) | verification |
| D4 | Lock semantics on write = **auto-reacquire**: writer calls `acquire_lock` (idempotent for same user, succeeds when free); 409 `{error_kind: "project_locked", lock}` only when held by another user | verification |
| D5 | Delete confirm = new `ConfirmDialog` wrapper over `Dialog.tsx` (danger style, project-name echo, pending state); cascade-409 re-opens the same dialog with the descendant list — replaces the 8s auto-dismissing `confirmToast` on that path | verification |
| D6 | Import gate lives inside `ImportZone.handleFile` (covers drop + Browse + both mounts). Policy: raw import with a bound project → **silent save-then-import** (Sidebar precedent); bundle-into-current-project → explicit confirm; scratch network with undo depth > 0 → confirm | verification |
| D7 | Local mode: enforcement no-ops via `local_mode.is_local_mode()` per call | Claude, unchallenged by verification |
| D8 | Acquire-on-write stands for save/rename/delete/scenario/members/snapshots (incl. autosave and chat saves — writer becomes holder for TTL); put_layout is check-only; no-op empty PATCH does not gate | final review + controller ruling |

## Verified constraints

All **Verified** (read in code) unless marked otherwise. Paths relative to `pypsa-gui/`.

- Lock is **user-keyed**, not session-keyed: `holder_user_id` (`backend/services/project_locks.py:53,67`). Same user's second session and same-user chat writes pass an enforcement check by design.
- `acquire_lock` is idempotent for the holder and succeeds on a free slot (`project_locks.py:50-72`) → auto-reacquire (D4) needs no new service code.
- `heartbeat_lock` returns False once the row is pruned (`project_locks.py:74-86`); the frontend then falls read-only and **stops pinging** (`frontend/src/utils/projectActions.ts:262-276`). Laptop sleep > 120s currently strands the holder; D4 un-strands them on next write.
- Resident contexts are shared per `(org_id, project_uuid)` (`backend/services/project_registry.py:147-156`) → `/api/network/*` and `/api/io/*` mutations from a non-holder edit the holder's in-memory network. Route-edge locks alone are cosmetic; the write middleware (which already gates solver-in-flight, `backend/main.py:99-107` region) must also enforce.
- Chat tool calls invoke route handlers as plain functions; an unsatisfiable `Depends` raises `RuntimeError` (`backend/services/chat_tools.py:1494-1527`). Decorator-level enforcement never runs for chat; signature-level breaks chat. Hence D3's shared helper called from handler bodies.
- Solve-queue dispatcher imports `_save_context` and saves on a background thread (`backend/services/solve_queue.py:546-551`); LRU eviction write-back also reaches `_save_context` (`backend/services/pypsa_service.py:134-142`). Both bypass any route-level check.
- First save creates the DB row (`backend/routers/projects.py:1292-1296`) — enforcement must allow row-absent projects (nothing to lock yet).
- Undo capture covers only `/api/network/` and `/api/io/` (`backend/main.py:92`); `/api/projects/import_bundle` and `/api/simulation/*` are not captured. Raw drop-imports are one Ctrl+Z away; bundle imports are not.
- `ImportZone` owns both entry paths — drop (`frontend/src/pages/ImportExport.tsx:150`) and Browse (`:174`) — through `handleFile` (`:144`), and is mounted twice: `ImportExport.tsx:241` and `layout/Sidebar.tsx:397`. The file is captured synchronously before any await, so a confirm before `mutate` is drag-lifetime-safe.
- Silent-save precedent: `Sidebar.tsx:1012` (`handleOpenFromFile`) runs `saveProjectQuietly(currentProject)` before a destructive bundle import.
- Delete entry points: the only unconfirmed one is the ScenariosPanel trash icon (`frontend/src/pages/ScenariosPanel.tsx:801` → handler `:623-676`). Chat's `delete_project` already requires typed confirmation (`frontend/src/components/ChatPanel.tsx:263`). CommandPalette/home page have no delete.
- Cascade path today: 409 → `confirmToast` with default `duration = 8000` (`ScenariosPanel.tsx:663-668`, `utils/toasts.tsx:31`).
- `Dialog.tsx` provides role/aria-modal, focus trap, restore, Escape (`frontend/src/components/Dialog.tsx`) but no danger/typed-confirm/pending API — wrapper needed (D5). Its Escape listener is capture-phase with `stopPropagation` — do not stack over the scenario edit/create dialogs.
- Test that pins current (confirm-less) delete behavior and must change: `ScenariosPanel.test.tsx:180-207`.

## Workstreams

**WS1 — backend lock enforcement.** Estimate: 2.5–4 days (Assumed; moves if the
middleware gate interacts badly with the solver-in-flight 409 path).
1. `enforce_project_lock(db, project, user)` helper in `project_locks.py`: local-mode no-op; row-absent no-op; `acquire_lock` else 409 `{error_kind: "project_locked", lock}`. (TDD: service-level tests first.)
2. Call it from handler bodies: save, rename, delete, scenario-patch, layout PUT, members PUT, snapshot create/restore/delete. Chat `_route` reaches the same bodies — no `_route` change needed.
3. Write-middleware gate for `/api/network/*` + `/api/io/*` mutations when the session's active project has a foreign lock (same shape as the solver-in-flight refusal).
4. Solve-queue: refuse enqueue with 409 when another user holds the lock; completion save and eviction write-back stay exempt (system persistence of already-authorized state — blocking them loses data).
5. Frontend: map `project_locked` to the read-only banner + a re-acquire attempt (not toast spam); heartbeat-loss path retries acquire instead of going permanently read-only.

**WS2 — delete confirmation.** Estimate: 0.5–1 day (Assumed).
1. `ConfirmDialog` wrapper over `Dialog.tsx`: danger styling, name echo, async-pending (confirm disabled while `deleteMut.isPending`).
2. ScenariosPanel: trash click → dialog; cascade-409 → same dialog re-opened with `describeDescendants` list; lighter copy for `missing` rows. Update `ScenariosPanel.test.tsx:180-207`.

**WS3 — import gate.** Estimate: 0.5–1 day (Assumed).
1. In `ImportZone.handleFile`: bound project + raw file → `saveProjectQuietly` then import; bound project + `.pypsaproj.zip` → ConfirmDialog ("replaces *name*'s contents"); no project + undo depth > 0 → confirm; else import directly.
2. Reuse WS2's ConfirmDialog. Component tests for the three branches (jsdom pins logic only; WKWebView drop behavior needs the manual acceptance pass — see webkit memory).

Order: WS2 → WS3 (shared dialog), WS1 independent/parallel. TDD throughout per standing rules.

## Risks

| Failure mode | Mitigation |
|---|---|
| Middleware lock gate 409s the holder after laptop sleep | D4 auto-reacquire inside the gate, same as route edges |
| Enforcement breaks chat-driven saves | Helper-in-body placement; chat integration test on save/rename/delete |
| Logout in one tab releases the lock the user's other session holds (`routers/auth.py:202`) | Accept: next write auto-reacquires (same user) |
| Confirm fatigue on import | D6 uses silent-save (no prompt) for the recoverable raw case; prompts only for the two unrecoverable ones |
| Cascade dialog regression for keyboard/screen-reader users | Reuse Dialog primitives; manual a11y pass per modal-a11y doc |

## Open items

- A future read-only ACL tier would re-open lock-acquisition DoS (security F3, downgraded because no such tier exists today — `project_acl.py` has admin/member only) — revisit acquisition tier then; admin force-release / hold-cap deferred to slice 2.
- ~~**Uploads write edges are outside the lock's coverage**~~ — **CLOSED 2026-08-19/27.**
  Gated by `2c2bf504`; corrected to CHECK-only by `f977c3c5` (see below). Original entry kept for the
  reasoning, which still explains why the gap was invisible:
  `POST /{name}/uploads` (`routers/uploads.py:138`) and `DELETE /{name}/uploads/{file_id}` (`:256`), mounted at
  `/api/projects` (`main.py:1045`), both write into `project.directory`. Neither calls `_enforce_project_lock`,
  and the middleware does not reach them either: `_FOREIGN_LOCK_GATE_PREFIXES` (`main.py:115`) is
  `/api/network/`, `/api/io/`, `/api/simulation/` — `/api/projects/` is absent.
  Triage it correctly in BOTH directions: `ProjectAccessDep` still resolves within the caller's org and
  ACL-gates the project, so there is no cross-org reach and this is not a security finding. It IS a
  consistency bug: a non-holder can add or delete files in a project another user holds the edit lock on, and
  DELETE is the sharp end — it can remove a file another session is actively referencing.
  The asymmetry that hides it: `/api/projects/` IS in `_SOLVER_BLOCKING_PREFIXES` (`main.py:219`), so these
  routes are guarded against a solve-in-flight but not against another user's lock. Same path, two guards,
  one applied — which is why reading either guard alone reads as complete.
- ~~**Preflight failure renders as a clean bill of health**~~ — **CLOSED 2026-08-19** by `f0af0a63`
  (`IssuesPanel.tsx` + `Sidebar.tsx` now distinguish "could not check" from "no issues"); ADR-0001 entry
  `8f1f6a59`. The same defect at the StatusBar save-state dot was closed by `7cedd7a8` on 2026-08-27 —
  worth noting that the second instance was NOT found by fixing the first, which is why the ADR matters
  more than either fix. Original entry kept for the reasoning:
  `layout/Sidebar.tsx:1223-1224` computes `issueCount = (preflight?.errors ?? 0) + (preflight?.warnings ?? 0)`
  and never consults the query's error state; `pages/IssuesPanel.tsx` renders the same response, and its own
  comment promises "if this panel is empty the solver run will not fail on pre-checks". So a REFUSED preflight
  (any cause: 409, 503, auth, network, a future guard) is indistinguishable from a clean one — the badge reads
  zero and the panel empties, asserting there are no problems when the answer was merely never obtained.
  This is `docs/adr/0001-unresolvable-figures-ship-as-null.md` exactly ("a defaulted zero ... silently converts
  'we could not compute this' into 'we computed this and it is nothing'"), wearing a badge instead of a
  currency figure. Exempting preflight from the lock gate (`bc14189c`) removes the current trigger and HIDES
  this defect; it does not fix it. The fix is to distinguish "no issues" from "could not check" at both
  consumers. Recorded separately on purpose: folded into the gate fix, the symptom disappears and the defect
  survives. Credit: raised cross-session by the assistant-dock and coordinator sessions, 2026-08-18.

- **`_enforce_project_lock` vs `_check_project_lock` (2026-08-27).** Acquire-on-write is for edges that ARE
  the edit; CHECK-only is for incidental writes (uploads, `put_layout`). `2c2bf504` used the wrong one and
  an attachment upload claimed a 120 s lock — caught only by the FULL suite, as cross-file pollution, with
  79 targeted tests green. Fixed in `f977c3c5`; `a0cf3948` collapsed `put_layout`'s duplicate copy of the
  predicate onto the shared helper. When adding a gated write edge, pick deliberately and state which.

- **TOCTOU residual on the uploads guard, accepted, not a defect (2026-08-27).** `_safe_file_dir` resolves
  and returns; `rmtree` re-walks. A symlink swapped in between would defeat the containment check. The
  precondition is local write access to the uploads dir — strictly more access than the bug it guards —
  so it is not worth code. Recorded so it is not rediscovered as novel.

- Unsaved-but-undoless dirt: solver results (`/api/simulation/*` not undo-captured) on a scratch network won't trigger the import confirm. Accepted gap this slice; noting for slice 2.
- CommandPalette snapshot restore (`CommandPalette.tsx:522-536`), Sidebar same-name destructive re-load (`Sidebar.tsx:950-956`), and `App.tsx:441` reload remain unconfirmed destructive ops — named out of scope (candidate follow-up: reuse ConfirmDialog).
- Web deploy with auth off but not local mode would diverge from the frontend's `authEnabled` no-op keying (`frontend/src/auth/config.ts:7`). No such deployment exists today.
- `activate` (pointer swap + eviction) and unclaimed/folder import dir-moves left unenforced this slice — they don't overwrite a locked project's content directly.

## Concurrency

Checked 2026-08-14: 24 live claude processes; dirty paths in the shared tree are
`pypsa-gui/CONTEXT.md`, `frontend/src/utils/{attributeCatalog,gridEdit,assetWrite}*`
plus plan doc `docs/superpowers/plans/2026-08-14-asset-write-chokepoint.md` — an
asset-write chokepoint workstream. **No file overlap** with this slice's targets
(none of this spec's files modified within the last hour; newest 2026-08-13 11:18).
Premise overlap noted: that session is also building a write chokepoint (asset
edits, frontend); WS1's middleware gate touches `backend/main.py`, which that
session's plan may also touch — re-run the concurrency check before starting WS1
and read their plan doc first. Decision: proceed with spec now; re-check at
implementation start.
