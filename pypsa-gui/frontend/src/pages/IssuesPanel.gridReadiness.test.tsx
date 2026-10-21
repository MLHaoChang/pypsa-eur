// DC grid readiness 02, PR B: the Expert mount. RUN › Issues gains a "Grid
// readiness" group when the scorecard reads; its "Go to field" opens the
// component like the Issues rows do.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import { campusApi, type GridReadiness } from '../api/campusElectrical'
import { useUIStore } from '../store/uiStore'
import IssuesPanel from './IssuesPanel'

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, preflight: vi.fn() } }
})
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn() } }
})
vi.mock('../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, gridReadiness: vi.fn() } }
})

const SCORECARD: GridReadiness = {
  schema: 1, profile: { id: 'eu_rfg_dcc_ce', title: 'EU RfG and DCC', error: null },
  template_id: 'eh_datacenter', envelope: true, basis: 'advisory',
  summary: { pass: 0, warn: 0, fail: 1, unknown: 0, assumed: 1, red_flags: 1 },
  rows: [{ id: 'ramp_rate', requirement: 'Load ramp rate at the PoC', limit: 4, unit: 'MW/min',
    source: 'assumed', project_value: 1404, verdict: 'fail', assumed: true, note: null,
    section: 'connection',
    target: { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'connection_ramp' } }],
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><IssuesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: null })
  vi.mocked(simulationApi.preflight).mockResolvedValue({ ok: true, errors: 0, warnings: 0, issues: [] })
  vi.mocked(networkApi.getCarriers).mockResolvedValue([])
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('IssuesPanel grid readiness', () => {
  it('shows the scorecard as its own group and jumps to the row target', async () => {
    vi.mocked(campusApi.gridReadiness).mockResolvedValue(SCORECARD)
    const user = userEvent.setup()
    renderPanel()
    await screen.findByTestId('grid-readiness-group')
    expect(screen.getByText('Grid readiness')).toBeTruthy()
    expect(screen.getByText('1 red flag · 0 unknown · 1 assumed')).toBeTruthy()
    await user.click(screen.getByTestId('gr-jump-ramp_rate'))
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Link', name: 'grid_import' })
  })

  it('leaves the panel as it was when the scorecard cannot be read', async () => {
    vi.mocked(campusApi.gridReadiness).mockRejectedValue(new Error('no network'))
    renderPanel()
    await waitFor(() => expect(campusApi.gridReadiness).toHaveBeenCalled())
    expect(screen.queryByTestId('grid-readiness-group')).toBeNull()
  })
})
