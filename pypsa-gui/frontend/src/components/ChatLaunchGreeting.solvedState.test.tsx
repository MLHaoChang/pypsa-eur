import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { getApiKeySettings } from '../api/chat'
import ChatLaunchGreeting from './ChatLaunchGreeting'

// Bug 2 (guided-mode spec §2.2): after an FMEA sweep the sweep's closing base
// re-solve leaves dispatch on the live network, so `/simulation/status` says
// `dispatch: 'fresh'` while `condition`/`solve_time` stay null (no foreground
// solve was recorded). The canvas footer and SnapshotPicker read the latter and
// say "Run a simulation to enable"; the greeting must not claim "Solved" then.

vi.mock('../api/network', () => ({ networkApi: { getMeta: vi.fn() } }))
vi.mock('../api/simulation', () => ({ simulationApi: { getStatus: vi.fn() } }))
vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return { ...actual, getApiKeySettings: vi.fn() }
})

const STUDY_RESOLVE =
  'The network carries dispatch from a study re-solve, but no foreground solve is recorded — run a simulation for results you can read here.'

function renderGreeting() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatLaunchGreeting />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  // Expert: the sentences below are Expert's (a Guided case follows).
  useUIStore.setState({ currentProject: 'dc', uiMode: 'expert' })
  vi.mocked(networkApi.getMeta).mockResolvedValue({ name: 'dc', bus_count: 3, snapshot_count: 24 })
  vi.mocked(getApiKeySettings).mockResolvedValue({
    configured: true, source: 'settings', hint: '…wxyz',
    overridden_by_environment: false, storage_path: '/tmp/user.env',
  })
})

describe('greeting solve line (bug 2)', () => {
  it('says "Solved —" when dispatch is fresh and a foreground solve is recorded', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'completed', condition: 'optimal',
      objective: 1, solve_time: 3, dispatch: 'fresh',
    })
    renderGreeting()
    const line = await screen.findByTestId('chat-launch-solve')
    expect(line.textContent).toMatch(/^Solved —/)
  })

  it('says the study-re-solve sentence when dispatch is fresh but no condition is recorded', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'idle', condition: null,
      objective: null, solve_time: null, dispatch: 'fresh',
    })
    renderGreeting()
    const line = await screen.findByTestId('chat-launch-solve')
    expect(line.textContent).toBe(STUDY_RESOLVE)
    expect(line.textContent).not.toMatch(/^Solved/)
  })

  it('treats a condition without a solve_time as unrecorded too', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'idle', condition: 'optimal',
      objective: null, solve_time: null, dispatch: 'fresh',
    })
    renderGreeting()
    expect((await screen.findByTestId('chat-launch-solve')).textContent).toBe(STUDY_RESOLVE)
  })

  it('leaves the stale sentence unchanged', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'completed', condition: null,
      objective: null, solve_time: null, dispatch: 'stale',
    })
    renderGreeting()
    expect((await screen.findByTestId('chat-launch-solve')).textContent).toBe(
      'Solved earlier, but the results are stale — the network changed since.',
    )
  })
})

// P24-FE re-gate: Guided hides the header Run button, so after a study the
// greeting must not send the user to "run a simulation"; it points to the hub.
describe('greeting solve line in Guided', () => {
  it('after a study re-solve it points to Hub design, not to a hidden Run', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({
      running: false, status: 'idle', condition: null,
      objective: null, solve_time: null, dispatch: 'fresh',
    })
    renderGreeting()
    const line = await screen.findByTestId('chat-launch-solve')
    expect(line.textContent).toBe('A study has run on this network — its results are in Hub design.')
    expect(line.textContent).not.toMatch(/run a simulation/i)
  })
})

