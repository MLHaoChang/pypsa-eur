# Project-Write Safety (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Server-side ProjectLock enforcement at the real write edges, a ConfirmDialog
replacing one-click project delete, and a guarded ImportZone that can no longer silently
destroy work.

**Architecture:** Backend enforcement is a raising helper called from *handler bodies*
(never route decorators — chat's `_route` bypasses those) plus a foreign-lock check in the
existing write middleware and at solve-queue enqueue. Lock semantics are auto-reacquire:
`acquire_lock` is already idempotent for the holder and succeeds on a free slot, so a
lapsed holder's next write re-acquires instead of stranding them. Frontend ships one shared
`ConfirmDialog` over the existing accessible `Dialog` primitive, used by ScenariosPanel
delete and ImportZone.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + React Query + Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-08-14-project-write-safety-design.md`

## Global Constraints

- All paths below relative to `pypsa-gui/` unless noted; repo root is `pypsa-eur/`.
- **Backend tests go into EXISTING files** under `backend/tests/` (new files break
  concurrent pytest collection — standing rule). Frontend test files may be new.
- Backend test run: `cd <repo-root> && pixi run python -m pytest "pypsa-gui/backend/tests/<file>.py" -v`.
  Full gate before finishing: `pixi run gui-tests` (7 webview failures = wrong env, rerun via pixi).
- Frontend: `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx vitest run <file>`
  and `npx tsc --noEmit -p tsconfig.json` with the same PATH.
- Commit path-limited (`git commit -- <paths>`, never `git add -A`); run
  `git branch --show-current` before EVERY commit — other sessions move this branch.
- Before Task 4 (first `main.py`/backend task): re-run `git status --short` +
  mtime check on `backend/main.py` — a concurrent session may be in it.
- Local mode: every backend gate no-ops when `local_mode.is_local_mode()` is true.
- 409 detail shape must match the existing lock routes exactly:
  `{"error_kind": "project_locked", "message": ..., "lock": _serialize_project_lock(db, project.id, user)}`
  (see `backend/routers/projects.py:2104-2112`).

---

### Task 1: ConfirmDialog component

**Files:**
- Create: `frontend/src/components/ConfirmDialog.tsx`
- Test: `frontend/src/components/ConfirmDialog.test.tsx` (new file — allowed on frontend)

**Interfaces:**
- Consumes: `Dialog` from `./Dialog` (`open`, `onClose`, `title`, `dismissOnBackdrop`).
- Produces: `ConfirmDialog({ open, title, message, confirmLabel, danger?, pending?, onConfirm, onCancel })`
  — Tasks 2 and 3 import exactly this.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/ConfirmDialog.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'

describe('ConfirmDialog', () => {
  it('renders message and fires onConfirm', async () => {
    const onConfirm = vi.fn()
    render(
      <ConfirmDialog open title="Delete project" message="Delete 'Alpha'? This removes its files from disk."
        confirmLabel="Delete" danger onConfirm={onConfirm} onCancel={() => {}} />,
    )
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/removes its files/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('disables both buttons and blocks Escape while pending', async () => {
    const onCancel = vi.fn()
    render(
      <ConfirmDialog open title="Delete project" message="working" confirmLabel="Delete"
        pending onConfirm={() => {}} onCancel={onCancel} />,
    )
    expect(screen.getByRole('button', { name: /Working/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('renders nothing when closed', () => {
    render(
      <ConfirmDialog open={false} title="x" message="y" confirmLabel="z"
        onConfirm={() => {}} onCancel={() => {}} />,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/components/ConfirmDialog.test.tsx` (with pixi PATH)
Expected: FAIL — module `./ConfirmDialog` not found.

- [ ] **Step 3: Implement**

```tsx
// frontend/src/components/ConfirmDialog.tsx
import type { ReactNode } from 'react'
import { Dialog } from './Dialog'

// Shared confirmation for destructive actions. Deliberately a Dialog, not a
// confirmToast: toasts auto-dismiss (8 s) and double-fire on double-click —
// both wrong for a destructive decision.
interface ConfirmDialogProps {
  open: boolean
  title: string
  message: ReactNode
  confirmLabel: string
  danger?: boolean
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open, title, message, confirmLabel,
  danger = false, pending = false, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const close = () => { if (!pending) onCancel() }
  return (
    <Dialog open={open} onClose={close} title={title} dismissOnBackdrop={!pending}>
      <div className="p-4 flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-text">{title}</h2>
        <div className="text-sm text-muted">{message}</div>
        <div className="flex justify-end gap-2 mt-2">
          <button
            className="px-3 py-1.5 text-xs border border-border rounded text-text hover:border-accent disabled:opacity-50"
            onClick={onCancel} disabled={pending}
          >
            Cancel
          </button>
          <button
            className={`px-3 py-1.5 text-xs rounded text-white disabled:opacity-50 ${danger ? 'bg-red-600 hover:bg-red-500' : 'bg-accent hover:opacity-90'}`}
            onClick={onConfirm} disabled={pending}
          >
            {pending ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
```

Note: `Dialog`'s own Escape handler calls `onClose`; passing the `close` wrapper (no-op
while pending) is what makes the pending test pass. Do not stack this over
EditScenarioDialog/CreateScenarioDialog — Dialog's capture-phase Escape stops propagation.

