import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi, simulationApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import type { Line, StorageUnit } from '../../api/types'
import CapacityExpansion from './CapacityExpansion'
import { getKpiValueWithin } from '../../test/kpi'
import * as shared from './shared'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: { ...actual.resultsApi, getCostBreakdown: vi.fn(), getEconomicsByCarrier: vi.fn() },
    simulationApi: { ...actual.simulationApi, getAssetCosts: vi.fn(), getStatus: vi.fn() },
  }
})

// The tab reads the cost breakdown and economics as one bound view (A5.2 S3b);
// it carries what the two mocked routes return.
vi.mock('../../api/displayedResults', async () => {
  const { resultsApi } = await import('../../api/simulation')
  return {
    displayedResultsApi: {
      capex: () => Promise.all([resultsApi.getCostBreakdown(), resultsApi.getEconomicsByCarrier()])
        .then(([cost, economics]) => cost && {
          status: 'ok', data: { cost_breakdown: cost, economics_by_carrier: economics }, binding: { version: 1, project_id: 'p', scope: 'current', view: 'capex', data_sha256: 'a'.repeat(64) },
        }),
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
  vi.mocked(simulationApi.getStatus).mockReset().mockResolvedValue(SOLVED)
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
/** A solve the page can read (`GET /simulation/status`). */
const SOLVED = { running: false, status: 'completed', condition: 'optimal', objective: 1, solve_time: 1, dispatch: 'fresh' as const }

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
  // Storage CAPEX can only be "unavailable" when there is storage that could
  // be built; with none the tile says Not applicable (UX overhaul S2).
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([extendableBattery])
  renderPage()
  await switchToLifetimeMode()

  expect(within(kpiCard('CAPEX (installed, PV)')).getByText('unavailable')).toBeTruthy()
  expect(within(kpiCard('Total new investment (PV)')).getByText('unavailable')).toBeTruthy()
  expect(within(kpiCard('Storage CAPEX (new, PV)')).getByText('unavailable')).toBeTruthy()
  // …and a curtailment cost nobody configured says so, distinct from unavailable.
  expect(within(kpiCard('Curtailment cost')).getByText('Not modelled')).toBeTruthy()
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
  expect(getKpiValueWithin(kpiCard('CAPEX (installed)'), '€8.07 B')).toBeTruthy()
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

  expect(getKpiValueWithin(kpiCard('CAPEX (installed, PV)'), '€36.0 B')).toBeTruthy()
  expect(screen.queryByText('unavailable')).toBeNull()
  expect(screen.queryByText(/upfront \(PV\) investment costs are unavailable/i)).toBeNull()
})

// ── Per-asset overnight-cost unavailable (Investments-by-asset table) ─────
// A SEPARATE consumer from the KPI strip above: `assetCosts` (GET
// /api/simulation/asset_costs, `AssetCostMap`) feeds `costPerUnit`
// (CapacityExpansion.tsx), which builds the per-asset "CAPEX" column on the
// Lines/Generators/etc tables below the KPIs. The trap this guards: falling
// through to `raw` (the row's own `capital_cost` field) whenever the backend
// per-asset value looks unusable — `raw` is exactly 0 (or whatever the
// network's raw column holds) for an asset priced through `overnight_cost`
// alone, so an unresolved upfront cost used to render a confident currency
// figure instead of a missing-data marker, silently reintroducing the exact
// "€0" bug the backend fix exists to close.

it('shows the unavailable marker — not a raw fallback number — for an asset whose upfront cost failed to resolve', async () => {
  // Fails if: `costPerUnit`'s lifetime branch drops the
  // `overnight_cost_available === false` check and falls through to
  // `v != null && Number.isFinite(v) && v > 0 ? v : raw` unconditionally —
  // `m.overnight_cost_pv` is `null` here, so that expression evaluates to
  // `raw` (777), and the CAPEX cell would print a real currency figure
  // (777 x 324.24 delta ≈ "€251.9 k") instead of "unavailable".
  const unresolvedLine: Line & { s_nom_opt: number } = {
    name: 'Line 1', bus0: 'Bus 0', bus1: 'Bus 1', length: 10, r: 0, x: 0, b: 0,
    s_nom: 100, s_nom_extendable: true, s_nom_min: 0, s_nom_max: null,
    // A distinctive nonzero `raw` so a reverted `costPerUnit` prints a
    // distinctive, greppable wrong number rather than a `€0.00` that could
    // be confused with the top KPI strip's own (legitimate) zero.
    capital_cost: 777, fom_cost: 0, overnight_cost: null, discount_rate: null,
    carrier: '', build_year: 2026, lifetime: null,
    s_nom_opt: 424.24,
  }
  vi.mocked(networkApi.getLines).mockReset().mockResolvedValue([unresolvedLine])
  vi.mocked(simulationApi.getAssetCosts).mockReset().mockResolvedValue({
    lines: {
      'Line 1': {
        // Annualised resolves fine — independent of the upfront resolve.
        capital_cost: 50_000,
        fom_cost: 0,
        fixed_cost: 50_000,
        overnight_cost: null,
        overnight_cost_pv: null,
        overnight_cost_available: false,
        lifetime: 40,
      },
    },
  })

  renderPage()
  await switchToLifetimeMode()

  const row = (await screen.findByText('Line 1')).closest('tr')
  if (!row) throw new Error('Lines table row not found')
  // The CAPEX column is the last cell — read it directly rather than
  // substring-searching the whole row, which also contains an (unrelated)
  // "324.2 MVA" Built/Total cell that could otherwise collide with a numeric
  // substring check.
  const capexCell = row.querySelectorAll('td')[row.querySelectorAll('td').length - 1]
  expect(capexCell.textContent).toBe('unavailable')
})

it('still derives a real per-asset PV figure when the upfront cost resolved', async () => {
  // Fails if: the unavailable path fires unconditionally regardless of
  // `overnight_cost_available`, which would make the derivable-case test
  // above pass for the wrong reason (every asset "unavailable").
  const resolvedLine: Line & { s_nom_opt: number } = {
    name: 'Line 1', bus0: 'Bus 0', bus1: 'Bus 1', length: 10, r: 0, x: 0, b: 0,
    s_nom: 100, s_nom_extendable: true, s_nom_min: 0, s_nom_max: null,
    capital_cost: 0, fom_cost: 0, overnight_cost: null, discount_rate: null,
    carrier: '', build_year: 2026, lifetime: null,
    s_nom_opt: 424.24,
  }
  vi.mocked(networkApi.getLines).mockReset().mockResolvedValue([resolvedLine])
  vi.mocked(simulationApi.getAssetCosts).mockReset().mockResolvedValue({
    lines: {
      'Line 1': {
        capital_cost: 50_000,
        fom_cost: 0,
        fixed_cost: 50_000,
        overnight_cost: 1_000,
        overnight_cost_pv: 1_000,
        overnight_cost_available: true,
        lifetime: 40,
      },
    },
  })

  renderPage()
  await switchToLifetimeMode()

  const row = (await screen.findByText('Line 1')).closest('tr')
  if (!row) throw new Error('Lines table row not found')
  // capex = overnight_cost_pv (1_000) x delta (324.24) = 324_240 -> "€324.2 k"
  const capexCell = row.querySelectorAll('td')[row.querySelectorAll('td').length - 1]
  expect(capexCell.textContent).toBe('€324.2 k')
})

// ── Per-unit CAPEX heading names its basis (S0 follow-up, F1) ───────────────
// In "Annualised" mode the per-unit column is the asset's annual fixed cost
// (annuitised investment plus FOM), so it reads per year. In "Total
// investment (PV)" mode it is the upfront overnight cost, which is not per
// year. Both the expansion table and the retirement table carry it.
function generatorRows() {
  return [
    // Built: p_nom_opt above p_nom.
    { name: 'gas_new', bus: 'B1', carrier: 'gas', p_nom: 0, p_nom_opt: 50, p_nom_extendable: true,
      capital_cost: 1000, build_year: 2026 },
    // Retired: p_nom_opt below p_nom.
    { name: 'coal_old', bus: 'B1', carrier: 'coal', p_nom: 80, p_nom_opt: 20, p_nom_extendable: true,
      capital_cost: 900, build_year: 2000 },
  ] as never
}

it('heads the per-unit CAPEX per year in annualised mode, on built and retired generators', async () => {
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue(generatorRows())
  renderPage()
  await screen.findByText('gas_new')
  await screen.findByText('coal_old')
  expect(screen.getAllByText('CAPEX €/MW/yr')).toHaveLength(2)
  expect(screen.queryByText('CAPEX €/MW')).toBeNull()
})

it('heads the per-unit CAPEX without /yr in total-investment (PV) mode', async () => {
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue(generatorRows())
  renderPage()
  await screen.findByText('gas_new')
  await switchToLifetimeMode()
  expect(screen.getAllByText('CAPEX €/MW')).toHaveLength(2)
  expect(screen.queryByText('CAPEX €/MW/yr')).toBeNull()
})

// ── Template-e2e 14: an IPP's owned plants are never "retired" ──────────────
// eh_ipp_hybrid's wind farm is extendable between equal bounds (60 MW); before a solve the
// network serves p_nom_opt = 0. GK B2 (3) d, UX 2 (4) 1.
const ipp = (over: Record<string, unknown> = {}) => [
  { name: 'wind_farm', bus: 'B1', carrier: 'wind', p_nom: 60, p_nom_opt: 0, p_nom_min: 60, p_nom_max: 60,
    p_nom_extendable: true, capital_cost: 0, ...over },
] as never

it('shows no retired header for a plant pinned at its size', async () => {
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue(ipp())
  renderPage()
  await screen.findAllByText('Total system cost')
  await waitFor(() => expect(vi.mocked(simulationApi.getStatus)).toHaveBeenCalled())
  expect(screen.queryByText('Retired assets')).toBeNull()
  expect(screen.queryByText(/assets? retired/)).toBeNull()
})

it('shows no retired header before a solve, even for an asset free to shrink', async () => {
  vi.mocked(simulationApi.getStatus).mockReset().mockResolvedValue({ ...SOLVED, condition: null, objective: null, status: 'idle' })
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue(ipp({ p_nom_min: 0 }))
  renderPage()
  await screen.findAllByText('Total system cost')
  await waitFor(() => expect(vi.mocked(simulationApi.getStatus)).toHaveBeenCalled())
  expect(screen.queryByText('Retired assets')).toBeNull()
})

it('lists a solved retirement of an asset free to shrink', async () => {
  vi.mocked(networkApi.getGenerators).mockReset().mockResolvedValue(ipp({ p_nom_min: 0, p_nom_opt: 20 }))
  renderPage()
  expect(await screen.findByText('Retired assets')).toBeTruthy()
  expect(screen.getByText(/1 asset retired/)).toBeTruthy()
})

it('says a negative total system cost is revenue over cost, never a profit', async () => {
  const base = await resultsApi.getCostBreakdown()
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue({ ...base, capex: 0, opex: -4_550_000, total: -4_550_000 } as never)
  renderPage()
  const card = await waitFor(() => kpiCard('Total system cost'))
  await waitFor(() => expect(within(card).getByText('(revenue exceeds cost)')).toBeTruthy())
  // The sign before the €, never "€-4.55 M" (the shared tile writes its own minus).
  expect(card.textContent).toMatch(/[-\u2212]€4\.55\s?M/)
  expect(card.textContent).not.toMatch(/profit/i)
  // One sign position and one minus for the total in the view (UX 2 (5)): the build-up chart's
  // header reads as the tile does, never "€-4.55 M".
  expect(screen.getByText('-€4.55 M per year')).toBeTruthy()
})

// ── Result states (UX overhaul S2) ──────────────────────────────────────────
const extendableBattery = {
  name: 'Battery', bus: 'Bus 0', carrier: 'battery', p_nom: 0, p_nom_extendable: true,
} as unknown as StorageUnit

it('says storage tiles do not apply when the network has no storage', async () => {
  renderPage()
  await screen.findByText('Storage charge cost')
  expect(within(kpiCard('Storage charge cost')).getByText('Not applicable')).toBeTruthy()
  expect(within(kpiCard('Storage CAPEX (new)')).getByText('Not applicable')).toBeTruthy()
  expect(within(kpiCard('Storage CAPEX (new)')).queryByText('€0.00')).toBeNull()
})

it('says storage CAPEX was not modelled when no storage may expand', async () => {
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ ...extendableBattery, p_nom_extendable: false }])
  renderPage()
  await screen.findByText('Storage CAPEX (new)')
  expect(within(kpiCard('Storage CAPEX (new)')).getByText('Not modelled')).toBeTruthy()
  expect(within(kpiCard('Storage CAPEX (new)')).getByText(/No storage was allowed to expand/)).toBeTruthy()
})

