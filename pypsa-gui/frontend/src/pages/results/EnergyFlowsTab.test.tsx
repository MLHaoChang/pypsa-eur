import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import type { ChartSpec } from '../../api/reports'
import { resetReportCharts } from './reportCharts'
import { useUIStore } from '../../store/uiStore'
import { energyFlowsApi, type EnergyFlowsPayload } from '../../api/energyFlows'
import EnergyFlowsTab, { FlowSankeyChart, seriesName } from './EnergyFlowsTab'
import { fmtMwh, sankeyData } from './energyFlows'
import { ROWS } from './energyFlows.test'
import { expectAllButtonsNamed } from '../../test-utils/accessibleName'
import * as shared from './shared'

vi.mock('../../api/energyFlows', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/energyFlows')>()
  return { ...actual, energyFlowsApi: { get: vi.fn(), csv: vi.fn() } }
})

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
vi.mock('../../utils/chartPng', () => ({
  svgToPngBlob: vi.fn().mockResolvedValue(new Blob(['png'], { type: 'image/png' })),
}))
const reportApi = vi.hoisted(() => ({ listReportCharts: vi.fn(), addReportChart: vi.fn(), removeReportChart: vi.fn() }))
vi.mock('../../api/reports', async () => ({
  ...(await vi.importActual<typeof import('../../api/reports')>('../../api/reports')),
  ...reportApi,
}))

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
    for (const l of ['Solar', 'Grid import', 'Battery', 'Direct', 'Load', 'Losses']) expect(labels).toContain(l)
    expect(screen.getByTestId('ef-conservation').getAttribute('data-status')).toBe('ok')
    expect(screen.getByTestId('ef-series-supply')).toBeTruthy()
    expectAllButtonsNamed(container)
  })

  it('exports exactly the rows it draws', async () => {
    const spy = vi.spyOn(shared, 'downloadCSV').mockImplementation(() => {})
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'CSV' }))
    const [file, header, rows] = spy.mock.calls[0]
    expect(file).toBe('energy_flows_year.csv')
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

describe('undelivered product', () => {
  it('names what a process could not deliver, beside the flows', async () => {
    vi.mocked(energyFlowsApi.get).mockResolvedValue({ ...OK, unserved_product_mwh: { nh3_delivery: 2500 } })
    mount()
    expect((await screen.findByTestId('ef-undelivered-nh3_delivery')).textContent).toMatch(/Not delivered to nh3_delivery: 2\.5 GWh/)
    expect(screen.getByTestId('ef-sankey')).toBeTruthy()
  })

  it('says nothing when everything was delivered', async () => {
    vi.mocked(energyFlowsApi.get).mockResolvedValue({ ...OK, unserved_product_mwh: {} })
    mount()
    await screen.findByTestId('ef-sankey')
    expect(document.querySelector('[data-testid^="ef-undelivered"]')).toBeNull()
  })
})

describe('the Sankey frame', () => {
  it('heads the diagram with the shared chart frame: the total, the unit and both exports', async () => {
    mount()
    const frame = await screen.findByTestId('ef-flow')
    expect(frame.hasAttribute('data-chart-frame')).toBe(true)
    expect(within(frame).getByRole('heading', { name: /Energy flow · / })).toBeTruthy()
    const total = ROWS.reduce((s, r) => s + r.mwh, 0)
    expect(frame.textContent).toContain(`${fmtMwh(total)} delivered or lost`)
    // One unit in the header: the figure carries it, the hint does not repeat another.
    expect(frame.textContent).not.toContain('MWh ·')
    expect(within(frame).getByRole('button', { name: 'SVG' })).toBeTruthy()
    expect(within(frame).getByRole('button', { name: 'CSV' })).toBeTruthy()
    // The total is said once: the strip above keeps only the losses share.
    expect(screen.getByTestId('ef-view').textContent!.split('delivered or lost')).toHaveLength(2)
  })

  it('says an empty window in the frame, with no diagram and no SVG export', async () => {
    vi.mocked(energyFlowsApi.get).mockResolvedValue({ ...OK, rows: [] })
    mount()
    const frame = await screen.findByTestId('ef-flow')
    expect(within(frame).getByTestId('ef-empty').textContent).toBe('No energy moved in this window.')
    expect(within(frame).getByTestId('chart-zero')).toBeTruthy()
    expect(screen.queryByTestId('ef-sankey')).toBeNull()
    expect(within(frame).queryByRole('button', { name: 'SVG' })).toBeNull()
  })
})