- [ ] **Step 4: Run test to verify it passes** — same command, expected PASS.
- [ ] **Step 5: Typecheck** — `npx tsc --noEmit -p tsconfig.json` (pixi PATH), expected clean.
- [ ] **Step 6: Commit**

```bash
git branch --show-current   # confirm before committing
git commit -m "feat(frontend): ConfirmDialog — dialog-based destructive confirm" \
  -- pypsa-gui/frontend/src/components/ConfirmDialog.tsx pypsa-gui/frontend/src/components/ConfirmDialog.test.tsx
```

---

### Task 2: ScenariosPanel delete confirmation

**Files:**
- Modify: `frontend/src/pages/ScenariosPanel.tsx` (delete mutation `:623-676`, trash
  callback `:801`, plus dialog state + render)
- Test: `frontend/src/pages/ScenariosPanel.test.tsx` (rewrite the two delete tests at
  `:180-207`, add a no-mutate-before-confirm test)

**Interfaces:**
- Consumes: `ConfirmDialog` from Task 1; existing `describeDescendants(detail, name)`,
  `guardMutation`, `apiIdFor`, `deleteMut` in the same file.
- Produces: nothing downstream; the cascade `confirmToast` at `:663-668` is REMOVED.

- [ ] **Step 1: Rewrite the pinned tests to the dialog flow (failing first)**

Replace the two tests at `ScenariosPanel.test.tsx:180-207` and add one:

```tsx
it('does not delete until the dialog is confirmed', async () => {
  renderPanel()
  const row = await rowFor('base')
  await userEvent.click(within(row).getByTitle('Delete this scenario'))
  expect(vi.mocked(projectsApi.delete)).not.toHaveBeenCalled()
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(vi.mocked(projectsApi.delete)).toHaveBeenCalledWith('id-base', false))
})

it('shows descendants in the same dialog on 409 and retries with cascade', async () => {
  vi.mocked(projectsApi.delete).mockRejectedValueOnce({
    response: {
      status: 409,
      data: { detail: { error_kind: 'descendants_exist', message: 'Pass ?cascade=true …', descendants: ['variant'] } },
    },
  })
  renderPanel()
  const row = await rowFor('base')
  await userEvent.click(within(row).getByTitle('Delete this scenario'))
  await userEvent.click(await screen.findByRole('button', { name: 'Delete' }))
  // 409 → dialog re-opens with the descendant list, never a toast
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText(/variant/)).toBeInTheDocument()
  expect(within(dialog).queryByText(/cascade=true/)).not.toBeInTheDocument()
  vi.mocked(projectsApi.delete).mockResolvedValue({ deleted: ['base', 'variant'], failed: [] } as never)
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete all' }))
  await waitFor(() => expect(vi.mocked(projectsApi.delete)).toHaveBeenCalledWith('id-base', true))
})
```

Keep the existing `confirmToast` mock in the file's setup — it must now go UNUSED by the
delete path (add `expect(vi.mocked(confirmToast)).not.toHaveBeenCalled()` to the cascade test).

- [ ] **Step 2: Run to verify both fail** — `npx vitest run src/pages/ScenariosPanel.test.tsx`.
  Expected: FAIL (no dialog appears; delete fires immediately).

- [ ] **Step 3: Implement**

In `ScenariosPanel.tsx`:

(a) Add state next to the other dialog states:

```tsx
const [deleting, setDeleting] = useState<{ id: string; name: string; cascade: boolean; message: string } | null>(null)
```

(b) Change the trash callback at `:801` from direct mutate to:

```tsx
onDelete={(name) => {
  if (!guardMutation(name)) return
  const missing = projectList.find(p => p.name === name)?.missing
  setDeleting({
    id: apiIdFor(name), name, cascade: false,
    message: missing
      ? `Remove the registry entry for '${name}'? Its files are already gone.`
      : `Delete '${name}'? This removes its files from disk.`,
  })
}}
```

(If `ProjectInfo` has no `missing` field, check `frontend/src/api/types.ts` for the actual
flag the missing-folder card uses — `ProjectsHomePage.missing.test.tsx` names it — and use
that; if none exists on this list shape, drop the lighter-copy branch entirely.)

