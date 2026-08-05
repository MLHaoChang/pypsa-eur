import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi, simulationApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import type { Line } from '../../api/types'
import CapacityExpansion from './CapacityExpansion'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: { ...actual.resultsApi, getCostBreakdown: vi.fn(), getEconomicsByCarrier: vi.fn() },
    simulationApi: { ...actual.simulationApi, getAssetCosts: vi.fn() },
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
      getStores: vi.fn(),
      getLinks: vi.fn(),
      getLines: vi.fn(),
      getTransformers: vi.fn(),
      listVintageResults: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // Full CostBreakdown shape (api/simulation.ts:62-110). Checked directly
  // against CapacityExpansion.tsx: it reads `cost.by_component` only inside
  // `exportCostCSV` (:789), a click handler never invoked by this render-only
  // test, and reads `cost.by_period` only behind an `&&` guard (:813) — so
  // this file would NOT have crashed with the old `{ total: 0 }` stub
  // (unlike AggregatedOverview.tsx and Dispatch.tsx, which read the
  // equivalent fields unguarded in their JSX render path — see their own
  // test-file comments). Populated fully anyway for consistency with the
  // real contract, so a future edit that hoists one of these reads to the
  // render path doesn't reintroduce that crash silently.
  vi.mocked(resultsApi.getCostBreakdown).mockReset().mockResolvedValue({
    capex: 0, capex_lifetime: 0, capex_expansion: 0, capex_expansion_lifetime: 0,
    opex: 0, total: 0, curtailment_cost: 0,
    storage_capex_expansion: 0, storage_capex_expansion_lifetime: 0,
    by_component: [], by_carrier: [], by_period: [],
  })
  // Real shape is `{ by_carrier?: Record<...> }` (api/simulation.ts:337-350);
  // CapacityExpansion.tsx reads it as `econByCarrier?.by_carrier` (:824), so
  // an empty object (rather than `[]`) is the accurate empty fixture.
  vi.mocked(resultsApi.getEconomicsByCarrier).mockReset().mockResolvedValue({})
  vi.mocked(simulationApi.getAssetCosts).mockReset().mockResolvedValue({})
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStorageUnits).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getStores).mockReset().mockResolvedValue([])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([])
  // s_nom_opt is a real column PyPSA's Line dataframe carries post-solve;
  // GET /api/network/lines returns every column, so this is a direct
  // passthrough of the mocked network response, not a computed aggregate.
  //
  // Deviation from the brief's literal fixture: the brief's object literal
  // only listed 9 of the 18 `Line` fields (api/types.ts:8-21) and added
  // `s_nom_opt`, which is not a `Line` field at all — CapacityExpansion.tsx
  // reads it through its own `attr()` helper (:46-48), precisely because the
  // TS type omits a few columns PyPSA's dataframe actually carries. As
  // written it would neither satisfy `Line[]` (missing fields) nor compile
  // without an excess-property error (`s_nom_opt`). Per task instructions,
  // missing required fields are filled with inert defaults that nothing in
  // `sizedLines` (CapacityExpansion.tsx:251-289) reads, and `s_nom_opt` is
  // added back via an intersection type annotation on this local — not a
  // cast — so the object literal is checked against `Line & { s_nom_opt:
  // number }` (which legitimately has that property) rather than bare
  // `Line`. The resulting array is structurally a subtype of `Line[]`, so it
  // is assignable to `mockResolvedValue`'s parameter without widening
  // `networkApi.getLines`'s or `CapacityExpansion.tsx`'s own types.
  const lineWithOptimalSize: Line & { s_nom_opt: number } = {
    name: 'Line 1', bus0: 'Bus 0', bus1: 'Bus 1', length: 10, r: 0, x: 0, b: 0,
    s_nom: 100, s_nom_extendable: true, s_nom_min: 0, s_nom_max: null,
    capital_cost: 1000, fom_cost: 0, overnight_cost: null, discount_rate: null,
    carrier: '', build_year: 2026, lifetime: null,
    s_nom_opt: 424.24,
  }
  vi.mocked(networkApi.getLines).mockReset().mockResolvedValue([lineWithOptimalSize])
  vi.mocked(networkApi.getTransformers).mockReset().mockResolvedValue([])
  // Real shape is `{ results: Record<class, Record<name, {...}>> }`
  // (api/network.ts:201-207), not a bare array — `{ results: {} }` is the
  // accurate empty fixture (see the assertion comment below for why the
  // exact shape doesn't change this test's outcome either way, but it's the
  // literal contract, not a stand-in).
  vi.mocked(networkApi.listVintageResults).mockReset().mockResolvedValue({ results: {} })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CapacityExpansion />
    </QueryClientProvider>,
  )
}

it('renders a distinctive optimal line sizing value sourced from the mocked network API', async () => {
  renderPage()
  // Traced against CapacityExpansion.tsx directly. `sizedLines` (:251-289)
  // builds one row per extendable line whose `s_nom_opt - s_nom > 1e-6`:
  // our fixture has `s_nom_extendable: true`, `s_nom_opt: 424.24`,
  // `s_nom: 100`, so `delta = 324.24 > 1e-6` and the row is kept.
  // `vintagesFor('Line', 'Line 1')` (:119-123) reads
  // `vintageResults.results['Line']?.['Line 1']?.periods` — absent in our
  // `{ results: {} }` fixture, so it returns `null` and the non-vintage
  // branch runs, setting `row.optimal = opt = 424.24` — a direct
  // passthrough of the mocked `s_nom_opt`, confirming the task's original
  // assumption (no listVintageResults fallback needed). `LinesTable`
  // (:1348-1381) renders the "Total" column as
  // `fmtPower(r.optimal, 1).replace('MW', 'MVA')` (:1373) —
  // `fmtPower(424.24, 1)` (shared.tsx:499-504) is `"424.2 MW"` (424.24 is
  // >= 1 and < 1000, `toFixed(1)` rounds to "424.2"), so the cell renders
  // literally "424.2 MVA". Checked against every other number this fixture
  // produces (Initial "100.0 MVA", Built "+324.2 MVA", CAPEX "€324.2 k",
  // "New investment" KPI "€324.24 k") — none contain the substring
  // "424.2", so the single Lines row is the only match.
  const match = await screen.findByText((text) => text.includes('424.2'))
  expect(match).toBeTruthy()
})
