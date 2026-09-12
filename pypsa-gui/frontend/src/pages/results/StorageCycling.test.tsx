import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import StorageCycling from './StorageCycling'
import { buildMultiPeriodWindowFixture, COLUMNS, WINDOW } from './__fixtures__/multiPeriodWindow'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getStorageDispatchResults: vi.fn() } }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getStorageUnits: vi.fn(),
      getSnapshots: vi.fn(),
      listVintageResults: vi.fn(),
      // StorageCycling now sources its weight context via `useWeightCtx`
      // (shared.tsx), which fetches /network/investment_periods alongside
      // /network/snapshots — mock both or the unmocked real one 404s in jsdom.
      getInvestmentPeriods: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // pNom is rendered as u.pNom.toFixed(1) — a direct passthrough of the
  // mocked network row's own p_nom field, no aggregation involved.
  vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'], columns: ['Battery1'], data: [[10]],
  })
  // Full StorageUnit shape (api/types.ts:56-70) — 21 fields, not just the 4
  // the brief supplied. StorageCycling.tsx's `meta` builder (:114-121) only
  // ever reads `name`, `carrier`, `p_nom_opt` (absent here, so it falls back
  // to `p_nom`) and `max_hours` off each row. Every other field below is an
  // inert PyPSA-default / "no constraint" value (mirrors Curtailment.test.tsx
  // and Dispatch.test.tsx's fixtures, which traced sibling interfaces the
  // same way) that nothing in this test's render path consumes.
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([
    {
      name: 'Battery1', bus: 'Bus 0', carrier: 'battery', p_nom: 424.24,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      max_hours: 4, efficiency_store: 1, efficiency_dispatch: 1,
      standing_loss: 0, cyclic_state_of_charge: false, state_of_charge_initial: 0,
      inflow: 0,
      capital_cost: 0, marginal_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null,
      build_year: 0, lifetime: null,
    },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 1, snapshots: ['2026-01-01T00:00:00'], weightings: [], ts_start: null, ts_end: null,
    can_sample_weeks: false,
  })
  // listVintageResults resolves an object keyed by component class
  // (api/network.ts:201-207), not the bare array the brief supplied — `[]`
  // would fail to typecheck against `{ results: Record<...> }`. An empty
  // `results` map is the real "no vintage expansion has run" shape and is
  // inert here regardless: with a single flat snapshot (no `periods` on
  // either the dispatch TS or getSnapshots), StorageCycling.tsx's `p` is
  // always `null` (:140-144), so `capForPeriod` (:165-166) short-circuits on
  // `p == null` before ever touching `vintageResults`.
  vi.mocked(networkApi.listVintageResults).mockReset().mockResolvedValue({ results: {} })
  vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({ periods: [], weightings: [] })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <StorageCycling />
    </QueryClientProvider>,
  )
}

it('renders a distinctive rated power directly from the mocked storage-unit row', async () => {
  renderPage()
  const match = await screen.findByText('424.2')
  expect(match).toBeTruthy()
})

// ── Regression guards for the ISO/period-keyed weight lookup ────────────────
// `StorageCycling.tsx`'s `wAt` used to index `snap.weightings` POSITIONALLY
// (`weight[row]`), which only happened to be correct while `storPowerTS`
// always covered the full horizon starting at absolute row 0. Task 4 windows
// that fetch, so `row` became window-relative while `weightings` stayed
// absolute — a silent misalignment whenever the window didn't start at row 0.
// `wAt` itself is gone now — the component reads the shared `snapshotWeightAt`
// (shared.tsx), the SAME keyed (period|timestep) lookup `effectiveWeightAt`
// uses. These two cases still pin non-default weight values so a regression
// to a positional read fails loudly instead of shipping silently.
//
// Column changed from `objective` to `generators`: storage throughput is an
// ENERGY (MWh) quantity, and the ENERGY basis is `snapshot_weightings.
// generators` (see `routers/results.py::get_asset_economics`'s `w_vals_energy`
// convention, and the storage accumulation at results.py:3634-3635, which
// this file must agree with). Weighting by `objective` was the defect this
// suite's basis test (below) exists to catch. The asserted cycle counts
// (1.25 / 1.00) are UNCHANGED — these two tests exist to pin window/period
// keying, not basis, so the same weight VALUES were moved to the correct
// column rather than picking new ones.