it('shows a computed zero storage CAPEX when storage could expand but was not built', async () => {
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([extendableBattery])
  renderPage()
  await screen.findByText('Storage CAPEX (new)')
  const tile = kpiCard('Storage CAPEX (new)')
  expect(tile.closest('[data-state]')?.getAttribute('data-state')).toBe('zero')
  expect(getKpiValueWithin(tile, '€0')).toBeTruthy()
})

it('says curtailment cost was not modelled when no generator carries one', async () => {
  renderPage()
  await screen.findByText('Curtailment cost')
  expect(within(kpiCard('Curtailment cost')).getByText('Not modelled')).toBeTruthy()
  expect(within(kpiCard('Curtailment cost')).queryByText('—')).toBeNull()
})

it('reads a failed expansion compute as unavailable, the state the headline strip shows', async () => {
  // ui-modern 06. Fails if: the annualised figure goes back to
  // `cost.capex_expansion || totalExpansionCapex`, which turns a null into the
  // per-asset sum (here €0.00), or storage back to `?? 0`.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue({
    ...unavailablePayload(), capex_lifetime: 9_000, capex_expansion_lifetime: 1_000,
    capex_lifetime_available: true, storage_capex_expansion_lifetime: 0,
    capex_expansion: null, storage_capex_expansion: null,
  })
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([extendableBattery])
  renderPage()
  await screen.findByText('Total new investment per year')
  for (const label of ['Total new investment per year', 'Storage CAPEX (new)']) {
    const tile = kpiCard(label)
    expect(tile.closest('[data-state]')?.getAttribute('data-state')).toBe('unavailable')
    expect(within(tile).getByText('This figure could not be computed for this solve.')).toBeTruthy()
    expect(within(tile).queryByText('€0.00')).toBeNull()
  }
  // The figures that do not depend on the expansion compute stay.
  expect(getKpiValueWithin(kpiCard('CAPEX (installed)'), '€8.07 B')).toBeTruthy()
  // The one-step CAPEX in the build-up says why it is not split.
  expect(screen.getByTestId('waterfall-split-unavailable').textContent)
    .toMatch(/split into existing and new-build CAPEX is unavailable/)
  expect(screen.queryByText(/Existing-capacity CAPEX, new-build CAPEX and OPEX stack up/)).toBeNull()
})

