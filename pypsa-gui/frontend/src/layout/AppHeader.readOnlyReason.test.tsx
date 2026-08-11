// R11 wiring — nothing called `setSolvingReadOnly` before this task, and every
// `evaluateMutation` call site defaulted to the 'locked-by-user' message even
// when a queue job (not another user) was the actual reason a mutation was
// blocked. uiStore.readOnlyReason.test.ts already pins the store's fold in
// isolation (Task 5); this file pins the wiring that actually drives it from
// AppHeader — the one component that knows whether the CURRENT project has a
// queue job running.
//
// Three behaviours, three describe blocks:
//   1. The mount/unmount effect that calls setSolvingReadOnly(jobRunning) and
//      clears it on unmount (brief Step 3, AppHeader.tsx after `busy`).
//   2. The rename guard (commitName) passing readOnlyReason into
//      evaluateMutation, exercised via the realistic race it exists for: the
//      user is mid-rename when a solve starts on the same project.
//   3. Fix round 1 follow-up (spec review): `readOnly` had exactly ONE cause
//      before this task, so every hardcoded "another user is editing this
//      project" string in this file was always true. Introducing the second
//      cause (a queue job solving the project) is what turns seven of those
//      strings false — Ctrl+S's handler and three button tooltips. This
//      block pins the Ctrl+S path (a real behavioural path, not merely a
//      tooltip — the shortcut bypasses the disabled Save button) and the
//      three affected tooltips.
//
// Revert any piece of this wiring and the corresponding test below fails.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import AppHeader from './AppHeader'
import { useUIStore } from '../store/uiStore'
import { WRITABLE } from '../utils/lockState'
import { READ_ONLY_MUTATION_MESSAGE, SOLVING_MUTATION_MESSAGE } from '../utils/mutationGuard'

// Mutable so each test can put a running/queued job on whichever project it
// wants — mirrors the pattern in ScenariosPanel.test.tsx / ProjectTabs.test.tsx.
let queueJobs: Array<{ id: number; project_id: string; status: string; position: number | null }> = []
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: queueJobs, current: null } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: (list: { jobs: typeof queueJobs } | undefined, name: string) =>
    list?.jobs.find(j => j.project_id === name && (j.status === 'queued' || j.status === 'running')),
}))

// jsdom has no EventSource. The auto-attach effect opens one the instant a
// job is 'running' for the current project — exactly the state these tests
// need — so the log stream itself must be stubbed out; it is not what is
// under test here.
vi.mock('../api/simulation', async (orig) => ({
  ...(await orig<typeof import('../api/simulation')>()),
  createLogStream: vi.fn(() => () => {}),
}))

// authEnabled defaults to true in this env (src/auth/config.ts), which mounts
// <UserMenu/> — that needs a real AuthProvider this test has no reason to set
// up. Not what's under test here; same mocking pattern as RequireAdmin.test.tsx.
vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AppHeader /></MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  // `vi.spyOn` on an already-spied method does not clear prior call history —
  // without this, a toast.error call from an EARLIER test survives into a
  // later test's `.not.toHaveBeenCalledWith(...)` check and fails it for the
  // wrong reason.
  vi.clearAllMocks()
  queueJobs = []
  useUIStore.setState({ currentProject: null, projectName: 'Unnamed Network' })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
})

describe('AppHeader wires the current project\'s queue job into uiStore.solvingReadOnly', () => {
  it('goes read-only with the solving reason while the CURRENT project\'s job runs', () => {
    queueJobs = [{ id: 1, project_id: 'demo', status: 'running', position: null }]
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })

    renderHeader()

    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('solving')
  })

  it('stays writable when the running job belongs to a different project', () => {
    queueJobs = [{ id: 1, project_id: 'other-project', status: 'running', position: null }]
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })

    renderHeader()

    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).toBe('writable')
  })

  it('clears solvingReadOnly on unmount so the workbench cannot be stranded read-only', () => {
    queueJobs = [{ id: 1, project_id: 'demo', status: 'running', position: null }]
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })

    const { unmount } = renderHeader()
    expect(useUIStore.getState().readOnlyReason).toBe('solving')

    unmount()

    expect(useUIStore.getState().readOnly).toBe(false)
    expect(useUIStore.getState().readOnlyReason).toBe('writable')
  })

  it('falls back to the edit-lock reason once the solve ends, if another user still holds it', () => {
    queueJobs = [{ id: 1, project_id: 'demo', status: 'running', position: null }]
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })
    useUIStore.getState().setLockState({
      readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user',
    })

    const { rerender } = renderHeader()
    expect(useUIStore.getState().readOnlyReason).toBe('solving')

    // The job finishes — drop it from the queue and re-render so the effect
    // re-runs with jobRunning=false.
    queueJobs = []
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><AppHeader /></MemoryRouter>
      </QueryClientProvider>,
    )

    expect(useUIStore.getState().readOnly).toBe(true)
    expect(useUIStore.getState().readOnlyReason).toBe('locked-by-user')
  })
})

describe('AppHeader\'s rename guard names the SOLVING reason, not the edit-lock one', () => {
  it('shows the solving message when a solve starts mid-rename, not the edit-lock message', async () => {
    // Realistic race the guard exists for: the user opens the inline rename
    // editor while writable, then a queue job starts solving the SAME
    // project before they commit the new name.
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })

    const { container } = renderHeader()
    await userEvent.click(screen.getByTitle('Click to rename project'))

    const input = container.querySelector('input[maxlength="80"]') as HTMLInputElement
    expect(input).toBeTruthy()
    await userEvent.clear(input)
    await userEvent.type(input, 'renamed-mid-solve')

    // The solve starts while the editor is still open.
    useUIStore.getState().setSolvingReadOnly(true)

    await userEvent.keyboard('{Enter}')

    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(SOLVING_MUTATION_MESSAGE)
    expect(vi.mocked(toast.error)).not.toHaveBeenCalledWith(READ_ONLY_MUTATION_MESSAGE)
  })
})

describe("AppHeader's other read-only surfaces now name the solving reason too", () => {
  it('Ctrl+S shows the solving message, not the edit-lock one, while the project solves', () => {
    // handleQuickSave is reachable even though the Save button is disabled —
    // its own comment says the keyboard shortcut bypasses the disabled
    // button, and the effect below wires Ctrl/Cmd+S to it unconditionally.
    queueJobs = [{ id: 1, project_id: 'demo', status: 'running', position: null }]
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })
    renderHeader()
    expect(useUIStore.getState().readOnlyReason).toBe('solving')

    fireEvent.keyDown(window, { key: 's', ctrlKey: true })

    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(SOLVING_MUTATION_MESSAGE)
    expect(vi.mocked(toast.error)).not.toHaveBeenCalledWith(READ_ONLY_MUTATION_MESSAGE)
  })

  it('the rename, Undo and Save button tooltips name the solving reason too', () => {
    queueJobs = [{ id: 1, project_id: 'demo', status: 'running', position: null }]
    useUIStore.setState({ currentProject: 'demo', projectName: 'demo' })
    renderHeader()
    expect(useUIStore.getState().readOnlyReason).toBe('solving')

    // Rename (project name button), Undo, Save — all three read `readOnly`
    // with no further gate, unlike the Run button (gated on `!amber`, which
    // is true while jobRunning — see the brief's confirmed-correct site).
    expect(screen.getAllByTitle(SOLVING_MUTATION_MESSAGE)).toHaveLength(3)
    expect(screen.queryAllByTitle(READ_ONLY_MUTATION_MESSAGE)).toHaveLength(0)
  })
})
