// DC grid-readiness walkthrough 04: the scorecard reads the loaded network, so it
// is read again when the project's edit counter moves (and only then), and a
// busy network's saved-file answer says it is out of date.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { GridReadiness } from '../../api/campusElectrical'
import { useUIStore } from '../../store/uiStore'

const api = vi.hoisted(() => ({ revision: 1, reads: 0 }))
vi.mock('../../api/network', () => ({
  networkApi: { undoInfo: async () => ({ depth: 0, unsaved: false, network_revision: api.revision }) },
}))
vi.mock('../../api/campusElectrical', async orig => ({
  ...(await orig<typeof import('../../api/campusElectrical')>()),
  campusApi: { gridReadiness: async () => { api.reads += 1; return { reads: api.reads } } },
}))
vi.mock('../../hooks/useChatProfiles', () => ({
  useChatReadiness: () => ({ effectiveProfileId: 'p', ready: true, settled: true }),
}))

import { GridReadinessScorecard, useGridReadiness } from './GridReadinessScorecard'

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
    await qc.refetchQueries({ queryKey: ['undoInfo', 'p1'] })
    await waitFor(() => expect(result.current.data).toEqual({ reads: 2 }))
  })
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
      'A run is using the project, so this reads the last saved project. Save the project to update.')
    rerender(<GridReadinessScorecard data={{ ...DATA, network: 'live' }} mode="guided" />)
    expect(screen.queryByTestId('gr-out-of-date')).toBeNull()
  })
})
