// The header Save is the one Save (SAVE-1, UX-flow #9). It carries what the
// sidebar's Save used to say when a save is refused: a tab/backend mismatch is
// named and nothing is posted, and a 409 shows the backend's own sentence, not
// "[object Object]" or a false "network is empty".
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import AppHeader from './AppHeader'
import { projectsApi } from '../api/projects'
import { useUIStore } from '../store/uiStore'
import { useSimulationStore } from '../store/simulationStore'
import { WRITABLE } from '../utils/lockState'

vi.mock('../api/projects', async (orig) => ({
  ...(await orig<typeof import('../api/projects')>()),
  projectsApi: { ...(await orig<typeof import('../api/projects')>()).projectsApi, save: vi.fn() },
}))
vi.mock('react-hot-toast', () => {
  const t = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), dismiss: vi.fn(), loading: vi.fn() })
  return { default: t }
})
vi.mock('../hooks/useSolveQueue', () => ({
  useSolveQueue: () => ({ data: { jobs: [], running: [], paused: false } }),
  useEnqueueSolve: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAbortJob: () => ({ mutateAsync: vi.fn(), isPending: false }),
  activeJobForProject: () => undefined,
}))
vi.mock('../auth/AuthModeProvider', () => ({
  useAuthMode: () => ({ ready: true, authEnabled: false, enableAuth: () => {} }),
}))

const SENTENCE = 'This tab shows demo, but the app is now on other. Changes from this tab are paused.'

function renderHeader() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AppHeader /></MemoryRouter>
    </QueryClientProvider>,
  )
}
const reject409 = (detail: unknown) => Object.assign(new Error('Request failed with status code 409'), {
  response: { status: 409, data: { detail } },
})

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'demo', projectName: 'demo', uiMode: 'expert', projectMismatch: null })
  useUIStore.getState().setLockState(WRITABLE)
  useSimulationStore.setState({ status: 'idle' })
})

describe('header Save refusals', () => {
  it('while mismatched: says why and posts nothing', async () => {
    useUIStore.setState({ projectMismatch: { tab: 'demo', backend: 'other' } })
    renderHeader()
    await userEvent.click(screen.getByRole('button', { name: 'Save project' }))
    expect(projectsApi.save).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(SENTENCE)
  })

  it('a study_in_flight 409 shows the backend sentence, not "[object Object]"', async () => {
    const message = 'Cannot save the project while a FMEA sweep is running. Wait for it to finish, or abort it, and retry.'
    vi.mocked(projectsApi.save).mockRejectedValue(reject409({ error_kind: 'study_in_flight', study: 'fmea_sweep', message }))
    renderHeader()
    await userEvent.click(screen.getByRole('button', { name: 'Save project' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    const shown = vi.mocked(toast.error).mock.calls.map(c => String(c[0])).join(' ')
    expect(shown).toContain(message)
    expect(shown).not.toContain('[object Object]')
    expect(shown).not.toContain('network is empty')
  })

  it('an identity 409 raises the mismatch from the detail\'s names', async () => {
    vi.mocked(projectsApi.save).mockRejectedValue(reject409(
      "Backend network is bound to project 'other', not 'demo'. Refusing to overwrite 'demo' with the wrong network."))
    renderHeader()
    await userEvent.click(screen.getByRole('button', { name: 'Save project' }))
    await waitFor(() => expect(useUIStore.getState().projectMismatch).toEqual({ tab: 'demo', backend: 'other' }))
  })

  it('a bare 409 still means the network is empty', async () => {
    vi.mocked(projectsApi.save).mockRejectedValue(reject409('network is empty'))
    renderHeader()
    await userEvent.click(screen.getByRole('button', { name: 'Save project' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Cannot save: network is empty. Load the project first.'))
  })
})
