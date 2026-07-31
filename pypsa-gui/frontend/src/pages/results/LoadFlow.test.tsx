import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import LoadFlow from './LoadFlow'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getAcPfStatus: vi.fn(), getLineResults: vi.fn(), getTransformerResults: vi.fn(),
      getPrices: vi.fn(), getEmissions: vi.fn(), getCarrierKpis: vi.fn(),
      getLineDuals: vi.fn(), getUnitCommitment: vi.fn(), getLosses: vi.fn(),
      getVoltages: vi.fn(), getLineReactive: vi.fn(), getTransformerReactive: vi.fn(),
      getPriceDrivers: vi.fn(),
    },
  }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getSnapshots: vi.fn(), getLines: vi.fn(), getTransformers: vi.fn(), getBuses: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  const emptyTs = { index: [], columns: [], data: [] }
  vi.mocked(resultsApi.getAcPfStatus).mockReset().mockResolvedValue({ available: false })
  // Single line, single snapshot: max(|p0|) over one point equals that
  // point regardless of the exact peak-flow aggregation implementation.
  vi.mocked(resultsApi.getLineResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Line 1'], data: [[424.24]],
  })
  vi.mocked(resultsApi.getTransformerResults).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getPrices).mockReset().mockResolvedValue(emptyTs)
  // Full getEmissions shape (api/simulation.ts:260-306) — `client.get<{...}>`
  // carries an explicit generic, so the mock is checked against the whole
  // interface, not just `total_tCO2`/`by_carrier`. Every field below beyond
  // those two is an inert default: `emissions` is only read inside the
  // `SHOW_RETIRED_SECTIONS && emissions && (...)` block (LoadFlow.tsx:610),
  // and `SHOW_RETIRED_SECTIONS` is the literal `false as boolean` (:35), so
  // that whole branch is dead at runtime — nothing under it, including
  // `by_generator`/`cap`/`caps`/`is_multi_period`/`by_period`, ever reaches
  // the DOM regardless of value.
  vi.mocked(resultsApi.getEmissions).mockReset().mockResolvedValue({
    total_tCO2: 0, by_carrier: [], by_generator: [],
    cap: { active: false }, caps: [], is_multi_period: false, by_period: [],
  })
  // Real shape is `{ rows: Array<...> } | null` (api/simulation.ts:243-256),
  // not a bare array — `client.get<{rows:...}>` carries an explicit generic.
  // LoadFlow.tsx reads `carrierKpis.rows.length` unconditionally at :708; a
  // bare `[]` is truthy but has no `.rows`, so it would throw
  // `TypeError: Cannot read properties of undefined (reading 'length')`
  // during render. `rows: []` keeps the guard falsy (section doesn't
  // render) without touching anything this test asserts on.
  vi.mocked(resultsApi.getCarrierKpis).mockReset().mockResolvedValue({ rows: [] })
  // Real shape is `{ rows, total_congestion_rent_eur, n_snapshots, note? }`
  // (api/simulation.ts:221-238), not a TSPayload — `client.get<{...}>` again
  // carries an explicit generic. LoadFlow.tsx reads `lineDuals.rows.length`
  // unconditionally at :779; the TSPayload `emptyTs` shape has no `.rows`
  // and would crash the same way as carrierKpis above. `rows: []` keeps the
  // section hidden; `total_congestion_rent_eur`/`n_snapshots` are inert
  // (only read inside that now-untaken branch).
  vi.mocked(resultsApi.getLineDuals).mockReset().mockResolvedValue({
    rows: [], total_congestion_rent_eur: 0, n_snapshots: 0,
  })
  // Real shape is `{ generators, status_grid, n_committable, note? }`
  // (api/simulation.ts:205-217), not a TSPayload. LoadFlow.tsx reads
  // `ucResults.n_committable > 0` at :852 — a TSPayload's `.n_committable`
  // is `undefined`, and `undefined > 0` is `false` (no crash), but the
  // shape still fails `tsc` against the explicit generic. `n_committable: 0`
  // keeps the section hidden; `generators: []`/`status_grid: null` are inert.
  vi.mocked(resultsApi.getUnitCommitment).mockReset().mockResolvedValue({
    generators: [], status_grid: null, n_committable: 0,
  })
  // Real shape is `{ enabled, total_mwh, peak_mw, total_demand_mwh,
  // loss_pct_of_demand, by_branch }` (api/simulation.ts:373-380). Unlike the
  // three mocks above, this section renders UNCONDITIONALLY ("Always
  // rendered when a solved network is available", LoadFlow.tsx:968) and
  // reads `lossesData.total_mwh.toLocaleString(...)` etc. with no optional
  // chaining (:982-986) — a TSPayload `emptyTs` has no `.total_mwh` and
  // would crash on `undefined.toLocaleString`. All-zero/false values render
  // as "0.00 MWh" / "0.00 MW" / "0.000 %", none of which collide with the
  // "424.24 MW" string this test asserts on.
  vi.mocked(resultsApi.getLosses).mockReset().mockResolvedValue({
    enabled: false, total_mwh: 0, peak_mw: 0, total_demand_mwh: 0,
    loss_pct_of_demand: 0, by_branch: [],
  })
  vi.mocked(resultsApi.getVoltages).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getLineReactive).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getTransformerReactive).mockReset().mockResolvedValue(emptyTs)
  // Real shape is `{ threshold, total_above_threshold, truncated, rows }`
  // (api/simulation.ts:320-330), not a bare array. The consuming section
  // (:1606) is itself gated by `SHOW_RETIRED_SECTIONS && priceTS` and so is
  // dead at runtime regardless of this value, but the explicit generic on
  // `client.get<{...}>` still fails `tsc` against a bare `[]`. All-zero/
  // empty values are inert either way.
  vi.mocked(resultsApi.getPriceDrivers).mockReset().mockResolvedValue({
    threshold: 2000, total_above_threshold: 0, truncated: false, rows: [],
  })
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
  // Full Line shape (api/types.ts:8-23) — `getLines` is typed `Line[]` via
  // an explicit generic. `lineStats` (LoadFlow.tsx:195-227) reads only
  // `name`/`bus0`/`bus1`/`s_nom` off each row (plus `s_nom_opt` through an
  // `as unknown as {...}` cast the production code already applies for a
  // PyPSA column the `Line` interface doesn't declare — no fixture-side
  // cast needed since we simply omit the optional field). `peak`, which
  // `kpis.peakFlow` is built from, comes entirely from the mocked
  // `getLineResults` time series above, not from any of these fields. The
  // rest are inert PyPSA defaults nothing in this test's render path reads.
  vi.mocked(networkApi.getLines).mockReset().mockResolvedValue([
    {
      name: 'Line 1', bus0: 'Bus 0', bus1: 'Bus 1', length: 0, r: 0, x: 0, b: 0,
      s_nom: 1000, s_nom_extendable: false, s_nom_min: 0, s_nom_max: null,
      capital_cost: 0, fom_cost: 0, overnight_cost: null, discount_rate: null,
      carrier: 'AC', build_year: 0, lifetime: null,
    },
  ])
  vi.mocked(networkApi.getTransformers).mockReset().mockResolvedValue([])
  // Full Bus shape (api/types.ts:1-4) — `getBuses` is typed `Bus[]` via an
  // explicit generic. Nothing in this test's render path reads beyond
  // `name`/`v_nom`/`carrier` (busStats / voltage tables key off `priceTS`
  // and `voltagesTS`, both empty here); the rest are inert defaults.
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([
    { name: 'Bus 0', v_nom: 380, carrier: 'AC', x: 0, y: 0, country: '', unit: '', control: 'PQ', sub_network: '' },
  ])
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LoadFlow />
    </QueryClientProvider>,
  )
}

it('renders a distinctive peak-flow KPI sourced from a single mocked line/snapshot', async () => {
  renderPage()
  const match = await screen.findByText((text) => text.includes('424.24 MW'))
  expect(match).toBeTruthy()
})
