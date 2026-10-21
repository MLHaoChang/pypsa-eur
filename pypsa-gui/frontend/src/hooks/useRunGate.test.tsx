import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import * as flowApi from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { useRunGate } from './useRunGate'

vi.mock('../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/flow')>()
  return { ...actual, getProjectFlow: vi.fn() }
})

const target = { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'p_nom' } as const
const blocker = { section: 'connection', message: 'Connection & grid: no import connection', target } as const
const base = {
  sections: [], run_request: { optimisation: true, study: true }, notes: [],
  deferred: false, deferred_reason: null,
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

beforeEach(() => {
  vi.mocked(flowApi.getProjectFlow).mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
})

describe('useRunGate', () => {
  it('blocks with the server reason by section when the optimisation would be refused', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue({
      ...base, run_ready: false, run_blocker: blocker,
      verdicts: { optimisation: { ready: false, blocker }, study: null },
    } as never)
    const { result } = renderHook(() => useRunGate(), { wrapper })
    await waitFor(() => expect(result.current.blocked).toBe(true))
    expect(result.current.reason).toBe('Connection & grid: no import connection')
    expect(result.current.blocker?.target.entity).toBe('grid_import')
  })

  it('the header Run is not blocked by a study-only refusal, the readiness gate is', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue({
      ...base, run_ready: false, run_blocker: blocker,
      verdicts: { optimisation: { ready: true, blocker: null }, study: { ready: false, blocker } },
    } as never)
    const header = renderHook(() => useRunGate('optimisation'), { wrapper })
    const all = renderHook(() => useRunGate('all'), { wrapper })
    await waitFor(() => expect(all.result.current.blocked).toBe(true))
    expect(header.result.current.blocked).toBe(false)
  })

  it('keeps notes beside an enabled Run', async () => {
    const note = { section: 'project', message: 'Project & site: no location', target }
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue({
      ...base, run_ready: true, run_blocker: null, notes: [note],
      verdicts: { optimisation: { ready: true, blocker: null }, study: null },
    } as never)
    const { result } = renderHook(() => useRunGate(), { wrapper })
    await waitFor(() => expect(result.current.notes).toHaveLength(1))
    expect(result.current.blocked).toBe(false)
  })

  it('never blocks on a flow it could not read, or while deferred (the server still refuses)', async () => {
    vi.mocked(flowApi.getProjectFlow).mockRejectedValue(new Error('x'))
    const { result } = renderHook(() => useRunGate(), { wrapper })
    await new Promise(r => setTimeout(r, 20))
    expect(result.current.blocked).toBe(false)
  })
})

describe('useRunGate — deferred', () => {
  it('says it is checking while a solve holds the network, and does not block', async () => {
    vi.mocked(flowApi.getProjectFlow).mockResolvedValue({
      ...base, run_ready: false, run_blocker: null, deferred: true, deferred_reason: 'solving',
      verdicts: { optimisation: { ready: false, blocker: null }, study: null },
    } as never)
    const { result } = renderHook(() => useRunGate(), { wrapper })
    await waitFor(() => expect(result.current.checking).toBe(true))
    expect(result.current.blocked).toBe(false)
  })
})