(c) In `deleteMut.onSuccess` add `setDeleting(null)` as the first line. In `onError`,
replace the `confirmToast` branch at `:663-668` with:

```tsx
if (e.response?.status === 409 && !params.cascade) {
  setDeleting({
    id: params.id, name: params.name, cascade: true,
    message: describeDescendants(e.response.data?.detail, params.name),
  })
  return
}
setDeleting(null)
```

(d) Render next to the other dialogs:

```tsx
<ConfirmDialog
  open={deleting != null}
  title={deleting?.cascade ? 'Delete project and descendants' : 'Delete project'}
  message={deleting?.message ?? ''}
  confirmLabel={deleting?.cascade ? 'Delete all' : 'Delete'}
  danger
  pending={deleteMut.isPending}
  onConfirm={() => deleting && deleteMut.mutate({ id: deleting.id, name: deleting.name, cascade: deleting.cascade })}
  onCancel={() => setDeleting(null)}
/>
```

- [ ] **Step 4: Run to verify pass** — same command; also run the FULL file (other tests
  must stay green) and `npx tsc --noEmit`.
- [ ] **Step 5: Commit**

```bash
git branch --show-current
git commit -m "feat(frontend): project delete requires ConfirmDialog; cascade re-uses it" \
  -- pypsa-gui/frontend/src/pages/ScenariosPanel.tsx pypsa-gui/frontend/src/pages/ScenariosPanel.test.tsx
```

---

### Task 3: ImportZone guard

**Files:**
- Modify: `frontend/src/pages/ImportExport.tsx` (`ImportZone`, `:91-180`)
- Test: `frontend/src/pages/ImportExport.test.tsx` (new file)

**Interfaces:**
- Consumes: `ConfirmDialog` (Task 1), `saveProjectQuietly` from `../utils/projectActions`
  (`export async function saveProjectQuietly(name: string, clearUndo = false): Promise<boolean>`,
  `projectActions.ts:433`), `networkApi.undoInfo()` → `{ depth: number }` (`api/network.ts:249`).
- Produces: nothing downstream. Both mounts (`ImportExport.tsx:241`, `layout/Sidebar.tsx:397`)
  get the guard for free because it lives inside `ImportZone.handleFile`.

Policy (spec D6):
1. Bound project + `.pypsaproj.zip` → ConfirmDialog (bundle overwrites the project's
   contents and is NOT undo-captured).
2. Bound project + raw file (`.nc/.xlsx/.zip/.m`) → `saveProjectQuietly(currentProject)`
   first, then import, no prompt (raw imports are undo-captured; the save preserves the
   outgoing state — mirrors `Sidebar.tsx:1012`).
3. No project + undo depth > 0 → ConfirmDialog.
4. Otherwise import directly.

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/src/pages/ImportExport.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ImportZone } from './ImportExport'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { ioApi } from '../api/io'
import { networkApi } from '../api/network'
import { saveProjectQuietly } from '../utils/projectActions'

vi.mock('../api/projects')
vi.mock('../api/io')
vi.mock('../api/network')
vi.mock('../utils/projectActions', () => ({ saveProjectQuietly: vi.fn().mockResolvedValue(true) }))

function renderZone() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ImportZone onSuccess={() => {}} />
    </QueryClientProvider>,
  )
}

// The Browse <input> path exercises the same handleFile seam as drop.
async function pickFile(name: string) {
  const file = new File(['x'], name)
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(input, file)
  return file
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(saveProjectQuietly).mockResolvedValue(true)
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0 })
  vi.mocked(ioApi.importNetcdf).mockResolvedValue({} as never)
  vi.mocked(projectsApi.importBundle).mockResolvedValue({ imported: 'Alpha', summary: {} } as never)
})

describe('ImportZone guard', () => {
  it('bundle onto a bound project asks before importing', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    renderZone()
    await pickFile('other.pypsaproj.zip')
    expect(projectsApi.importBundle).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(/replace the contents of 'Alpha'/)
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(projectsApi.importBundle).toHaveBeenCalled())
  })

  it('raw import with a bound project silently saves first, no dialog', async () => {
    useUIStore.setState({ currentProject: 'Alpha' })
    renderZone()
    await pickFile('grid.nc')
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
    expect(saveProjectQuietly).toHaveBeenCalledWith('Alpha')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('scratch network with undo depth > 0 asks first', async () => {
    useUIStore.setState({ currentProject: null })
    vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 3 })
    renderZone()
    await pickFile('grid.nc')
    expect(ioApi.importNetcdf).not.toHaveBeenCalled()
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
  })

  it('clean scratch network imports without any prompt', async () => {
    useUIStore.setState({ currentProject: null })
    renderZone()
    await pickFile('grid.nc')
    await waitFor(() => expect(ioApi.importNetcdf).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(saveProjectQuietly).not.toHaveBeenCalled()
  })
})
```

(jsdom pins the branch logic only; WKWebView drop behavior still needs the manual
acceptance pass per the standing webkit memory. If `useUIStore.setState` needs more keys,
mirror whatever `ScenariosPanel.test.tsx`'s setup does.)

- [ ] **Step 2: Run to verify fail** — `npx vitest run src/pages/ImportExport.test.tsx`.
  Expected: FAIL — imports fire immediately, no dialog, no quiet save.

- [ ] **Step 3: Implement**

In `ImportZone` (`ImportExport.tsx:91`):

```tsx
const [pendingImport, setPendingImport] = useState<{ file: File; message: string } | null>(null)

