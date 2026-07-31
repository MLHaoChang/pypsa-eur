import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import Dispatch from './Dispatch'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getCostBreakdown: vi.fn(),
      getGeneratorResults: vi.fn(),
      getCurtailment: vi.fn(),
      getStorageResults: vi.fn(),
      getStorageDispatchResults: vi.fn(),
      getStoreDispatchResults: vi.fn(),
      getStoreEnergyResults: vi.fn(),
      getLoadResults: vi.fn(),
      getLinkResults: vi.fn(),
      getLostLoad: vi.fn(),
      getEconomicsByCarrier: vi.fn(),
    },
  }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getGenerators: vi.fn(), getStorageUnits: vi.fn(),
      getStores: vi.fn(), getLoads: vi.fn(), getLinks: vi.fn(),
      listVintageResults: vi.fn(), getSnapshots: vi.fn(), getInvestmentPeriods: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  const emptyTs = { index: [], columns: [], data: [] }
  // Full CostBreakdown shape (api/simulation.ts:62-110). Dispatch.tsx's own
  // hidden OPEX block reads `cost.by_component.map(...)` directly in the
  // render path with no optional chaining, falling back to it only when
  // `periodEntry?.by_component` is absent (Dispatch.tsx:1310-1311) — which
  // it is here since `filter.selectedPeriod` defaults to `null`. A
  // `{ total: 0 }` stub leaves `by_component` `undefined` and throws
  // `TypeError` during render, same failure mode as AggregatedOverview.tsx.
  vi.mocked(resultsApi.getCostBreakdown).mockReset().mockResolvedValue({
    capex: 0, capex_lifetime: 0, capex_expansion: 0, capex_expansion_lifetime: 0,
    opex: 0, total: 0, curtailment_cost: 0,
    storage_capex_expansion: 0, storage_capex_expansion_lifetime: 0,
    by_component: [], by_carrier: [], by_period: [],
  })
  vi.mocked(resultsApi.getGeneratorResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['ThermalGen'], data: [[424.24]],
  })
  vi.mocked(resultsApi.getCurtailment).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getStorageResults).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getStoreDispatchResults).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getStoreEnergyResults).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getLoadResults).mockReset().mockResolvedValue(emptyTs)
  vi.mocked(resultsApi.getLinkResults).mockReset().mockResolvedValue(emptyTs)
  // Full getLostLoad shape (api/simulation.ts:357-367): `total_mwh` /
  // `total_cost_eur` / `voll_eur_per_mwh` are required alongside the
  // index/columns/data TS payload, not optional. With `data: []`,
  // Dispatch.tsx's `lostLoadTotals` (:643-661) takes the `ts.data.length
  // === 0` branch and reads `total_mwh`/`total_cost_eur` straight off this
  // object (defaulting to 0 via `?? 0` either way, so 0 here is a no-op
  // versus the brief's under-typed literal, not a value change); neither
  // field feeds `kpis.thermal`/`kpis.totalGen`, which this test asserts on.
  vi.mocked(resultsApi.getLostLoad).mockReset().mockResolvedValue({
    index: [], columns: [], data: [],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 0,
  })
  // Real shape is `{ by_carrier?: Record<...> }` (api/simulation.ts:337-350);
  // Dispatch.tsx reads it as `econByCarrier?.by_carrier` (:1594, :1681).
  vi.mocked(resultsApi.getEconomicsByCarrier).mockReset().mockResolvedValue({})
  // Full Bus shape (api/types.ts:1-4). `busCarrierGroups` (Dispatch.tsx:397-455)
  // and `busCarrier` (:788-794) read only `name` and `carrier` off each row
  // (via a local `Array<{ name: string; carrier?: string }>` cast); the
  // remaining fields below are inert defaults that nothing in the render
  // path this test exercises consumes.
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([
    {
      name: 'Bus 0', v_nom: 380, carrier: 'AC', x: 0, y: 0,
      country: '', unit: '', control: 'PQ', sub_network: '',
    },
  ])
  // Full Generator shape (api/types.ts:33-55) — 26 fields, not just the 4
  // this test cares about. `groupCols` (Dispatch.tsx:299-307), which feeds
  // `kpis.thermal`/`kpis.totalGen` via `weightedSum`, reads only `name` and
  // `carrier`; `curtailmentCost` (:703-719) reads `curtailment_cost` but
  // short-circuits to 0 before ever looking at it because `curtailTS` is
  // `emptyTs` (`ts.data.length === 0`, :705). Every other field below is an
  // inert PyPSA-default / "no constraint" value that nothing in this test's
  // render path consumes, so filling them in to satisfy the interface
  // cannot change any value the assertion depends on.
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue([
    {
      name: 'ThermalGen', bus: 'Bus 0', carrier: 'gas', p_nom: 100,
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
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStores).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([])
  // Real shape is `{ results: Record<class, Record<name, {...}>> }`
  // (api/network.ts:201-207).
  vi.mocked(networkApi.listVintageResults).mockReset().mockResolvedValue({ results: {} })
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
      <Dispatch />
    </QueryClientProvider>,
  )
}

