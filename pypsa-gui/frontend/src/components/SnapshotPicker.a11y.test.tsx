// Phase 2 plan Task 6.4: the timeline is the 3D view's only time control, so
// its slider has an accessible name and reads out the timestamp.
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SnapshotPicker from './SnapshotPicker'
import { networkApi } from '../api/network'
import { resultsApi, simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network')
vi.mock('../api/simulation')

beforeEach(() => {
  useUIStore.setState({ currentProject: 'p', resultsOverlayEnabled: true, resultsSnapshotIdx: 2, activeSlidePanel: null, paletteMode: null })
  vi.mocked(networkApi.getSnapshots).mockResolvedValue({ count: 4, snapshots: ['2030-01-01T00:00:00', '2030-01-01T01:00:00', '2030-01-01T02:00:00', '2030-01-01T03:00:00'] } as never)
  vi.mocked(simulationApi.getStatus).mockResolvedValue({ status: 'completed', condition: 'optimal', solve_time: 1, dispatch: 'fresh' } as never)
  vi.mocked(resultsApi.getAcPfStatus).mockResolvedValue({ available: false } as never)
})

describe('SnapshotPicker slider', () => {
  it('has an accessible name and the timestamp as its value text', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><SnapshotPicker /></QueryClientProvider>)
    const slider = await waitFor(() => screen.getByRole('slider'))
    expect(slider.getAttribute('aria-label')).toBe('Snapshot')
    expect(slider.getAttribute('aria-valuetext')).toBe('2030-01-01 02:00 (3 of 4)')
  })
})