/** The rows the cost build-up exports (`system_cost_waterfall.csv`). */
async function waterfallRows(): Promise<unknown[][]> {
  const csv = vi.spyOn(shared, 'downloadCSV').mockImplementation(() => {})
  await screen.findByText('System cost build-up')
  for (const button of screen.getAllByRole('button', { name: /CSV/ })) fireEvent.click(button)
  const call = csv.mock.calls.find(c => c[0] === 'system_cost_waterfall.csv')
  csv.mockRestore()
  if (!call) throw new Error('the cost build-up exported nothing')
  return call[2]
}

it('builds the cost build-up from one CAPEX step when the split is unavailable (ui-modern 06)', async () => {
  // GK B W1. Fails if: the build-up goes back to `cost.capex_expansion ?? 0`,
  // which splits CAPEX into an "Existing CAPEX" equal to all of it and a
  // silent zero new build.
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue({
    ...unavailablePayload(), capex_expansion: null, storage_capex_expansion: null,
  })
  renderPage()
  const names = (await waterfallRows()).map(r => r[0])
  expect(names).toEqual(['CAPEX', 'OPEX', 'Total'])
})

it('splits the cost build-up into existing and new build when the split was computed', async () => {
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue(unavailablePayload())
  renderPage()
  const rows = await waterfallRows()
  expect(rows.map(r => r[0])).toEqual(['Existing CAPEX', 'New-build CAPEX', 'OPEX', 'Total'])
  expect(rows[1][1]).toBe(1_000)
})