it('weights a flat, WINDOWED two-row slice by each row\'s OWN generators weight, not by position', async () => {
  // Simulates a windowed fetch that starts mid-horizon: the full horizon has
  // THREE snapshots (weights 10, 2, 3), but `storPowerTS` here only covers
  // the LAST two (as if the window started at absolute row 1). A positional
  // `weight[row]` lookup reads `weightings[0]`/`weightings[1]` (10 and 2 —
  // the wrong rows) for this slice's row 0/row 1; the ISO-keyed lookup reads
  // `weightings[1]`/`weightings[2]` (2 and 3 — the right rows) because it
  // matches on each row's own timestamp instead of its position in the
  // slice. p_nom=10, max_hours=1 gives energyCap=10, so correct cycles =
  // (|5|*2 + |5|*3) / (2*10) = 25/20 = 1.25. Reverted-to-positional would
  // read (|5|*10 + |5|*2) / 20 = 60/20 = 3.00 — verified below.
  vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T01:00:00', '2026-01-01T02:00:00'],
    columns: ['WeightBattery'],
    data: [[5], [5]],
  })
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([
    {
      name: 'WeightBattery', bus: 'Bus 0', carrier: 'battery', p_nom: 10,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      max_hours: 1, efficiency_store: 1, efficiency_dispatch: 1,
      standing_loss: 0, cyclic_state_of_charge: false, state_of_charge_initial: 0,
      inflow: 0,
      capital_cost: 0, marginal_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null,
      build_year: 0, lifetime: null,
    },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 3,
    // Full, unwindowed horizon — three snapshots, so `storPowerTS` above
    // (rows 1 and 2 only) really is a proper subset, not the whole thing.
    snapshots: ['2026-01-01T00:00:00', '2026-01-01T01:00:00', '2026-01-01T02:00:00'],
    weightings: [
      { snapshot: '2026-01-01T00:00:00', generators: 10 },
      { snapshot: '2026-01-01T01:00:00', generators: 2 },
      { snapshot: '2026-01-01T02:00:00', generators: 3 },
    ] as unknown as Record<string, number>[],
    ts_start: null, ts_end: null, can_sample_weeks: false,
  })
  renderPage()
  const match = await screen.findByText('1.25')
  expect(match).toBeTruthy()
})

it('disambiguates identical timesteps under different periods by a period|timestep key, not position', async () => {
  // Simulates a WINDOWED fetch that starts mid-horizon: `storPowerTS` here
  // is a single row for investment period 2027, but `snap.weightings` (the
  // full, unwindowed horizon from getSnapshots) lists period 2026 FIRST and
  // 2027 SECOND — both sharing the same operational timestep (the normal
  // multi-period replication PyPSA-Eur uses). A positional `weight[row]`
  // lookup reads `weightings[0]` (period 2026's weight, 2) for this row
  // because it's row 0 of the fetched slice; the period-keyed lookup reads
  // `weightings[1]` (period 2027's weight, 5) because it matches on
  // `2027|2026-01-01T00:00:00` instead of array position. `snapshotWeightAt`
  // (shared.tsx) is stricter than a bare `find` because it keys on
  // `period|timestep`, not `timestep` alone. Correct: cycles = |4|*5 /
  // (2*10) = 20/20 = 1.00. Reverted-to-positional: |4|*2 / (2*10) = 8/20 =
  // 0.40.
  vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
    index: ['2026-01-01T00:00:00'],
    columns: ['MultiBattery'],
    data: [[4]],
    periods: [2027],
  })
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([
    {
      name: 'MultiBattery', bus: 'Bus 0', carrier: 'battery', p_nom: 10,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      max_hours: 1, efficiency_store: 1, efficiency_dispatch: 1,
      standing_loss: 0, cyclic_state_of_charge: false, state_of_charge_initial: 0,
      inflow: 0,
      capital_cost: 0, marginal_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null,
      build_year: 0, lifetime: null,
    },
  ])
  vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
    count: 2,
    // Full, unwindowed horizon: both periods present, period 2026 first.
    snapshots: ['2026-01-01T00:00:00', '2026-01-01T00:00:00'],
    periods: [2026, 2027],
    weightings: [
      { period: 2026, timestep: '2026-01-01T00:00:00', generators: 2 },
      { period: 2027, timestep: '2026-01-01T00:00:00', generators: 5 },
    ] as unknown as Record<string, number>[],
    ts_start: null, ts_end: null, can_sample_weeks: false,
  })
  renderPage()
  const match = await screen.findByText('1.00')
  expect(match).toBeTruthy()
})

