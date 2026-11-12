import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ChartSpec } from '../../api/reports'
import { resetReportCharts } from './reportCharts'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import Prices from './Prices'

// A fixed-size chart, so recharts draws in jsdom (its ResponsiveContainer
// measures 0 × 0 there and renders nothing).
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const { cloneElement } = await import('react')
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement<{ width?: number; height?: number }> }) =>
      cloneElement(children, { width: 600, height: 200 }),
  }
})

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getPrices: vi.fn(), getEmissions: vi.fn(), getPriceDrivers: vi.fn(),
    },
  }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getBuses: vi.fn(), getSnapshots: vi.fn() },
  }
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

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Single bus, single snapshot: mean/peak/trough of one point are all that
  // same point, regardless of the exact busStats aggregation implementation.
  vi.mocked(resultsApi.getPrices).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Bus 0'], data: [[424.24]],
  })
  // getEmissions' real return type (api/simulation.ts:260-306) requires
  // by_generator/cap/caps/is_multi_period/by_period beyond what the brief
  // supplied; the brief's literal transcription fails tsc AND crashes at
  // runtime (`emissions?.cap.active` throws on a missing `cap`). Filled
  // with inert defaults — `cap.active: false` keeps the CO2-shadow-price KPI
  // hidden, and Prices.tsx never reads by_generator/caps/is_multi_period/by_period.
  vi.mocked(resultsApi.getEmissions).mockReset().mockResolvedValue({
    total_tCO2: 0, by_carrier: [], by_generator: [],
    cap: { active: false }, caps: [], is_multi_period: false, by_period: [],
  })
  // getPriceDrivers' real return type is an object ({threshold,
  // total_above_threshold, truncated, rows}), not a bare array. The brief's
  // `[]` fails tsc AND crashes at runtime: Prices.tsx:470 reads
  // `priceDrivers.rows.length` unconditionally once `priceDrivers` is
  // truthy, and an array has no `.rows`. Fixed to the real empty shape.
  vi.mocked(resultsApi.getPriceDrivers).mockReset().mockResolvedValue({
    threshold: 2000, total_above_threshold: 0, truncated: false, rows: [],
  })
  // Bus (api/types.ts:1-4) requires x/y/country/unit/control/sub_network
  // beyond what the brief supplied. Prices.tsx only reads `carrier`/`v_nom`
  // off a bus row, so these six are inert padding to satisfy the type.
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([
    { name: 'Bus 0', v_nom: 380, carrier: 'AC',
      x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' },
  ])
  // Prices.tsx now windows its `getPrices` fetch via `useResultsWindow`,
  // which reads `/api/network/snapshots` to resolve the fetch bounds before
  // the prices query is allowed to fire (`enabled: winValid`). Without this
  // mock the real (unmocked) `getSnapshots` never resolves in the test
  // environment, `winValid` stays false forever, and the prices query never
  // fires — same single-snapshot fixture shape Curtailment/LostLoadTab's
  // tests already use.
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Prices />
    </QueryClientProvider>,
  )
}

it('renders a distinctive per-bus price sourced from a single mocked bus/snapshot', async () => {
  renderPage()
  // The brief's single-match `findByText('424.24')` throws
  // "Found multiple elements" here: the per-bus table's Mean/Peak/Trough
  // columns coincide on a one-snapshot fixture (confirmed by running it —
  // see task-20-report.md), exactly the collision the brief's own comment
  // above predicts. Using findAllByText (the same fix Task 18's Dispatch
  // brief applied for the identical coincidence) keeps the assertion tied
  // to the fixture's 424.24 without depending on how many cells repeat it.
  // findAllByText already throws if zero elements match, so a bare
  // toBeGreaterThan(0) is vacuous — it can never fail and leaves the count
  // unpinned. Empirically confirmed (by probing cell position within the
  // matched `<tr>`) that the 3 matches are the row's Mean/Peak/Trough `<td>`
  // cells (`Prices.tsx:269-271`) at cellIndex 3/4/5 respectively — three
  // genuinely distinct render sites, not one node counted three times.
  // The three System KPI tiles (mean / peak / trough) also show 424.24 now that
  // components/Kpi.tsx renders the number apart from its unit; count only the
  // table's <td> cells, which is what this test pins.
  const matches = (await screen.findAllByText('424.24')).filter(el => el.tagName === 'TD')
  expect(matches.length).toBe(3)
})

