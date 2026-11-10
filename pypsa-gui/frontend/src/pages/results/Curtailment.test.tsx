import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import Curtailment from './Curtailment'
import { findKpiValue } from '../../test/kpi'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getCurtailment: vi.fn(),
      getGeneratorResults: vi.fn(),
      getCurtailmentByCause: vi.fn(),
    },
  }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getGenerators: vi.fn(),
      getSnapshots: vi.fn(),
      getInvestmentPeriods: vi.fn(),
    },
  }
})

// Count how often the over-time chart is drawn. The real ResponsiveContainer
// draws nothing in jsdom (no size), so it passes its child through here.
const areaChartRenders = vi.hoisted(() => ({ count: 0 }))
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    AreaChart: () => { areaChartRenders.count += 1; return <div data-testid="curtailment-over-time" /> },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Single renewable generator, single snapshot, weight 1 (via getSnapshots
  // below): Σ curtailed MW × weighting × years over one point equals that
  // point.
  vi.mocked(resultsApi.getCurtailment).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['SolarGen'], data: [[424.24]],
  })
  vi.mocked(resultsApi.getCurtailmentByCause).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.getGeneratorResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['SolarGen'], data: [[0]],
  })
  // Full Generator shape (api/types.ts:33-55) — 29 fields, not just the 4
  // the brief supplied. Curtailment.tsx reads only `name` and `carrier` off
  // each row: `carrierByName` (:46) and `renewableNames` (:62-64). Every
  // other field below is an inert PyPSA-default / "no constraint" value
  // (mirrors Dispatch.test.tsx's fixture, which traced the same interface)
  // that nothing in this test's render path consumes.
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue([
    {
      name: 'SolarGen', bus: 'Bus 0', carrier: 'solar', p_nom: 500,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      p_min_pu: 0, p_max_pu: 1, control: 'PQ',
      marginal_cost: 0, capital_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null, curtailment_cost: 0,
      efficiency: 1, committable: false,
      ramp_limit_up: null, ramp_limit_down: null,
      start_up_cost: 0, shut_down_cost: 0, min_up_time: 0, min_down_time: 0,
      e_sum_min: null, e_sum_max: null,
      build_year: 0, lifetime: null, unit: 'MW',
    },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [], weightings: [] })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Curtailment />
    </QueryClientProvider>,
  )
}

it('renders a distinctive curtailed-energy KPI sourced from a single mocked snapshot', async () => {
  renderPage()
  // Curtailment.tsx's own local fmtEnergy (lines 199-205) rounds the MWh
  // tier to 1 decimal: "424.2 MWh". With a single carrier ('solar') and a
  // single snapshot, the per-carrier table's "By carrier" row (:137) sums to
  // the SAME total as the "Curtailed energy" KPI (:110-111) — both derive
  // from `weightedSum` over the identical one-column, one-row payload, so
  // both render the literal string "424.2 MWh". `findByText` requires
  // exactly one match; use `findAllByText` and assert on the known count
  // (2) instead, same resolution Dispatch.test.tsx used for its own
  // legitimate duplicate.
  // The by-carrier table keeps full precision ("424.2 MWh"); the KPI tile
  // rounds to the R1 standard (3 s.f.): "424 MWh".
  expect(await findKpiValue('424 MWh')).toBeTruthy()
  // The hour-by-day map below (ui-modern 02 slice 3) also writes the hour's
  // "424.2 MW" in its key and its text alternative; count only the table's
  // <td>, which is what this test pins (as Prices.test.tsx does).
  expect(screen.getAllByText((text) => text.includes('424.2')).filter(el => el.tagName === 'TD').length).toBe(1)
})

// ── Result states (UX overhaul S2) ──────────────────────────────────────────
it('says curtailment does not apply when the network has no renewable generators', async () => {
  vi.mocked(networkApi.getGenerators).mockResolvedValue([])
  renderPage()
  // One section-level state for the whole tab, not three tiles and two blocks.
  expect((await screen.findAllByText('Not applicable')).length).toBe(1)
  expect(screen.queryByText('Curtailed energy')).toBeNull()
  expect(document.querySelector('[data-kpi-value]')).toBeNull()
  expect(screen.queryByText(/dispatched every available MW/)).toBeNull()
})

it('shows a computed zero with what makes it zero, and a sentence instead of an empty chart', async () => {
  vi.mocked(resultsApi.getCurtailment).mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['SolarGen'], data: [[0]],
  })
  renderPage()
  await screen.findByText('Curtailed energy')
  const tile = document.querySelector('[data-state="zero"]')
  expect(tile?.querySelector('[data-kpi-value="0 MWh"]')).not.toBeNull()
  expect(tile?.textContent).toMatch(/all available renewable energy used/)
  expect(screen.getByText(/No curtailment in this range/)).toBeTruthy()
  expect(document.querySelector('.recharts-responsive-container')).toBeNull()
})

// IPP ticket 05: the split by cause sits above the hourly chart.
it('shows the curtailment by cause above the hourly chart', async () => {
  vi.mocked(resultsApi.getCurtailmentByCause).mockResolvedValue({
    status: 'not_established', reason: 'export_link_missing',
    plants: { SolarGen: { total_mwh: 424.24, available_mwh: 424.24 } },
  })
  renderPage()
  const table = await screen.findByTestId('curtailment-by-cause')
  const chart = screen.getByText('Curtailment over time')
  expect(table.compareDocumentPosition(chart) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('does not redraw the over-time chart when the tab re-renders with the same data', async () => {
  // The app re-renders Results every few seconds while it polls. On a full
  // year the over-time chart has 8760 x-axis ticks and Recharts measures each
  // one per draw, so a redraw per poll leaves the tab busy most of the time.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = () => (
    <QueryClientProvider client={client}>
      <Curtailment />
    </QueryClientProvider>
  )
  const { rerender } = render(page())
  await screen.findByTestId('curtailment-over-time')
  await findKpiValue('424 MWh')
  const drawn = areaChartRenders.count
  rerender(page())
  rerender(page())
  expect(areaChartRenders.count).toBe(drawn)
})