// ── Basis + no-years + window regression, via the shared multiPeriodWindow
// fixture (added in cb2e53a4, extended in 79ad5c86) ─────────────────────────
// `results.py::get_asset_economics` (backend, :3308-3310) draws a hard line:
// ENERGY quantities (MWh) weight by `snapshot_weightings.generators`; COST
// quantities (€) weight by `.objective`. Storage discharge/charge MWh is
// accumulated on `w_vals_energy` (generators) there (results.py:3634-3635).
// `StorageCycling.tsx` used to weight throughput — an energy quantity — by
// `.objective`, disagreeing with that convention whenever `objective !=
// generators` (any time-aggregated / sampled-weeks run). These three tests
// reuse the fixture's own non-uniform, per-row-divergent weight columns
// instead of hand-rolled numbers, and derive every expected value by
// independently summing over the fixture's data — never by calling the
// production code under test.
describe('StorageCycling: generators-basis + no-years + window-vs-whole-horizon', () => {
  const fx = buildMultiPeriodWindowFixture()
  const ASSET = COLUMNS[0] // 'asset_0'

  function storageUnitRow(name: string) {
    return {
      name, bus: 'Bus 0', carrier: 'battery', p_nom: 1,
      p_nom_extendable: false, p_nom_min: 0, p_nom_max: null,
      max_hours: 1, efficiency_store: 1, efficiency_dispatch: 1,
      standing_loss: 0, cyclic_state_of_charge: false, state_of_charge_initial: 0,
      inflow: 0,
      capital_cost: 0, marginal_cost: 0, fom_cost: 0,
      overnight_cost: null, discount_rate: null,
      build_year: 0, lifetime: null,
    }
  }

  // Full, unwindowed horizon exactly as `/network/snapshots` serves it — every
  // test below points `getSnapshots` at this so the component must resolve
  // each row's weight by (period, timestep), never by array position.
  function mockFullHorizonSnapshots() {
    vi.mocked(networkApi.getSnapshots).mockReset().mockResolvedValue({
      count: fx.whole.index.length,
      snapshots: fx.whole.index,
      periods: fx.whole.periods,
      weightings: fx.wholeWeights as unknown as Record<string, number>[],
      ts_start: null, ts_end: null, can_sample_weeks: false,
    })
    vi.mocked(networkApi.getInvestmentPeriods).mockReset().mockResolvedValue({
      periods: fx.periodWeights.map(p => Number(p.period)),
      weightings: fx.periodWeights as unknown as Array<Record<string, number | string>>,
    })
  }

  it('TEST 1 (basis, load-bearing): throughput matches the generators-weighted reference, NOT the objective-weighted sum', async () => {
    // Two rows from period 2026 (g=1, g=2 — g=0 has generators===objective by
    // fixture-formula coincidence, so it wouldn't discriminate the two bases).
    const g1 = 1, g2 = 2
    mockFullHorizonSnapshots()
    vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
      index: [fx.whole.index[g1], fx.whole.index[g2]],
      columns: [ASSET],
      data: [[fx.whole.data[g1][0]], [fx.whole.data[g2][0]]],
      periods: [fx.whole.periods![g1], fx.whole.periods![g2]],
    })
    vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([storageUnitRow(ASSET)])

    const generatorsSum = [g1, g2].reduce(
      (acc, g) => acc + Math.abs(fx.whole.data[g][0]) * (fx.wholeWeights[g].generators ?? 1), 0,
    )
    const objectiveSum = [g1, g2].reduce(
      (acc, g) => acc + Math.abs(fx.whole.data[g][0]) * (fx.wholeWeights[g].objective ?? 1), 0,
    )
    // Fixture sanity: the two bases must actually diverge here, or this test
    // can't discriminate a basis regression.
    expect(generatorsSum).not.toBeCloseTo(objectiveSum, 0)

    renderPage()
    const match = await screen.findByText(generatorsSum.toFixed(0))
    expect(match).toBeTruthy()
    expect(screen.queryByText(objectiveSum.toFixed(0))).toBeNull()
  })

  it('TEST 2 (no years): period 2028 (years=3) throughput is NOT scaled by periodWeights.years', async () => {
    // Two rows from period 2028 — the WINDOWed period, years=3 in the fixture.
    const g1 = 2 * 8760 + 0 // period 2028, t=0
    const g2 = 2 * 8760 + 1 // period 2028, t=1
    mockFullHorizonSnapshots()
    vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
      index: [fx.whole.index[g1], fx.whole.index[g2]],
      columns: [ASSET],
      data: [[fx.whole.data[g1][0]], [fx.whole.data[g2][0]]],
      periods: [fx.whole.periods![g1], fx.whole.periods![g2]],
    })
    vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([storageUnitRow(ASSET)])

    const noYears = [g1, g2].reduce(
      (acc, g) => acc + Math.abs(fx.whole.data[g][0]) * (fx.wholeWeights[g].generators ?? 1), 0,
    )
    const withYearsX3 = noYears * 3 // fx.periodWeights[2028].years === 3
    expect(noYears).not.toBeCloseTo(withYearsX3, 0)

    renderPage()
    const match = await screen.findByText(noYears.toFixed(0))
    expect(match).toBeTruthy()
    expect(screen.queryByText(withYearsX3.toFixed(0))).toBeNull()
  })

  it('TEST 3 (window basis): a WINDOWED payload (rows 17520-26279 re-based to 0) matches the whole horizon sliced to the same rows', async () => {
    mockFullHorizonSnapshots()
    // `storPowerTS` is the WINDOW-RELATIVE payload a real ?from=17520&to=26279
    // fetch returns — row 0 here is absolute row 17520, while `getSnapshots`
    // above still serves the FULL 26,280-row horizon (unwindowed, as
    // `/api/network/snapshots` always is). A positional `weight[row]` read
    // would pull period-2026 weights (rows 0..8759) for this payload instead
    // of period-2028's (rows 17520..26279) — the exact defect class
    // cb2e53a4/79ad5c86 fixed elsewhere in the Results tabs.
    vi.mocked(resultsApi.getStorageDispatchResults).mockReset().mockResolvedValue({
      index: fx.windowed.index,
      columns: [ASSET],
      data: fx.windowed.data.map(row => [row[0]]),
      periods: fx.windowed.periods,
      range: fx.windowed.range,
    })
    vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([storageUnitRow(ASSET)])

    // Reference: the WHOLE horizon's own data + weights, sliced to the exact
    // same absolute rows the window covers (17520..26279) — independent of
    // anything the component computes.
    let expected = 0
    for (let g = WINDOW.from; g <= WINDOW.to; g++) {
      expected += Math.abs(fx.whole.data[g][0]) * (fx.wholeWeights[g].generators ?? 1)
    }
    expect(expected).toBeGreaterThan(0)

    renderPage()
    const match = await screen.findByText(expected.toFixed(0))
    expect(match).toBeTruthy()
  })
})
