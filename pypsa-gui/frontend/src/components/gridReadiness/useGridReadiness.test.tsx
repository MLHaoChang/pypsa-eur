// DC grid-readiness walkthrough 04: the scorecard reads the loaded network, so it
// is read again when the project's edit counter moves (and only then), and a
// busy network's saved-file answer says it is out of date.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { GridReadiness } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'

const api = vi.hoisted(() => ({
  revision: 1, reads: 0, busy: false,
  hold: null as Promise<unknown> | null, release: () => {},
}))
vi.mock('../../api/network', () => ({
  networkApi: { undoInfo: async () => ({ depth: 0, unsaved: false, network_revision: api.revision }) },
}))
vi.mock('../../api/campusElectrical', async orig => ({
  ...(await orig<typeof import('../../api/campusElectrical')>()),
  campusApi: { gridReadiness: async () => {
    if (api.hold) { const h = api.hold; api.hold = null; await h }
    api.reads += 1
    return api.busy ? { reads: api.reads, network: 'out_of_date' } : { reads: api.reads }
  } },
}))
vi.mock('../../hooks/useChatProfiles', () => ({
  useChatReadiness: () => ({ effectiveProfileId: 'p', ready: true, settled: true }),
}))

import { GridReadinessScorecard, OUT_OF_DATE_REFETCH_MS, useGridReadiness } from './GridReadinessScorecard'

afterEach(() => cleanup())

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('useGridReadiness', () => {
  it('reads again when the network revision moves, not on every poll', async () => {
    useUIStore.setState({ currentProject: 'p1' })
    api.revision = 1
    api.reads = 0
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useGridReadiness(), { wrapper: wrapper(qc) })
    await waitFor(() => expect(result.current.data).toEqual({ reads: 1 }))
    // The same revision sampled again: no second read.
    await qc.refetchQueries({ queryKey: ['undoInfo', 'p1'] })
    await new Promise(r => setTimeout(r, 20))
    expect(api.reads).toBe(1)
    // An edit moves the counter: read again, the last answer shown meanwhile.
    api.revision = 2
    api.hold = new Promise(r => { api.release = r })
    await qc.refetchQueries({ queryKey: ['undoInfo', 'p1'] })
    await waitFor(() => expect(result.current.isFetching).toBe(true))
    expect(result.current.data).toEqual({ reads: 1 })
    expect(result.current.isPlaceholderData).toBe(true)
    api.release()
    await waitFor(() => expect(result.current.data).toEqual({ reads: 2 }))
    expect(result.current.isPlaceholderData).toBe(false)
  })

  it('never shows another project\'s verdicts while a newly opened project loads', async () => {
    useUIStore.setState({ currentProject: 'pA' })
    api.revision = 1
    api.reads = 0
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useGridReadiness(), { wrapper: wrapper(qc) })
    await waitFor(() => expect(result.current.data).toEqual({ reads: 1 }))
    api.hold = new Promise(r => { api.release = r })
    act(() => { useUIStore.setState({ currentProject: 'pB' }) })
    await waitFor(() => expect(result.current.isFetching).toBe(true))
    expect(result.current.data).toBeUndefined()
    api.release()
    await waitFor(() => expect(result.current.data).toEqual({ reads: 2 }))
  })

  it('asks again while the answer is out of date, with no edit, and stops once it is live', async () => {
    useUIStore.setState({ currentProject: 'p2' })
    api.revision = 1
    api.reads = 0
    api.busy = true
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useGridReadiness(), { wrapper: wrapper(qc) })
    await waitFor(() => expect(result.current.data).toEqual({ reads: 1, network: 'out_of_date' }))
    // The run ends; the revision has not moved, yet the next beat reads the live network.
    api.busy = false
    await waitFor(() => expect(result.current.data).toEqual({ reads: 2 }), { timeout: OUT_OF_DATE_REFETCH_MS + 2000 })
    // Live again: no more beats.
    await new Promise(r => setTimeout(r, OUT_OF_DATE_REFETCH_MS + 500))
    expect(api.reads).toBe(2)
  }, 15000)
})

const DATA: GridReadiness = {
  schema: 1, profile: { id: 'eu', title: 'EU RfG and DCC', error: null }, template_id: 'eh_datacenter',
  envelope: true, basis: 'advisory', rows: [],
  summary: { pass: 0, warn: 0, fail: 0, unknown: 0, assumed: 0, red_flags: 0 },
}

describe('the out-of-date line', () => {
  it('shows only when the network was busy', () => {
    const qc = new QueryClient()
    const { rerender } = render(<GridReadinessScorecard data={{ ...DATA, network: 'out_of_date' }} mode="guided" />,
      { wrapper: wrapper(qc) })
    expect(screen.getByRole('status').textContent).toBe(
      'A run is using the project, so this reads the last saved project. It updates when the run finishes.')
    rerender(<GridReadinessScorecard data={{ ...DATA, network: 'live' }} mode="guided" />)
    expect(screen.queryByTestId('gr-out-of-date')).toBeNull()
  })
})
