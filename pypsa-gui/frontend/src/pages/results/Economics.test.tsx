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
    // The backend emits this on every response. `true` is the ordinary case:
    // the capital-cost resolver ran and the cost fields below are real.
    capital_costs_available: true,
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

// ── Capital costs unavailable ─────────────────────────────────────────────
// When `periodized_capital_costs` raises, the backend used to leave every
// asset's capital cost at 0.0, and this tab formatted that as "€0.00" beside
// real revenue — a number the reader has no way to distinguish from a genuine
// free asset. The backend now sends `capital_costs_available: false` plus
// `null` on every capital-cost-derived field; the tab must say so rather than
// print anything.
//
// `—` is NOT an acceptable rendering here and these tests reject it: this tab
// already uses `—` for "not applicable" (a generator has no charge cost, a
// storage unit has no capacity factor), so reusing it would fold "could not be
// computed" into "does not apply".

/**
 * The KPI card carrying `label`.
 *
 * "Net profit" appears twice on the page — as a KPI label (a `<div>`) and as a
 * table column header (a `<th>`) — so a bare `getByText` throws on multiple
 * matches. Filtering to the DIV picks the card's label, whose PARENT is the
 * card root (`KPI` renders label and value as sibling divs, so `.closest('div')`
 * would return the label itself and scope the search to the wrong element).
 */
function kpiCard(label: string): HTMLElement {
  const el = screen.getAllByText(label).find(e => e.tagName === 'DIV')
  if (!el?.parentElement) throw new Error(`no KPI card labelled "${label}"`)
  return el.parentElement
}

/** The failure payload: flag false, every capital-derived field null. */
function unavailablePayload() {
  return {
    currency: 'EUR',
    is_multi_period: false,
    capital_costs_available: false,
    periods: [],
    generators: [
      {
        name: 'ThermalGen', bus: 'Bus 0', carrier: 'gas',
        p_nom_opt_mw: 100, energy_mwh: 424.24, capacity_factor: 0.5,
        // Dispatch-derived — unaffected by the resolver, still real.
        revenue_eur: 1000, vom_cost_eur: 100,
        // Capital-derived — null on the wire.
        fixed_cost_eur: null, fom_cost_eur: null, net_profit_eur: null,
        lcoe_eur_per_mwh: null,
        avg_price_eur_per_mwh: null, by_period: [],
      },
    ],
    storage_units: [],
    stores: [],
    links: [],
  }
}

it('announces that capital costs are unavailable instead of rendering them', async () => {
  // Fails if: Economics.tsx ignores `capital_costs_available` / the nulls and
  // falls back to formatting them (`fmtCurrency(null)` → `—`, or a coalesce to
  // 0 → `€0.00`). Verified by deleting the banner: this assertion is the only
  // thing that catches its absence.
  vi.mocked(resultsApi.getAssetEconomics).mockResolvedValue(unavailablePayload())
  renderPage()

  expect(await screen.findByText(/capital costs are unavailable/i)).toBeTruthy()
})

it('shows the unavailable marker — not €0 and not a dash — in the capital-cost cells', async () => {
  // Fails if: the null fixed cost / net profit / LCOE render through
  // `fmtCurrency` (giving `—`) or through a `?? 0` coalesce (giving `€0.00`).
  // Verified by reverting the `Fixed` cell to `{fmtCurrency(total.fixed_cost_eur)}`
  // — the `—` assertion below then fires.
  vi.mocked(resultsApi.getAssetEconomics).mockResolvedValue(unavailablePayload())
  renderPage()

  const row = (await screen.findByText('Thermal')).closest('tr') as HTMLElement
  const cells = within(row).getAllByRole('cell')
  // Column order (Economics.tsx table head): 0 Asset · 1 Carrier · 2 Capacity ·
  // 3 Cap. factor · 4 Energy · 5 Revenue · 6 Charge cost · 7 VOM · 8 Fixed ·
  // 9 Net profit · 10 LCOE/LCOS · 11 Spread.
  expect(cells[8].textContent).toBe('unavailable')   // Fixed
  expect(cells[9].textContent).toBe('unavailable')   // Net profit
  expect(cells[10].textContent).toBe('unavailable')  // LCOE/LCOS
  // And the em-dash keeps its own, different meaning: cap. factor and charge
  // cost genuinely do not apply to a thermal group total. If "unavailable"
  // were rendered as `—` these two would be indistinguishable from the three
  // above, which is precisely the collapse this decision rejects.
  expect(cells[3].textContent).toBe('—')             // Cap. factor
  expect(cells[6].textContent).toBe('—')             // Charge cost
  expect(within(row).queryByText('€0.00')).toBeNull()
})

it('keeps the figures that do not depend on capital cost', async () => {
  // Fails if: the fix blanks the whole tab (an early return, or routing every
  // number through the unavailable helper). Revenue, energy and VOM are
  // computed from dispatch and bus prices — the resolver's failure says
  // nothing about them, and hiding them would throw away a working half of
  // the tab. Verified by making the banner an early `return`.
  vi.mocked(resultsApi.getAssetEconomics).mockResolvedValue(unavailablePayload())
  renderPage()

  const row = (await screen.findByText('Thermal')).closest('tr') as HTMLElement
  expect(within(row).getByText('424.24 MWh')).toBeTruthy()   // energy
  expect(within(row).getByText('€1.00 k')).toBeTruthy()       // revenue
  expect(within(row).getByText('€100.00')).toBeTruthy()       // VOM
})

it('blanks the capital-cost KPIs rather than summing nulls to zero', async () => {
  // Fails if: `kpis` accumulates the nulls as 0 (`fixed += r.fixed_cost_eur`
  // with a `?? 0`), which prints a confident "€0.00" portfolio CAPEX and a net
  // profit inflated by the entire missing CAPEX — the headline version of the
  // same defect. Verified by restoring `fixed += r.fixed_cost_eur ?? 0`.
  vi.mocked(resultsApi.getAssetEconomics).mockResolvedValue(unavailablePayload())
  renderPage()

  await screen.findByText('Thermal')  // wait for the query to resolve

  expect(within(kpiCard('Fixed cost')).getByText('unavailable')).toBeTruthy()
  expect(within(kpiCard('Net profit')).getByText('unavailable')).toBeTruthy()
})

it('renders real capital-cost figures untouched when the flag is true', async () => {
  // Fails if: the unavailable path fires unconditionally, or keys off the
  // presence of the flag rather than its value. Without this, a fix that
  // reports EVERY run as unavailable passes every other test above. Uses the
  // default (healthy) mock from `beforeEach`.
  renderPage()

  const row = (await screen.findByText('Thermal')).closest('tr') as HTMLElement
  expect(within(row).getByText('€200.00')).toBeTruthy()      // fixed cost
  expect(within(row).getByText('€700.00')).toBeTruthy()      // net profit
  // Group LCOE is `sumGroup`'s (fixed + vom + charge) / energy —
  // (200 + 100 + 0) / 424.24 — not the row's own 50, which is the asset-level
  // figure. Asserting the group's own arithmetic keeps this honest about
  // which number is on screen.
  expect(within(row).getByText('0.71 €/MWh')).toBeTruthy()
  expect(within(row).queryByText('unavailable')).toBeNull()
  expect(screen.queryByText(/capital costs are unavailable/i)).toBeNull()
})
