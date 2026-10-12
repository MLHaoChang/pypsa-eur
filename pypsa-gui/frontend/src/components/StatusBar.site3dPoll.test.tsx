// Phase 2 plan Task 5.1 (and the WP4 gate): the 3D view polls the status on
// the status bar's query key. Its data must never make the status bar toast
// "Results cleared" or clear the store — neither while the store is idle,
// nor later by leaving a stale 'fresh' baseline behind.
import { act, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import StatusBar from './StatusBar'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { useDispatchFresh, STATUS_POLL_MS } from '../site3d/useDispatchFresh'

vi.mock('../api/network')
vi.mock('../api/simulation')
vi.mock('react-hot-toast', () => ({ default: vi.fn() }))

function Poll() { useDispatchFresh('Alpha'); return null }

let qc: QueryClient
const clearResults = vi.fn()
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 50) })

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.mocked(toast).mockClear(); clearResults.mockClear()
  useUIStore.setState({ currentProject: 'Alpha', projectName: 'Alpha' })
  useSimulationStore.setState({ status: 'idle', clearResults })
  vi.mocked(networkApi.getMeta).mockResolvedValue({ bus_count: 1 } as never)
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: 24 } as never)
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 0, unsaved: false } as never)
})
afterEach(() => { vi.useRealTimers() })

const ui = (poll: boolean) => (
  <QueryClientProvider client={qc}><StatusBar />{poll && <Poll />}</QueryClientProvider>
)

describe('StatusBar with the 3D view\'s status poll', () => {
  it('store idle: a fresh → none change brought in by the poll neither toasts nor clears', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'completed', dispatch: 'fresh' } as never)
    render(ui(true))
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'completed', dispatch: 'none' } as never)
    await tick(); await tick()
    expect(toast).not.toHaveBeenCalled()
    expect(clearResults).not.toHaveBeenCalled()
  })
  it('a baseline the poll cached earlier does not make a later failed solve toast', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'completed', dispatch: 'fresh' } as never)
    const view = render(ui(true))
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await tick()
    view.rerender(ui(false))                              // the 3D view closes; the user edits (dispatch → none)
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'failed', dispatch: 'none' } as never)
    act(() => { useSimulationStore.setState({ status: 'failed' }) })   // a solve fails: the status bar starts watching
    await tick(); await tick()
    expect(toast).not.toHaveBeenCalled()
    expect(clearResults).not.toHaveBeenCalled()
  })
  it('(pin) after an in-session solve the status bar still toasts once when an edit clears the results', async () => {
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'completed', dispatch: 'fresh' } as never)
    useSimulationStore.setState({ status: 'completed' })
    render(ui(false))
    await waitFor(() => expect(simulationApi.getStatus).toHaveBeenCalled())
    await tick()
    vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'completed', dispatch: 'none' } as never)
    await tick(); await tick()
    expect(toast).toHaveBeenCalledTimes(1)
    expect(clearResults).toHaveBeenCalledTimes(1)
  })
})
