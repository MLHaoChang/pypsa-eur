import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import Economics from './Economics'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getAssetEconomics: vi.fn(),
      getLcoh: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // The original hedge here was justified: `getAssetEconomics()` does NOT
  // return a flat array of `AggregatedAssetRow`-shaped objects. Its real
  // contract (api/simulation.ts:403-405, `AssetEconomicsPayload` :488-495)
  // is `{ currency, is_multi_period, periods, generators: GeneratorEconomicsRow[],
  // storage_units: StorageUnitEconomicsRow[], stores: StoreEconomicsRow[],
  // links: LinkEconomicsRow[] }`.
  // Economics.tsx's `everyRow` (:294-301) reads `payload.generators.map(...)`
  // — with an array-shaped payload (the original fixture), `payload.generators`
  // is `undefined` and `.map` throws `TypeError` during render. The mocked
  // value below is `GeneratorEconomicsRow`-shaped (api/simulation.ts:409-434),
  // matching what `makeGenRow` (Economics.tsx:76-109) actually consumes.
  vi.mocked(resultsApi.getAssetEconomics).mockReset().mockResolvedValue({
    currency: 'EUR',
    is_multi_period: false,
    periods: [],
    generators: [
      {
        name: 'ThermalGen', bus: 'Bus 0', carrier: 'gas',
        p_nom_opt_mw: 100, energy_mwh: 424.24, capacity_factor: 0.5,
        revenue_eur: 1000, vom_cost_eur: 100, fixed_cost_eur: 200,
        fom_cost_eur: 0, net_profit_eur: 700, lcoe_eur_per_mwh: 50,
        avg_price_eur_per_mwh: null, by_period: [],
      },
    ],
    storage_units: [],
    stores: [],
    links: [],
  })
  // Real shape is `{ rows: [...], total: null | {...}, currency }`
  // (api/simulation.ts:173-197). Economics.tsx's `LcohSection` (:995) reads
  // `lcoh.rows.length` unguarded once `lcoh` itself is truthy — `{}` (the
  // original fixture) is truthy with `rows` `undefined`, so
  // `undefined.length` throws `TypeError` during render. `rows: []` makes
  // the section's own early return (`if (!lcoh || lcoh.rows.length === 0)
  // return null`) fire cleanly, contributing no extra text to the page.
  vi.mocked(resultsApi.getLcoh).mockReset().mockResolvedValue({ rows: [], total: null, currency: 'EUR' })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Economics />
    </QueryClientProvider>,
  )
}

it('renders a distinctive group energy total sourced from the mocked asset economics API', async () => {
  renderPage()
  // Traced against Economics.tsx directly. `makeGenRow` (:76-109) maps our
  // one generator into an `AggregatedAssetRow` with `group: 'Thermal'`
  // (`isRenewableCarrier('gas')` is false) and `energy_mwh: 424.24` passed
  // through unchanged. `groupedRows.Renewables`/`.Storage` are both empty,
  // so the group-table loop (:722, iterating `['Renewables', 'Thermal',
  // 'Storage']`) returns `null` for those two (`if (rows.length === 0)
  // return null`, :725) and renders exactly one `GroupSection` row: Thermal.
  // Its header cell renders `{groupKey}` literally as "Thermal" (:849), and
  // `sumGroup` (:178-245) with a single row sums `energy += r.energy_mwh`
  // to 424.24, rendered via `fmtEnergy(total.energy_mwh)` (:855) as
  // "424.24 MWh" in the SAME `<tr>`.
  //
  // The KPI strip's "Energy delivered" card ALSO renders "424.24 MWh"
  // (`kpis.energy`, :406-413, sums the same single row) — a second,
  // identically-formatted match elsewhere on the page. Since "Thermal"
  // appears exactly once in this component's whole output (confirmed by
  // reading the file — Renewables/Storage rows are skipped, and no filter
  // chip or chart label reuses the bare word "Thermal"), scope the energy
  // assertion to the group row specifically rather than searching the
  // whole document.
  const thermalCell = await screen.findByText('Thermal')
  const row = thermalCell.closest('tr') as HTMLElement
  expect(within(row).getByText('424.24 MWh')).toBeTruthy()
})