const handleFile = useCallback(async (file: File) => {
  const isBundle = file.name.toLowerCase().endsWith('.pypsaproj.zip')
  if (currentProject && isBundle) {
    // Bundle-into-current overwrites the project's contents and is NOT
    // undo-captured (backend _UNDO_PREFIXES excludes /api/projects/) — ask.
    setPendingImport({
      file,
      message: `Importing this bundle will replace the contents of '${currentProject}'.`,
    })
    return
  }
  if (currentProject && !isBundle) {
    // Raw import is undo-captured; persist the outgoing project first so
    // nothing is lost, then proceed without a prompt (Sidebar precedent).
    await saveProjectQuietly(currentProject)
    importMut.mutate(file)
    return
  }
  let depth = 0
  try { depth = (await networkApi.undoInfo()).depth } catch { /* unreachable backend: fall through */ }
  if (depth > 0) {
    setPendingImport({ file, message: 'The current unsaved network will be replaced.' })
    return
  }
  importMut.mutate(file)
}, [importMut, currentProject])
```

Add imports for `saveProjectQuietly`, `networkApi`, `ConfirmDialog`. Render inside the
zone's root `<div>` (after the Browse label):

```tsx
<ConfirmDialog
  open={pendingImport != null}
  title="Replace current network"
  message={pendingImport?.message ?? ''}
  confirmLabel="Import"
  danger
  pending={importMut.isPending}
  onConfirm={() => { if (pendingImport) { importMut.mutate(pendingImport.file); setPendingImport(null) } }}
  onCancel={() => setPendingImport(null)}
/>
```

The `File` is captured synchronously in `onDrop`/`onChange` before any await, so holding
it across the dialog is safe (dataTransfer lifetime does not apply to the extracted File).

- [ ] **Step 4: Run to verify pass** + `npx tsc --noEmit`. Also run
  `npx vitest run src/layout` — Sidebar tests stub ImportZone but must stay green.
- [ ] **Step 5: Commit**

```bash
git branch --show-current
git commit -m "feat(frontend): ImportZone guards destructive imports (confirm or save-first)" \
  -- pypsa-gui/frontend/src/pages/ImportExport.tsx pypsa-gui/frontend/src/pages/ImportExport.test.tsx
```

---

### Task 4: Backend lock-enforcement helper

**Files:**
- Modify: `backend/routers/projects.py` (new `_enforce_project_lock` near
  `_serialize_project_lock` at `:651`)
- Test: `backend/tests/test_project_locks.py` (EXISTING file — append)

**Interfaces:**
- Consumes: `services.project_locks.acquire_lock(db, project_id, user_id)` (idempotent for
  holder, succeeds on free slot, `project_locks.py:42-72`); `local_mode.is_local_mode()`;
  `_serialize_project_lock(db, project_id, user)`.
- Produces: `_enforce_project_lock(db, project, user) -> None` — raises
  `HTTPException(409, {"error_kind": "project_locked", ...})` when another user holds the
  lock. Tasks 5–6 call it; snapshots router imports it from `routers.projects`.

**Concurrency check first:** `git status --short` + `ls -lt backend/routers/projects.py backend/main.py` —
another session may be mid-edit; if mtime is inside the last hour, read their in-flight work before proceeding.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_project_locks.py`;
  reuse its existing db/user fixtures — read the file's fixture names first and match them):

