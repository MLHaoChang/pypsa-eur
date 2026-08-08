import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
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
    // The backend emits this on every response. `true` is the ordinary case:
    // every component class's upfront cost resolved and the `*_lifetime`
    // figures above are real numbers rather than nulls.
    capex_lifetime_available: true,
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

// ── Upfront (PV) CAPEX unavailable ────────────────────────────────────────
// `cost_breakdown`'s `*_lifetime` fields are `number | null`. `null` means the
// backend could not resolve an upfront (overnight) cost for some component
// class — on the user's live network that was Line, 95.8% of system CAPEX,
// and this tab rendered `cost.capex_lifetime ?? 0` as a confident "€0.00".
// The tab must now say "unavailable" instead, and must NOT reuse the "—" it
// already prints for figures that genuinely do not apply (curtailment cost,
// storage charge cost).

/** The KPI card carrying `label` — `KPI` renders label and value as sibling
 *  divs, so the label's PARENT is the card root. */
function kpiCard(label: string): HTMLElement {
  const el = screen.getAllByText(label).find(e => e.tagName === 'DIV')
  if (!el?.parentElement) throw new Error(`no KPI card labelled "${label}"`)
  return el.parentElement
}

/** Switch the cost strip to the PV / lifetime basis. */
async function switchToLifetimeMode() {
  fireEvent.click(await screen.findByText('Total investment (PV)'))
}

/** Backend response with every lifetime figure unresolvable. */
function unavailablePayload() {
  return {
    capex: 8_067_640_124, capex_lifetime: null,
    capex_expansion: 1_000, capex_expansion_lifetime: null,
    opex: 500_000, total: 8_068_140_124, curtailment_cost: 0,
    storage_capex_expansion: 0, storage_capex_expansion_lifetime: null,
    capex_lifetime_available: false,
    by_component: [], by_carrier: [], by_period: [],
  }
}

it('shows the unavailable marker — not €0 — in the PV CAPEX KPIs', async () => {
  // Fails if: the KPIs go back to `cost.capex_lifetime ?? 0` /
  // `storage_capex_expansion_lifetime ?? 0`, which render "€0.00".
  // Verified by restoring the `?? 0` on `displayCapex`: this assertion is what
  // catches it.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue(unavailablePayload())
  renderPage()
  await switchToLifetimeMode()

  expect(within(kpiCard('CAPEX (installed, PV)')).getByText('unavailable')).toBeTruthy()
  expect(within(kpiCard('Total new investment (PV)')).getByText('unavailable')).toBeTruthy()
  expect(within(kpiCard('Storage CAPEX (new, PV)')).getByText('unavailable')).toBeTruthy()
  // …and the em-dash keeps its own, different meaning on the same strip.
  expect(within(kpiCard('Curtailment cost')).getByText('—')).toBeTruthy()
})

it('refuses to print a total system cost built on an unknown CAPEX', async () => {
  // Fails if: `displayCapex + displayOpex` is computed with a null CAPEX,
  // which is NaN in JS and renders as "€NaN" — or worse, if the null is
  // coalesced to 0 and the OPEX alone is published under a "Total system
  // cost" label. Verified by reverting the KPI to
  // `fmtCurrency(displayCapex + displayOpex)`.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue(unavailablePayload())
  renderPage()
  await switchToLifetimeMode()

  const card = kpiCard('Total system cost')
  expect(within(card).getByText('unavailable')).toBeTruthy()
  expect(within(card).queryByText(/NaN/)).toBeNull()
  expect(within(card).queryByText('€500.00 k')).toBeNull()   // the bare OPEX
})

it('explains the blank PV figures once, at the top of the tab', async () => {
  // Fails if: the banner is deleted, leaving the reader to infer from four
  // identical "unavailable" cells what happened. Verified by deleting it.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue(unavailablePayload())
  renderPage()
  await switchToLifetimeMode()

  expect(await screen.findByText(/upfront \(PV\) investment costs are unavailable/i))
    .toBeTruthy()
})

it('keeps the annualised figures, which do not depend on the failed resolve', async () => {
  // Fails if: the fix blanks the whole tab (an early return), or lets the
  // lifetime null leak into annualised mode. Annualised CAPEX comes from
  // n.statistics() and is unaffected by the upfront-cost resolve — hiding it
  // would throw away a working half of the tab, and the banner would be noise
  // in a mode where nothing on screen is affected.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue(unavailablePayload())
  renderPage()

  // Default mode is Annualised — no banner, and real numbers.
  await screen.findByText('CAPEX (installed)')
  expect(within(kpiCard('CAPEX (installed)')).getByText('€8.07 B')).toBeTruthy()
  expect(within(kpiCard('CAPEX (installed)')).queryByText('unavailable')).toBeNull()
  expect(screen.queryByText(/upfront \(PV\) investment costs are unavailable/i)).toBeNull()
})

it('renders real PV figures untouched when every class resolved', async () => {
  // Fails if: the unavailable path fires unconditionally, or keys off the
  // presence of the flag rather than its value. Without this, a fix that
  // reports EVERY run as unavailable passes every other test above.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue({
    ...unavailablePayload(),
    capex_lifetime: 36_000_000_000,
    capex_expansion_lifetime: 4_000,
    storage_capex_expansion_lifetime: 0,
    capex_lifetime_available: true,
  })
  renderPage()
  await switchToLifetimeMode()

  expect(within(kpiCard('CAPEX (installed, PV)')).getByText('€36.00 B')).toBeTruthy()
  expect(screen.queryByText('unavailable')).toBeNull()
  expect(screen.queryByText(/upfront \(PV\) investment costs are unavailable/i)).toBeNull()
})