it('shows the Fuel & run hours section only when the cost breakdown carries a fuel-curve generator', async () => {
  const plain = await resultsApi.getCostBreakdown()
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue({
    ...plain!, fuel: [], run_hours: [],
  })
  const first = renderPage()
  await screen.findByText(/Total annual system cost|Total system cost/i, undefined, { timeout: 3000 }).catch(() => undefined)
  expect(screen.queryByTestId('fuel-run-hours')).toBeNull()
  first.unmount()
  vi.mocked(resultsApi.getCostBreakdown).mockResolvedValue({
    ...plain!,
    fuel: [{ generator: 'genset', carrier: 'diesel', fuel_mwh: 1200, no_load_fuel_mwh: 100, fuel_cost_eur: 36000, no_load_cost_eur: 3000 }],
    run_hours: [{ generator: 'genset', carrier: 'diesel', counted_from: 'status', hours_run: 800, om_cost_eur: 4000 }],
  })
  renderPage()
  expect(await screen.findByTestId('fuel-run-hours')).toBeTruthy()
})

it('withholds the storage charge cost on an integer solve rather than summing the priced part', async () => {
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([extendableBattery])
  const b = (total: number) => ({ total, by_period: {} })
  const carrier = (charge: ReturnType<typeof b> | null) => ({
    revenue_meur: null, opex_meur: b(1), gen_cost_meur: b(1), storage_charge_cost_meur: charge,
    capex_meur: b(1), dispatch_gwh: b(1), lcoe_eur_per_mwh: b(1),
  })
  vi.mocked(resultsApi.getEconomicsByCarrier).mockResolvedValue({
    available: true, unavailable_reason: 'mip_duals_not_marginal',
    // One carrier priced, one withheld: the total must not be the priced part.
    by_carrier: { gas: carrier(b(0.5)), battery: carrier(null) },
  })
  renderPage()
  await screen.findByText('Not available (integer solve)')
  const tile = kpiCard('Storage charge cost')
  expect(tile.textContent).toContain('Not available (integer solve)')
  expect(tile.textContent).not.toMatch(/€\s*0|500|NaN/)
})

