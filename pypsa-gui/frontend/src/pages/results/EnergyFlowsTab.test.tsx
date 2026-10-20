import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { energyFlowsApi, type EnergyFlowsPayload } from '../../api/energyFlows'
import EnergyFlowsTab, { FlowSankeyChart } from './EnergyFlowsTab'
import { sankeyData } from './energyFlows'
import { ROWS } from './energyFlows.test'
import { expectAllButtonsNamed } from '../../test-utils/accessibleName'
import * as shared from './shared'

vi.mock('../../api/energyFlows', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/energyFlows')>()
  return { ...actual, energyFlowsApi: { get: vi.fn(), csv: vi.fn() } }
})

const OK: EnergyFlowsPayload = {
  status: 'ok', unit: 'MWh', period: null, periods: [null],
  window: { from: 0, to: 167, snapshots: 168, start: '2030-01-01 00:00:00', end: '2030-01-07 23:00:00',
            first: '2030-01-01 00:00:00', last: '2030-01-07 23:00:00' },
  rows: ROWS,
  series: { index: ['2030-01-01 00:00:00', '2030-01-01 01:00:00'], bucket_snapshots: 1,
            supply: { solar: [1, 2], 'Grid import': [3, 4] }, demand: { load: [4, 6] } },
  conservation: { status: 'ok', checks: [], generation_mwh: 1030, destinations_mwh: 1030,
                  storage_delta_mwh: 0, stock_mwh: 0, residual_mwh: 0, tolerance_mwh: 1 },
  unserved_mwh: null, flags: [],
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><EnergyFlowsTab sankeyWidth={700} /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(energyFlowsApi.get).mockReset().mockResolvedValue(OK)
})
afterEach(() => cleanup())

describe('EnergyFlowsTab', () => {
  it('draws the Sankey with its nodes and the conservation chip', async () => {
    const { container } = mount()
    const svg = (await screen.findByTestId('ef-sankey')).querySelector('svg')!
    const labels = [...svg.querySelectorAll('text')].map(t => t.textContent)
    for (const l of ['solar', 'Grid import', 'Battery', 'Direct', 'load', 'Losses']) expect(labels).toContain(l)
    expect(screen.getByTestId('ef-conservation').getAttribute('data-status')).toBe('ok')
    expect(screen.getByTestId('ef-series-supply')).toBeTruthy()
    expectAllButtonsNamed(container)
  })

  it('exports exactly the rows it draws', async () => {
    const spy = vi.spyOn(shared, 'downloadCSV').mockImplementation(() => {})
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Export CSV' }))
    const [, header, rows] = spy.mock.calls[0]
    expect(header).toEqual(['Source', 'Via', 'Destination', 'MWh'])
    expect(rows).toHaveLength(ROWS.length)
    spy.mockRestore()
  })

  it('shows an unproven run as unavailable, with no flows and no zeros', async () => {
    vi.mocked(energyFlowsApi.get).mockResolvedValue({
      ...OK, status: 'not_established', rows: [], series: null, conservation: null,
      reason: 'no solved generator dispatch for every component', flags: ['bus balance does not close at hub'],
    })
    mount()
    expect(await screen.findByTestId('ef-unavailable')).toBeTruthy()
    expect(screen.getByText(/bus balance does not close at hub/)).toBeTruthy()
    expect(screen.queryByTestId('ef-sankey')).toBeNull()
  })

  it('says "run a solve" before there is one', async () => {
    vi.mocked(energyFlowsApi.get).mockResolvedValue(null)
    mount()
    expect(await screen.findByText(/Run a solve/)).toBeTruthy()
  })

  it('asks for the narrowed window after the slider settles', async () => {
    mount()
    const end = await screen.findByLabelText('Window end')
    fireEvent.change(end, { target: { value: '23' } })
    expect(screen.getByTestId('ef-window-label').textContent).toContain('24 of 168')
    await waitFor(() => expect(energyFlowsApi.get).toHaveBeenLastCalledWith(
      expect.objectContaining({ from: 0, to: 23 })), { timeout: 2000 })
  })

  it('states an unserved amount only when the run recorded one', async () => {
    mount()
    await screen.findByTestId('ef-sankey')
    expect(screen.queryByTestId('ef-unserved')).toBeNull()
    cleanup()
    vi.mocked(energyFlowsApi.get).mockResolvedValue({ ...OK, unserved_mwh: 12 })
    mount()
    expect((await screen.findByTestId('ef-unserved')).textContent).toContain('12')
  })
})

describe('cross-link', () => {
  it('points at the money view and asks for that tab', async () => {
    mount()
    fireEvent.click(await screen.findByTestId('ef-to-value-flows'))
    expect(useUIStore.getState().resultsTabRequest).toBe('investment')
  })
})

describe('FlowSankeyChart', () => {
  it('draws a path per link', () => {
    const { container } = render(<FlowSankeyChart data={sankeyData(ROWS)} width={700} />)
    expect(container.querySelectorAll('svg path').length).toBeGreaterThanOrEqual(6)
  })
})
