import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { resultsApi, simulationApi } from '../api/simulation'
import { getApiKeySettings } from '../api/chat'
import ChatLaunchGreeting from './ChatLaunchGreeting'

// Bug 2 (guided-mode spec §2.2): after an FMEA sweep the sweep's closing base
// re-solve leaves dispatch on the live network, so `/simulation/status` says
// `dispatch: 'fresh'` while `condition`/`solve_time` stay null (no foreground
// solve was recorded). The canvas footer and SnapshotPicker read the latter and
// say "Run a simulation to enable"; the greeting must not claim "Solved" then.

vi.mock('../api/network', () => ({ networkApi: { getMeta: vi.fn() } }))
vi.mock('../api/simulation', () => ({
  simulationApi: { getStatus: vi.fn() },
  resultsApi: { getEhStudy: vi.fn() },
}))
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


// P26 (coordinator item 7): after the hub study (and the FMEA sweep) a Guided
// greeting still said "Not solved yet.". Two reasons: the EH study solves a
// copy, so the live network's dispatch stays `none`; and a sweep started from
// the Improve card was polled by no mounted panel, so the cached status was
// never re-read. The greeting now also reads the hub study record (Guided only).
describe('greeting solve line in Guided after the hub study (P26)', () => {
  const NONE = { running: false, status: 'idle', condition: null,
    objective: null, solve_time: null, dispatch: 'none' } as const
  it('a finished hub study → points to Hub design, never "Not solved yet."', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ ...NONE })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'done' } as never)
    renderGreeting()
    await vi.waitFor(() => expect(screen.getByTestId('chat-launch-solve').textContent)
      .toBe('A study has run on this network — its results are in Hub design.'))
  })

  it('a running hub study → says so and where to follow it', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ ...NONE })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'running' } as never)
    renderGreeting()
    await vi.waitFor(() => expect(screen.getByTestId('chat-launch-solve').textContent)
      .toBe('The hub study is running — follow it in Hub design.'))
  })

  it('no hub study yet → still "Not solved yet."', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ ...NONE })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(null)
    renderGreeting()
    await vi.waitFor(() => expect(resultsApi.getEhStudy).toHaveBeenCalled())
    expect((await screen.findByTestId('chat-launch-solve')).textContent).toBe('Not solved yet.')
  })

  it('Expert is unchanged: "Not solved yet." and the study record is not read', async () => {
    useUIStore.setState({ uiMode: 'expert' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ ...NONE })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: 'done' } as never)
    renderGreeting()
    expect((await screen.findByTestId('chat-launch-solve')).textContent).toBe('Not solved yet.')
    expect(resultsApi.getEhStudy).not.toHaveBeenCalled()
  })
})

// P26 gate B1: the greeting's study query had no poll, so with the hub panel
// closed a finished study still read "running" for as long as the chat stayed
// empty. It now polls with the hub's own interval (same key, same options).
// Also: a failed / aborted study says so instead of "Not solved yet.".
describe('greeting follows the hub study with the hub closed (P26 gate B1)', () => {
  const NONE = { running: false, status: 'idle', condition: null,
    objective: null, solve_time: null, dispatch: 'none' } as const
  it('running → done updates the line with no hub panel mounted', async () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: null })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ ...NONE })
    vi.mocked(resultsApi.getEhStudy)
      .mockResolvedValueOnce({ status: 'running' } as never)
      .mockResolvedValue({ status: 'done' } as never)
    renderGreeting()
    await vi.waitFor(() => expect(screen.getByTestId('chat-launch-solve').textContent)
      .toBe('The hub study is running — follow it in Hub design.'))
    await vi.waitFor(() => expect(screen.getByTestId('chat-launch-solve').textContent)
      .toBe('A study has run on this network — its results are in Hub design.'), { timeout: 5000 })
  })

  it.each(['failed', 'aborted'])('a %s hub study → "did not finish", not "Not solved yet."', async (st) => {
    useUIStore.setState({ uiMode: 'guided' })
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ ...NONE })
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({ status: st } as never)
    renderGreeting()
    await vi.waitFor(() => expect(screen.getByTestId('chat-launch-solve').textContent)
      .toBe('The last hub study did not finish — see Hub design.'))
  })
})