it('reads the cost breakdown and economics as one bound view and marks its root with it (A5.2)', async () => {
  const { container } = renderPage()
  await waitFor(() => expect(container.querySelector('[data-result-tab="capex"]')).toBeTruthy())
  const root = container.querySelector('[data-result-tab="capex"]')!
  expect(JSON.parse(root.getAttribute('data-result-binding')!).view).toBe('capex')
})

it('a failed read says the capacity expansion could not be read, never "has not run yet" (UX 31)', async () => {
  vi.mocked(resultsApi.getCostBreakdown).mockRejectedValue(new Error('500'))
  renderPage()
  expect(await screen.findByTestId('capex-error')).toBeTruthy()
  expect(screen.getByText(/The capacity expansion could not be read/)).toBeTruthy()
  expect(screen.queryByText(/has not run yet/)).toBeNull()
})

it('keeps the loading and not-solved branches; "unavailable" is only for a failed read (UX 31)', async () => {
  let resolve: (v: null) => void = () => {}
  vi.mocked(resultsApi.getCostBreakdown).mockReturnValue(new Promise(r => { resolve = r }) as never)
  renderPage()
  expect(await screen.findByText('Loading…')).toBeTruthy()
  resolve(null)
  expect(await screen.findByText(/has not run yet/)).toBeTruthy()
  expect(screen.queryByTestId('capex-error')).toBeNull()
})

it('a solve with no expansion keeps its zero state, not "could not be read" (UX 31)', async () => {
  const { container } = renderPage()  // the default mock: capex_expansion 0
  await waitFor(() => expect(container.querySelector('[data-result-tab="capex"]')).toBeTruthy())
  expect(screen.queryByTestId('capex-error')).toBeNull()
  expect(container.textContent).not.toMatch(/could not be read/)
})