describe('the source line on a multi-period run', () => {
  const line = 'Latest solve · solved · 2027-01-01 00:00 → 2030-12-31 23:00 · all periods'
  const mountIn = () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={client}>
        <shared.ChartSourceProvider value={{ line, stale: false }}><EnergyFlowsTab sankeyWidth={700} /></shared.ChartSourceProvider>
      </QueryClientProvider>)
  }

  it('names the one period the diagram draws, not "all periods"', async () => {
    vi.mocked(energyFlowsApi.get).mockResolvedValue({ ...OK, period: 2027, periods: [2027, 2030] })
    mountIn()
    const frame = await screen.findByTestId('ef-flow')
    expect(within(frame).getByRole('heading', { name: 'Energy flow · 2027' })).toBeTruthy()
    const source = frame.querySelector('[data-chart-source]')!.textContent!
    expect(source).toContain('period 2027')
    expect(source).not.toContain('all periods')
    // The dates are the whole horizon's, not the drawn period's.
    expect(source).not.toContain('2030-12-31')
    expect(screen.getByTestId('ef-one-period').textContent).toBe('All periods are selected above. The diagram draws one period: 2027.')
    // The supply and demand charts below read the same period.
    expect(screen.getByTestId('ef-series-supply').querySelector('[data-chart-source]')!.textContent).toContain('period 2027')
  })

  it('leaves the line alone on a single-period run', async () => {
    mountIn()
    const frame = await screen.findByTestId('ef-flow')
    expect(frame.querySelector('[data-chart-source]')!.textContent).toBe(line)
    expect(screen.queryByTestId('ef-one-period')).toBeNull()
  })
})

describe('cross-link', () => {
  it('points at the money view and asks for that tab', async () => {
    mount()
    fireEvent.click(await screen.findByTestId('ef-to-value-flows'))
    expect(useUIStore.getState().resultsTabRequest).toBe('investment')
    // One "›" link, and Back names where the user came from.
    expect(screen.getByTestId('ef-to-value-flows').textContent).toBe('Investment: value flows ›')
    expect(JSON.stringify(useUIStore.getState().flowReturn)).toContain('Energy flow')
  })
})

describe('FlowSankeyChart', () => {
  it('draws a path per link', () => {
    const { container } = render(<FlowSankeyChart data={sankeyData(ROWS)} width={700} />)
    expect(container.querySelectorAll('svg path').length).toBeGreaterThanOrEqual(6)
  })
})

describe('seriesName', () => {
  it('reads a storage series in plain words, never as a raw id', () => {
    expect(seriesName('ups_battery discharge')).toBe('UPS battery discharge')
    expect(seriesName('bess_new_2027 charge')).toBe('BESS new 2027 charge')
    expect(seriesName('solar')).toBe('Solar')
    expect(seriesName('Grid import')).toBe('Grid import')
  })
})

describe('energy flow · report', () => {
  beforeEach(() => {
    resetReportCharts()
    reportApi.listReportCharts.mockReset().mockResolvedValue({ charts: [], limit: 12 })
    reportApi.addReportChart.mockReset().mockImplementation(async (_p: string, spec: ChartSpec) => ({
      id: 'chart-0123456789abcdef', created_at: 't', earlier_solve: false, ...spec,
    }))
    useUIStore.setState({ currentProject: 'demo' })
  })

  async function save(endHour?: string) {
    // The server answers with the window it was asked for.
    vi.mocked(energyFlowsApi.get).mockImplementation(async (q) =>
      ({ ...OK, window: { ...OK.window!, from: q?.from ?? 0, to: q?.to ?? 167 } }))
    mount()
    if (endHour) {
      fireEvent.change(await screen.findByLabelText('Window end'), { target: { value: endHour } })
      await waitFor(() => expect(energyFlowsApi.get).toHaveBeenLastCalledWith(
        expect.objectContaining({ to: Number(endHour) })), { timeout: 2000 })
      await waitFor(() => expect(screen.getByTestId('ef-flow')).toBeTruthy())
    }
    const frame = await screen.findByTestId('ef-flow')
    await userEvent.click(await within(frame).findByRole('button', { name: 'Add to report' }))
    await waitFor(() => expect(reportApi.addReportChart).toHaveBeenCalled())
    const spec = reportApi.addReportChart.mock.calls.at(-1)![1] as ChartSpec
    cleanup()
    reportApi.addReportChart.mockClear()
    return spec
  }

  it('saves the flow table with its figure id', async () => {
    const spec = await save()
    expect(spec.view).toBe('energy-flows')
    expect(spec.figure).toBe('energy_flows_year_0-167')
    expect(spec.unit).toBe('MWh')
    expect(spec.columns).toEqual(['Source', 'Via', 'Destination', 'MWh'])
    expect(spec.rows).toHaveLength(ROWS.length)
  })

  it('two windows are two figures', async () => {
    const a = await save()
    const b = await save('23')
    expect(b.figure).toBe('energy_flows_year_0-23')
    expect(a.figure).not.toBe(b.figure)
  })
})
