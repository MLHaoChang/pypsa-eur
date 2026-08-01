import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import AggregatedOverview from './AggregatedOverview'
import type { WeightCtx, SnapshotWeightRow } from './shared'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getGeneratorResults: vi.fn(),
      getStorageDispatchResults: vi.fn(),
      getLoadResults: vi.fn(),
      getCurtailment: vi.fn(),
      getLostLoad: vi.fn(),
      getCostBreakdown: vi.fn(),
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
      getStorageUnits: vi.fn(),
      getLoads: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getGeneratorResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'],
    columns: ['ThermalGen'],
    data: [[424.24]],
  })
  vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
    index: [], columns: [], data: [],
  })
  vi.mocked(resultsApi.getLoadResults).mockReset().mockResolvedValue({
    index: [], columns: [], data: [],
  })
  vi.mocked(resultsApi.getCurtailment).mockReset().mockResolvedValue({
    index: [], columns: [], data: [],
  })
  // Full getLostLoad shape (api/simulation.ts:357-367): `total_mwh` /
  // `total_cost_eur` / `voll_eur_per_mwh` are required alongside the
  // index/columns/data TS payload, not optional. With `data: []`,
  // AggregatedOverview.tsx's `lostLoadTotals` (:128-134) takes the
  // `ts.data.length === 0` branch and reads `total_mwh`/`total_cost_eur`
  // straight off this object (defaulting to 0 via `?? 0` either way, so 0
  // here is a no-op versus the previous under-typed fixture, not a value
  // change); `voll_eur_per_mwh` only feeds `economicsBars`' VOLL-per-period
  // calc, which this test never asserts on.
  vi.mocked(resultsApi.getLostLoad).mockReset().mockResolvedValue({
    index: [], columns: [], data: [],
    total_mwh: 0, total_cost_eur: 0, voll_eur_per_mwh: 0,
  })
  // Full CostBreakdown shape (api/simulation.ts:62-110). AggregatedOverview.tsx's
  // OPEX section reads `cost.by_component.filter(...)` directly in the JSX
  // render path (AggregatedOverview.tsx:527, no optional chaining, not
  // behind a click handler). A `{ total: 0 }` stub leaves `by_component`
  // `undefined`, and `undefined.filter(...)` throws `TypeError` during
  // render, failing this test before the assertion ever runs. Every field is
  // populated so the render path that reads them doesn't crash.
  vi.mocked(resultsApi.getCostBreakdown).mockReset().mockResolvedValue({
    capex: 0, capex_lifetime: 0, capex_expansion: 0, capex_expansion_lifetime: 0,
    opex: 0, total: 0, curtailment_cost: 0,
    storage_capex_expansion: 0, storage_capex_expansion_lifetime: 0,
    by_component: [], by_carrier: [], by_period: [],
  })
  // Full Generator shape (api/types.ts:33-55) — 29 fields, not just the 4
  // this test cares about. AggregatedOverview.tsx and shared.tsx read only
  // `name`, `carrier` (both preserved from the original fixture) and
  // `curtailment_cost` (via an `as unknown as` cast, AggregatedOverview.tsx:150,378)
  // off a Generator row; every other field below is an inert PyPSA-default /
  // "no constraint" value that nothing in the render path consumes, so
  // filling them in to satisfy the interface cannot change any value this
  // test's assertion depends on. `curtailment_cost: 0` also matches the
  // previous fixture's behaviour exactly: the field was `undefined` before,
  // and `curtailmentCost`'s guard (`c && Number.isFinite(c) && c > 0`,
  // AggregatedOverview.tsx:151/379) was already short-circuiting it to
  // unused regardless.
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
  vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([])
})

function renderOverview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // weightCtx is normally assembled by the AggregatedResultsBody wrapper in
  // Results.tsx (:483-500), which is not exported and cannot be imported —
  // this fixture matches the exact shape that wrapper builds, with a single
  // snapshot at weight 1 so the KPI's weighted sum equals the raw mocked
  // dispatch value.
  // `name` is not a `SnapshotWeightRow` field. `_snapshotWeightRow`
  // (shared.tsx:181-195) treats a row with neither `snapshot` nor `timestep`
  // set as a positional match, so `name` is inert regardless — it's kept
  // here only because a real weightings row would carry the snapshot's ISO
  // under some key. Typed via intersection (the sanctioned form used at
  // CapacityExpansion.test.tsx:84 for `lineWithOptimalSize`) so the extra
  // field narrows this local fixture rather than widening `SnapshotWeightRow`
  // or being silenced with a forbidden `as never`.
  const snapshotWeightRow: SnapshotWeightRow & { name: string } = {
    name: '2026-01-01T00:00:00', objective: 1, generators: 1, stores: 1,
  }
  const weightCtx: WeightCtx = {
    snapshots: ['2026-01-01T00:00:00'],
    snapshotPeriods: undefined,
    snapshotWeights: [snapshotWeightRow],
    periodWeights: undefined,
  }
  return render(
    <QueryClientProvider client={client}>
      <AggregatedOverview weightCtx={weightCtx} />
    </QueryClientProvider>,
  )
}

it('renders a distinctive thermal generation value sourced from the mocked results API', async () => {
  renderOverview()
  // Traced against AggregatedOverview.tsx directly (not left to the
  // executor): `kpis.thermal` (:100-101) is
  // `weightedSum(gensTS, groupCols.thermal, weightCtx, fullRange, 'generators')`.
  // `groupCols.thermal` contains 'ThermalGen' because `generatorGroup('gas')`
  // (shared.tsx:24-26, backed by utils/carriers.ts `isRenewableCarrier`)
  // classifies carrier "gas" as Thermal, not Renewables. `fullRange` is the
  // single mocked row (AggregatedOverview.tsx:73-76, `{from: 0, to: 0}` for
  // a 1-row TS). The fixture's lone
  // `snapshotWeights` row has no `snapshot`/`timestep` key, so
  // `_snapshotWeightRow` (shared.tsx:181-195) takes its positional-match
  // branch and returns it; `generators: 1` makes the weight multiplier
  // exactly 1, and no `snapshotPeriods` entry exists to add a period-years
  // factor. So `kpis.thermal === 424.24` unweighted, and
  // `fmtEnergy(424.24, 2)` (shared.tsx:481-488) renders "424.24 MWh"
  // verbatim (424.24 is >= 1 and < 1000, so no unit rescale).
  //
  // `kpis.totalGen` (thermal + renewable, and renewable is 0 here since
  // there is no renewable generator in the fixture) evaluates to the SAME
  // 424.24 and the "Total generation" KPI card renders an IDENTICAL
  // "424.24 MWh" string. A document-wide text search on that substring
  // therefore matches two elements and `findByText` throws. Scope the
  // query to the "Thermal" KPI card specifically — each KPI is a `label`
  // div followed by a sibling `value` div inside one wrapping card div
  // (shared.tsx `KPI`, :653-692) — rather than searching the whole document.
  const thermalLabel = await screen.findByText('Thermal')
  const card = thermalLabel.parentElement as HTMLElement
  expect(within(card).getByText('424.24 MWh')).toBeTruthy()
})
