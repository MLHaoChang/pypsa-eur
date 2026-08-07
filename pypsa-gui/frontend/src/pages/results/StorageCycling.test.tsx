import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import StorageCycling from './StorageCycling'

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
// The fixture above uses `weightings: []`, so `wAt` only ever hits its `1`
// fallback and can't tell a correct lookup from a reverted-to-positional one.
// These two cases pin non-default weight values so a regression to
// `weight[row]` fails loudly instead of shipping silently. Both were run
// against a temporarily-reverted positional `wAt` and confirmed to FAIL —
// see task-4-report.md for the observed (wrong) numbers.

it('weights a flat, WINDOWED two-row slice by each row\'s OWN objective weight, not by position', async () => {
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
      { snapshot: '2026-01-01T00:00:00', objective: 10 },
      { snapshot: '2026-01-01T01:00:00', objective: 2 },
      { snapshot: '2026-01-01T02:00:00', objective: 3 },
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
  // `2027|2026-01-01T00:00:00` instead of array position. This is the exact
  // case `shared.tsx`'s own period-blind `find` fallback would also get
  // wrong (carried to a later fix wave per the reviewer) — `wAt` here is
  // stricter because it keys on `period|timestep`, not `timestep` alone.
  // Correct: cycles = |4|*5 / (2*10) = 20/20 = 1.00. Reverted-to-positional:
  // |4|*2 / (2*10) = 8/20 = 0.40.
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
      { period: 2026, timestep: '2026-01-01T00:00:00', objective: 2 },
      { period: 2027, timestep: '2026-01-01T00:00:00', objective: 5 },
    ] as unknown as Record<string, number>[],
    ts_start: null, ts_end: null, can_sample_weeks: false,
  })
  renderPage()
  const match = await screen.findByText('1.00')
  expect(match).toBeTruthy()
})