```python
def test_enforce_allows_free_and_makes_caller_holder(db, user_a, project_row):
    from routers.projects import _enforce_project_lock
    from services import project_locks

    _enforce_project_lock(db, project_row, user_a)  # no raise
    lock = project_locks.get_lock(db, project_row.id)
    assert lock is not None and lock.holder_user_id == user_a.id


def test_enforce_409_when_foreign_holder(db, user_a, user_b, project_row):
    from fastapi import HTTPException
    from routers.projects import _enforce_project_lock
    from services import project_locks

    assert project_locks.acquire_lock(db, project_row.id, user_b.id) is not None
    with pytest.raises(HTTPException) as exc:
        _enforce_project_lock(db, project_row, user_a)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "project_locked"
    assert "lock" in exc.value.detail


def test_enforce_reacquires_after_expiry(db, user_a, user_b, project_row):
    from datetime import datetime, timedelta, timezone
    from routers.projects import _enforce_project_lock
    from services import project_locks
    from db.models import ProjectLock

    project_locks.acquire_lock(db, project_row.id, user_b.id)
    row = db.get(ProjectLock, project_row.id)
    row.expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
    db.commit()
    _enforce_project_lock(db, project_row, user_a)  # expired lock pruned, A takes over
    assert project_locks.get_lock(db, project_row.id).holder_user_id == user_a.id


def test_enforce_noops_in_local_mode(db, user_a, user_b, project_row, monkeypatch):
    import routers.projects as projects_router
    from services import project_locks

    project_locks.acquire_lock(db, project_row.id, user_b.id)
    monkeypatch.setattr(projects_router.local_mode, "is_local_mode", lambda: True)
    projects_router._enforce_project_lock(db, project_row, user_a)  # no raise
```

If the existing file has no two-user + project-row fixtures, borrow the construction used
in `backend/tests/test_projects_tenancy.py` inline (create users/org/project rows via the
same helpers that file uses) rather than adding a conftest.

These direct-function-call tests are ALSO the chat-path regression (spec risk table):
chat's `_route` invokes route handlers as plain functions with injected `db`/`user`
(`services/chat_tools.py:1494-1527`), which is exactly what these tests do — a gate that
passes them runs for chat-driven saves too.

- [ ] **Step 2: Run to verify fail**

Run: `pixi run python -m pytest "pypsa-gui/backend/tests/test_project_locks.py" -v`
Expected: FAIL — `_enforce_project_lock` does not exist. Existing 6 tests stay green.

- [ ] **Step 3: Implement** (in `routers/projects.py`, directly below
  `_serialize_project_lock`; add `import local_mode` at the top of the file if absent —
  check how `main.py` imports it and match):

```python
def _enforce_project_lock(db: DBSession, project, user) -> None:
    """
    Write-edge lock gate (design D3/D4). Called from HANDLER BODIES — never a
    route decorator: chat's `_route` invokes handlers as plain functions, so a
    decorator dependency would silently never run for chat-driven writes.

    Auto-reacquire semantics: `acquire_lock` is idempotent for the current
    holder and succeeds on a free slot, so a holder whose heartbeat lapsed
    (laptop sleep) is re-armed by their next write instead of stranded. Only a
    live lock held by a DIFFERENT user raises.
    """
    if local_mode.is_local_mode():
        return
    if project is None or user is None:
        # First save creates the row after this point; nothing to lock yet.
        return
    from services import project_locks

    if project_locks.acquire_lock(db, project.id, user.id) is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "project_locked",
                "message": f"'{project.name}' is being edited by another user.",
                "lock": _serialize_project_lock(db, project.id, user),
            },
        )
```

- [ ] **Step 4: Run to verify pass** — same command, all tests green.
- [ ] **Step 5: Commit**

```bash
git branch --show-current
git commit -m "feat(backend): _enforce_project_lock — auto-reacquire write gate" \
  -- pypsa-gui/backend/routers/projects.py pypsa-gui/backend/tests/test_project_locks.py
```

---

### Task 5: Enforce at the project write edges

**Files:**
- Modify: `backend/routers/projects.py` — `save_project` (`:1257`), `rename_project`
  (`:2731`), `delete_project` (`:2639`), `update_scenario_metadata` (`:2463`),
  `put_layout` (`:2933`), `set_members` (`:3047`)
- Modify: `backend/routers/snapshots.py` — snapshot create / restore / delete handlers
  (`:382-616`; the list/read handlers get NO gate)
- Modify: `backend/routers/solve_queue.py` — `enqueue_solve` (`:76`)
- Test: `backend/tests/test_projects_tenancy.py` (EXISTING file — append)