it('marks the mean on the price chart and the duration curve, rounded like the tile (S4b)', async () => {
  const { container } = renderPage()
  await screen.findAllByText('424.24')
  const labels = Array.from(container.querySelectorAll('.recharts-reference-line text'))
    .map(t => t.textContent)
  expect(labels).toEqual(['mean 424.2 €/MWh', 'mean 424.2 €/MWh'])
})

// ── "Add to report" on the hourly price chart ───────────────────────────────
describe('hourly price chart · report', () => {
  // 28 days (one 12:00 stamp each) in every season: weekly and monthly both become available.
  const stamps = [1, 4, 7, 10].flatMap(m => Array.from({ length: 28 }, (_, d) =>
    `2026-${String(m).padStart(2, '0')}-${String(d + 1).padStart(2, '0')}T12:00:00`))
  beforeEach(() => {
    try { localStorage.clear() } catch { /* ignore */ }
    resetReportCharts()
    reportApi.listReportCharts.mockReset().mockResolvedValue({ charts: [], limit: 12 })
    reportApi.addReportChart.mockReset().mockImplementation(async (_p: string, spec: ChartSpec) => ({
      id: 'chart-0123456789abcdef', created_at: 't', earlier_solve: false, ...spec,
    }))
    useUIStore.setState({ currentProject: 'demo' })
    vi.mocked(resultsApi.getPrices).mockResolvedValue({
      index: stamps, columns: ['Bus 0', 'Bus 1'],
      data: stamps.map((_, i) => [40 + (i % 7), 50 + (i % 5)]),
      data_adjusted: stamps.map((_, i) => [30 + (i % 7), 35 + (i % 5)]),
    } as never)
    vi.mocked(networkApi.getBuses).mockResolvedValue([
      { name: 'Bus 0', v_nom: 380, carrier: 'AC', x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' },
      { name: 'Bus 1', v_nom: 380, carrier: 'AC', x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' },
    ])
    vi.mocked(networkApi.getSnapshots).mockResolvedValue({
      count: stamps.length, snapshots: stamps, weightings: [], ts_start: null, ts_end: null,
      can_sample_weeks: false,
    })
  })

  async function pick(mode: string, view: string, source: string) {
    renderPage()
    const frame = (await screen.findByRole('heading', { name: 'Hourly marginal price' })).closest('[data-chart-frame]') as HTMLElement
    await waitFor(() => expect((within(frame).getByRole('button', { name: 'Merit order' }) as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(within(frame).getByRole('button', { name: mode }))
    await userEvent.click(within(frame).getByRole('button', { name: view }))
    await userEvent.click(within(frame).getByRole('button', { name: source }))
    await userEvent.click(within(frame).getByRole('button', { name: 'Add to report' }))
    await waitFor(() => expect(reportApi.addReportChart).toHaveBeenCalled())
    const spec = reportApi.addReportChart.mock.calls.at(-1)![1] as ChartSpec
    cleanup()
    reportApi.addReportChart.mockClear()
    return spec
  }

  it('saves the seasonal weekly chart with a season column', async () => {
    const spec = await pick('Weekly', 'Average', 'LP dual')
    expect(spec.view).toBe('prices')
    expect(spec.figure).toBe('weekly_avg_lp')
    expect(spec.columns![0]).toBe('season')
    expect(spec.rows!.length).toBeGreaterThan(0)
  })
  it('saves the monthly and full-timeframe charts', async () => {
    const m = await pick('Monthly', 'Per bus', 'LP dual')
    expect(m.figure).toBe('monthly_per_bus_lp')
    expect(m.columns![0]).toBe('season')
    expect(m.columns).toContain('Bus 1')
    const t = await pick('Full timeframe', 'Average', 'LP dual')
    expect(t.figure).toBe('hourly_avg_lp')
    expect(t.columns).toEqual(['snapshot', 'avg'])
    expect(t.rows!.length).toBeGreaterThan(0)
  })
  it('mode, view and source each change the figure id', async () => {
    const ids = new Set([
      (await pick('Weekly', 'Average', 'LP dual')).figure,
      (await pick('Monthly', 'Average', 'LP dual')).figure,
      (await pick('Weekly', 'Per bus', 'LP dual')).figure,
      (await pick('Weekly', 'Average', 'Merit order')).figure,
    ])
    expect(ids.size).toBe(4)
  })
})
