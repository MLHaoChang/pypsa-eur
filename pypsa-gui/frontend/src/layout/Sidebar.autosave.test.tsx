// A2 (deferred spec 2026-09-28 §2.1, "Autosave" and "Sidebar 409 branch").
//
// While the tab and the backend disagree about the open project, autosave is
// suspended through `guardProjectMutation` (one WARN per tick, nothing
// posted) and the manual Save button says why in its title. A save refused
// with the identity 409 ("Backend network is bound to project 'Y', not 'X'")
// raises the mismatch at once from the detail's names. And the 409 branch no
// longer runs `String(detail)` on a dict: a manual save refused with the
// `study_in_flight` dict toasts the backend's sentence, not "[object Object]"
// or the empty-network sentence.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import Sidebar from './Sidebar'
import { useUIStore } from '../store/uiStore'
import { useSimulationStore } from '../store/simulationStore'
import { WRITABLE } from '../utils/lockState'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'

vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
}))
vi.mock('../hooks/useLocalSettings', () => ({ useLocalSettingsAvailable: () => false }))
vi.mock('../api/projects')
vi.mock('../utils/projectActions', async (orig) => ({
  ...(await orig<typeof import('../utils/projectActions')>()),
  abortRunningSim: vi.fn().mockResolvedValue(true),
}))

const SENTENCE = 'This tab shows demo, but the app is now on other. Changes from this tab are paused.'
const FIVE_MIN = 5 * 60 * 1000

function renderSidebar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><Sidebar /></MemoryRouter>
    </QueryClientProvider>,
  )
}

function reject409(detail: unknown) {
  return Object.assign(new Error('Request failed with status code 409'), {
    response: { status: 409, data: { detail } },
  })
}

const logLines = () => useSimulationStore.getState().logLines

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({
    currentProject: 'demo', projectName: 'demo', sidebarMode: 'expanded', recents: [],
    autosaveEnabled: true, projectMismatch: null, projectSwitchInProgress: false, uiMode: 'expert',
  })
  useUIStore.getState().setLockState(WRITABLE)
  useUIStore.getState().setSolvingReadOnly(false)
  useSimulationStore.setState({ status: 'idle', logLines: [] })
  vi.mocked(projectsApi.list).mockResolvedValue([])
  vi.mocked(projectsApi.save).mockResolvedValue({ saved: 'demo', bus_count: 3, ts_columns_saved: 0 } as never)
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
  vi.spyOn(networkApi, 'undoInfo').mockResolvedValue({ depth: 0, unsaved: false } as never)
  vi.spyOn(networkApi, 'getMeta').mockResolvedValue(
    { name: 'demo', bus_count: 3, snapshot_count: 1, loaded_project: 'demo' } as never)
  vi.spyOn(simulationApi, 'preflight').mockResolvedValue({ errors: [], warnings: [] } as never)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  useUIStore.setState({ projectMismatch: null, autosaveEnabled: false })
})

async function tickAutosave() {
  await act(async () => { await vi.advanceTimersByTimeAsync(FIVE_MIN + 10) })
}

describe('autosave while the tab and the backend disagree', () => {
  it('posts nothing and logs one WARN per tick', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderSidebar()
    await waitFor(() => expect(networkApi.getMeta).toHaveBeenCalled())
    act(() => { useUIStore.getState().setProjectMismatch({ tab: 'demo', backend: 'other' }) })
    await tickAutosave()
    expect(projectsApi.save).not.toHaveBeenCalled()
    const warns = logLines().filter(l => / WARN Autosave skipped/.test(l))
    expect(warns).toHaveLength(1)
    expect(warns[0]).toContain(SENTENCE)
  })

  it('the control: without a mismatch the tick saves', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderSidebar()
    await waitFor(() => expect(networkApi.getMeta).toHaveBeenCalled())
    await tickAutosave()
    await waitFor(() => expect(projectsApi.save).toHaveBeenCalledTimes(1))
  })

  it('an identity 409 ("bound to project") raises the mismatch from the detail\'s names', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(projectsApi.save).mockRejectedValue(reject409(
      "Backend network is bound to project 'other', not 'demo'. It was loaded/swapped out from "
      + "under this client (another tab, an external client, or a load that bypassed this save's "
      + "caller). Refusing to overwrite 'demo' with the wrong network. Reload 'demo' to resync, then retry."))
    renderSidebar()
    await waitFor(() => expect(networkApi.getMeta).toHaveBeenCalled())
    await tickAutosave()
    await waitFor(() => expect(useUIStore.getState().projectMismatch)
      .toEqual({ tab: 'demo', backend: 'other' }))
    expect(logLines().filter(l => / WARN Autosave skipped/.test(l))).toHaveLength(1)
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('manual Save', () => {
  it('while mismatched: the Save button says why, and nothing is posted', async () => {
    useUIStore.setState({ projectMismatch: { tab: 'demo', backend: 'other' } })
    renderSidebar()
    const save = screen.getByText('Save').closest('button')!
    expect(save.getAttribute('title')).toBe(SENTENCE)
    await userEvent.click(save)
    expect(projectsApi.save).not.toHaveBeenCalled()
    expect(vi.mocked(toast.error).mock.calls.map(c => String(c[0]))).toEqual([SENTENCE])
  })

  it('refused with the study_in_flight dict → the backend sentence, not "[object Object]"', async () => {
    const message = 'Cannot save the project while a FMEA sweep is running — it re-solves the '
      + 'in-memory network between its own iterates. Wait for it to finish, or abort it, and retry.'
    vi.mocked(projectsApi.save).mockRejectedValue(
      reject409({ error_kind: 'study_in_flight', study: 'fmea_sweep', message }))
    renderSidebar()
    await userEvent.click(screen.getByText('Save').closest('button')!)
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    const shown = vi.mocked(toast.error).mock.calls.map(c => String(c[0]))
    expect(shown).toEqual([message])
    expect(shown.join(' ')).not.toContain('[object Object]')
    expect(shown.join(' ')).not.toContain('network is empty')
  })
})