it('renders a distinctive thermal dispatch value sourced from the mocked results API', async () => {
  renderPage()
  // Traced against Dispatch.tsx directly. `hasResults` (:1162-1163) is true
  // (gensTS is non-null), so the component clears its "no solved network"
  // early return. Default `view` state is 'dispatch' (:1169) and
  // `effectiveMode` (useSeasonalViewMode, shared.tsx:912-956) falls back to
  // 'timeframe' because a single mocked snapshot can't satisfy
  // `weeklyAvailable`/`monthlyAvailable` — so the branch at :1631
  // (`busCarrierGroups.length <= 1 ? <DispatchStack .../> : ...`) renders
  // the legacy single-stack layout, not `CarrierKpiPanel` (our one-bus
  // fixture makes `busCarrierGroups.length === 1`). `CarrierKpiPanel` is
  // therefore never mounted and contributes no text.
  //
  // The only place the mocked 424.24 value surfaces as text is the legacy
  // "System dispatch" KPI strip at :1230, wrapped in
  // `<section style={{display: 'none'}}>` (the code comment there says this
  // was "REMOVED" in favour of per-carrier panels, but the JSX is still
  // mounted — only visually hidden). `kpis.thermal` (:730) is
  // `weightedSum(gensTS, groupCols.thermal, weightCtx, range, 'generators')`;
  // `range` (:490, via `resolveRange`) is `{from: 0, to: 0}` for the single
  // mocked row and the default (no `ResultsFilterProvider`) filter context.
  // `weightCtx` comes from `useWeightCtx(refTs)` (shared.tsx:153-174), whose
  // OWN internal query resolves our mocked `getSnapshots().weightings: []`
  // — an empty array — so `effectiveWeightAt` (shared.tsx:230) never enters
  // its weighting branch and the multiplier is 1. So
  // `kpis.thermal === 424.24` unweighted, rendering "424.24 MWh"
  // (`fmtEnergy`, shared.tsx:481-488) at the "Thermal" KPI card (:1258).
  // `kpis.totalGen` (thermal + renewable, renewable = 0) evaluates to the
  // SAME 424.24 and renders an IDENTICAL "424.24 MWh" string at the
  // "Total generation" card (:1260) — inside the SAME hidden section.
  //
  // Testing Library's `getByText`/`findByText` do not filter on CSS
  // visibility, so both hidden nodes are still queryable — but because
  // there are two of them, a single-result query (`findByText`) throws
  // "multiple elements". Use `findAllByText` (which permits >1 match) and
  // assert the literal computed string is present at least once, rather
  // than trying to disambiguate a duplicate produced by an already-hidden,
  // marked-for-removal legacy section.
  const matches = await screen.findAllByText('424.24 MWh')
  expect(matches.length).toBeGreaterThan(0)
})