**Interfaces:**
- Consumes: `_enforce_project_lock(db, project, user)` from Task 4
  (`from routers.projects import _enforce_project_lock` in snapshots.py / solve_queue.py —
  cross-router import of projects helpers is established practice, see
  `solve_queue.py`'s dispatcher imports).
- Produces: 409 `error_kind: "project_locked"` on every listed route when a foreign user
  holds the lock. Task 8's frontend mapping relies on exactly this shape.

- [ ] **Step 1: Write the failing tests** (append to `test_projects_tenancy.py`, reusing
  its existing two-user API-client fixtures — read the file first and use its actual
  fixture/client names):

```python
def test_save_409s_when_other_user_holds_lock(client_a, client_b, shared_project):
    r = client_a.post(f"/api/projects/{shared_project['id']}/lock")
    assert r.status_code == 200
    r = client_b.post(f"/api/projects/{shared_project['name']}")
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "project_locked"


def test_rename_and_delete_409_under_foreign_lock(client_a, client_b, shared_project):
    client_a.post(f"/api/projects/{shared_project['id']}/lock")
    assert client_b.post(
        f"/api/projects/{shared_project['name']}/rename", json={"new_name": "Taken"}
    ).status_code == 409
    assert client_b.delete(f"/api/projects/{shared_project['name']}").status_code == 409


def test_holder_still_saves_and_free_project_saves(client_a, shared_project):
    client_a.post(f"/api/projects/{shared_project['id']}/lock")
    assert client_a.post(f"/api/projects/{shared_project['name']}").status_code in (200, 409)
    # 409 only if the save gate other than the lock (e.g. empty network) fires —
    # assert specifically that the lock is NOT the reason:
    r = client_a.post(f"/api/projects/{shared_project['name']}")
    if r.status_code == 409:
        assert r.json()["detail"].get("error_kind") != "project_locked"


def test_enqueue_409s_when_other_user_holds_lock(client_a, client_b, shared_project):
    client_a.post(f"/api/projects/{shared_project['id']}/lock")
    r = client_b.post("/api/simulation/queue", json={"project_id": shared_project["id"]})
    assert r.status_code == 409
    assert r.json()["detail"]["error_kind"] == "project_locked"
```

(Adapt the queue path/body field to `EnqueueRequest`'s real shape — read
`routers/solve_queue.py:60-80`. `shared_project` = a project both users can access; if the
file has no such fixture, build it with the same org-membership helpers its other tests use.
The save test needs a non-empty in-memory network for user B or the empty-network 409 fires
first — mirror how the file's existing save tests arrange that; if none do, keep only the
rename/delete/enqueue assertions, which don't serialize.)

- [ ] **Step 2: Run to verify fail** — routes return 200 (no gate yet).

- [ ] **Step 3: Implement.** One line per handler, placed AFTER the project row is
  resolved and access-checked, BEFORE the first mutation:

- `save_project`: after `storage_dir = project_registry.ensure_project_dir(project)`
  (`:1300`), add `_enforce_project_lock(db, project, user)`. (Both branches — found row and
  just-created root — have `project` bound; a just-created root's acquire always succeeds.)
- `rename_project`, `delete_project`, `update_scenario_metadata`, `set_members`: after
  their resolve/`can_delete_project`/`can_manage_membership` checks.
- `put_layout`: it resolves via `_resolve_project_src` (a path, not a row). Resolve the row
  first: `project = project_registry.find_project(db, user, name)`, then
  `if project is not None: _enforce_project_lock(db, project, user)` (row-absent = legacy
  layout path, ungated).
- `snapshots.py` create/restore/delete: these already carry an `AuthorizedProject` (with
  `.id`/`.uuid` and org) via `ProjectAccessDep` — call
  `_enforce_project_lock(db, project, user)` with that object; confirm its attribute
  carrying the DB uuid (the lock table keys on `Project.id`) and pass a shim object or the
  underlying row accordingly.
- `enqueue_solve` (`solve_queue.py:100`): after `resolve_project`, a CHECK not an acquire —
  enqueueing on your own locked project must not steal or extend anything, and an
  unlocked project must stay unlocked (the dispatcher save is exempt by design):

```python
    from services import project_locks
    lock = project_locks.get_lock(db, project.id)
    if lock is not None and lock.holder_user_id != user.id:
        raise HTTPException(
            status_code=409,
            detail={
                "error_kind": "project_locked",
                "message": f"'{project.name}' is being edited by another user.",
            },
        )
```

Explicitly NOT gated (design §Open items): `activate`, `import_bundle`, `from_template`,
`create_scenario`, unclaimed/folder imports, eviction write-back, dispatcher completion save.

- [ ] **Step 4: Run to verify pass** — new tests green; then run the file's FULL suite
  (`test_projects_tenancy.py`) plus `test_local_mode_api.py` (local mode must be
  unaffected — every gate no-ops there).
- [ ] **Step 5: Commit**

```bash
git branch --show-current
git commit -m "feat(backend): enforce project lock at save/rename/delete/scenario/layout/members/snapshots/enqueue" \
  -- pypsa-gui/backend/routers/projects.py pypsa-gui/backend/routers/snapshots.py \
     pypsa-gui/backend/routers/solve_queue.py pypsa-gui/backend/tests/test_projects_tenancy.py
```

---

### Task 6: Foreign-lock gate in the write middleware

**Files:**
- Modify: `backend/main.py` (inside the write middleware, directly after the
  solver-in-flight gate that ends at `:624`)
- Test: `backend/tests/test_projects_tenancy.py` (append)

**Interfaces:**
- Consumes: `request.state.auth_user` (set by the auth middleware for `/api/*`,
  `main.py:493-503`); `db_session_module.SessionLocal()` (the middleware's existing db
  idiom, `:493/:556`); the middleware-local active-context binding (block around `:538-560`)
  and `services.project_context.get_binding` for the bound project UUID;
  `services.project_locks.get_lock`.
- Produces: 409 JSON `{"detail": {...}, "code": "project_locked"}` for `/api/network/*` and
  `/api/io/*` writes when the session's ACTIVE project is lock-held by a different user.
  This closes the shared-resident-context bypass (design: contexts are shared per
  `(org, uuid)`, so a non-holder's component edits land in the holder's memory).

- [ ] **Step 1: Write the failing test** (append to `test_projects_tenancy.py`):

```python
def test_network_write_409s_when_active_project_lock_held_by_other(client_a, client_b, shared_project):
    # Both users activate the same project; A holds the lock.
    client_a.post(f"/api/projects/{shared_project['id']}/activate")
    client_a.post(f"/api/projects/{shared_project['id']}/lock")
    client_b.post(f"/api/projects/{shared_project['id']}/activate")
    r = client_b.post("/api/network/buses", json={"name": "Intruder", "v_nom": 380})
    assert r.status_code == 409
    assert r.json().get("code") == "project_locked"
    # The holder's own writes still pass:
    r = client_a.post("/api/network/buses", json={"name": "Legit", "v_nom": 380})
    assert r.status_code in (200, 201)
```

(Adapt the bus-create path/body to the actual component route shape used elsewhere in the
backend tests — grep the test dir for `"/api/network/buses"`.)

- [ ] **Step 2: Run to verify fail** — B's write returns 200.

- [ ] **Step 3: Implement.** In the write middleware, after the solver-in-flight block
  (`:603-624`) and BEFORE the undo-snapshot section (`:626`), reusing the same
  `is_write` + `_SOLVER_BLOCKING_PREFIXES` surface (it is exactly `/api/network/` +
  `/api/io/`— verify against the constant's definition and reuse it or mirror it under a
  clearly-named alias):

```python
    # ── Foreign-lock gate (project-write-safety D3) ───────────────────
    # The resident ProjectContext is shared per (org, project); without this,
    # a non-holder's component edits mutate the holder's in-memory network and
    # the holder's next autosave persists them. Holder (or free/expired lock,
    # or local mode) passes; only a live foreign lock refuses.
    if (is_write
            and any(path.startswith(p) for p in _SOLVER_BLOCKING_PREFIXES)
            and not local_mode.is_local_mode()):
        gate_user = getattr(request.state, "auth_user", None)
        if gate_user is not None:
            binding_uuid = None
            # Reuse the middleware-local context binding established above —
            # read the bound project UUID via project_context.get_binding.
            try:
                from services import project_context, project_locks
                binding = project_context.get_binding(PyPSAService.get_active_context())
                binding_uuid = getattr(binding, "project_uuid", None)
            except Exception:
                binding_uuid = None  # unbound scratch context — nothing to guard
            if binding_uuid is not None:
                with db_session_module.SessionLocal() as gate_db:
                    lock = project_locks.get_lock(gate_db, binding_uuid)
                    if lock is not None and lock.holder_user_id != gate_user.id:
                        return JSONResponse(
                            status_code=409,
                            content={
                                "detail": (
                                    "This project is being edited by another "
                                    "user. Their edit lock must expire or be "
                                    "released before network changes are "
                                    "accepted."
                                ),
                                "code": "project_locked",
                            },
                        )
```

Before writing this, read `main.py:530-560` and `services/project_context.py` for the real
accessor shape (`get_binding`'s return type and field names) and match them exactly — the
snippet's `getattr(binding, "project_uuid", None)` is the intended semantics, not a
guaranteed field name. Watch the two standing middleware traps: no function-local
`from services.pypsa_service import PyPSAService` if the name is read earlier in the
function (UnboundLocalError-into-bare-except), and this code runs in the MIDDLEWARE's
context, which is fine here because the middleware established its own binding.

- [ ] **Step 4: Run to verify pass** — plus the whole tenancy file and
  `test_local_mode_api.py`.
- [ ] **Step 5: Commit**

```bash
git branch --show-current
git commit -m "feat(backend): middleware refuses network/io writes under a foreign project lock" \
  -- pypsa-gui/backend/main.py pypsa-gui/backend/tests/test_projects_tenancy.py
```

---

### Task 7: Frontend lock recovery (re-acquire instead of strand)

**Files:**
- Modify: `frontend/src/utils/projectActions.ts` (heartbeat catch, `:262-276`;
  `saveProjectQuietly`, `:433-470`)
- Test: `frontend/src/utils/projectActions.test.ts` (EXISTING — append)

**Interfaces:**
- Consumes: the acquire call already used by `switchToProject` (`projectActions.ts` ~`:290`
  — reuse its exact `projectsApi` method), `_applyLock`, `stopLockHeartbeat`,
  `_lockFromErrorDetail` (all in the same file).
- Produces: heartbeat-409 now attempts ONE re-acquire before falling read-only; a save
  rejected with `error_kind: "project_locked"` applies the lock banner state instead of
  only a toast.

- [ ] **Step 1: Write the failing tests** (append to `projectActions.test.ts`, following
  its existing mocking pattern for `projectsApi`):

```ts
it('heartbeat 409 re-acquires when the lock merely expired', async () => {
  vi.mocked(projectsApi.heartbeatLock).mockRejectedValueOnce({ response: { status: 409 } })
  vi.mocked(projectsApi.acquireLock).mockResolvedValueOnce({ lock: { holder: 'me', mine: true } } as never)
  // drive one heartbeat tick via the file's existing timer harness
  await runHeartbeatTick('proj-1')
  expect(projectsApi.acquireLock).toHaveBeenCalledWith('proj-1')
  expect(getLockState().readOnly).toBe(false)   // did NOT fall read-only
})

it('heartbeat 409 falls read-only when re-acquire is refused', async () => {
  vi.mocked(projectsApi.heartbeatLock).mockRejectedValueOnce({
    response: { status: 409, data: { detail: { lock: { holder: 'other' } } } },
  })
  vi.mocked(projectsApi.acquireLock).mockRejectedValueOnce({ response: { status: 409 } })
  await runHeartbeatTick('proj-1')
  expect(getLockState().readOnly).toBe(true)
})
```

(`runHeartbeatTick` / `getLockState` = whatever harness the existing heartbeat tests in
this file or `lockState.test.ts` already use — reuse it; if none exists, drive
`startLockHeartbeat` with `vi.useFakeTimers()` + `vi.advanceTimersByTimeAsync(LOCK_HEARTBEAT_MS)`.
Match `acquireLock` to the real `projectsApi` method name used at `:290`.)

- [ ] **Step 2: Run to verify fail** — current code falls read-only without any acquire call.

- [ ] **Step 3: Implement.** In the heartbeat `.catch` (`:264-276`), replace the 409 branch:

```ts
if (status === 409) {
  // The lock may have merely EXPIRED (laptop sleep outlives the 120 s TTL).
  // acquire is idempotent for us and succeeds on a free slot — try once
  // before declaring the workbench read-only.
  projectsApi.acquireLock(projectId)
    .then(res => _applyLock({ ok: true, lock: res.lock }))
    .catch((e2) => {
      _applyLock({ ok: false, lock: _lockFromErrorDetail(e2) })
      stopLockHeartbeat()
      appLog('WARN', `Lost the edit lock on '${projectId}' — the workbench is now read-only.`)
    })
}
```

In `saveProjectQuietly`'s error handling, when the failure detail carries
`error_kind === 'project_locked'`, additionally call
`_applyLock({ ok: false, lock: _lockFromErrorDetail(err) })` and `stopLockHeartbeat()` so
autosave loops stop hammering a foreign-locked project and the banner explains why.

- [ ] **Step 4: Run to verify pass** — the whole `projectActions.test.ts` + `lockState.test.ts`
  + `npx tsc --noEmit`.
- [ ] **Step 5: Commit**

```bash
git branch --show-current
git commit -m "feat(frontend): heartbeat 409 re-acquires once; locked save falls to banner" \
  -- pypsa-gui/frontend/src/utils/projectActions.ts pypsa-gui/frontend/src/utils/projectActions.test.ts
```

---

### Task 8: Full gates + finish

- [ ] **Step 1: Backend full suite** — `pixi run gui-tests` from repo root. All green (7
  webview failures means the env is wrong — rerun via pixi, do not skip).
- [ ] **Step 2: Frontend full suite** — `npx vitest run` + `npx tsc --noEmit -p tsconfig.json`
  (pixi PATH) in `pypsa-gui/frontend`. All green.
- [ ] **Step 3: Security-relevant diff check** — this branch diff touches auth-adjacent
  surface (lock enforcement, middleware). Per standing rules run the adversarial
  review-over-`main..HEAD` variant of /security-review (the command itself needs a remote
  and fails in this repo — use an adversarial agent over the diff instead, per memory).
- [ ] **Step 4: Report** — TDD Evidence per task (RED command + failing output, GREEN
  command + passing output) is REQUIRED in every task report; a missing section is a gap.
  Note in the final report that the packaged macOS app needs `bash pypsa-gui/build-macos.sh`
  before any DMG/app-level claim — source-only until rebuilt.
