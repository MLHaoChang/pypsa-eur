// The backend computes NO delta for the Compare tab — GET
// /compare/{name}/results-summary returns ONE project's numbers, and
// CompareView.tsx fetches A and B independently (paired useQuery calls per
// tab) then subtracts them client-side, inline in JSX via <Delta>. Every
// number the user reads *as a comparison* is frontend arithmetic that had
// zero test coverage before this file. Nothing in CompareView.tsx is
// exported — every tab component and helper is module-private — so these
// tests render the whole page with a mocked API and assert on rendered
// text, per the established pattern in layout/ProjectTabs.test.tsx.
//
// Task 17 (A/A identity): feed the same payload to both sides on every
// drivable tab; no delta cell may carry a sign.
// Task 18 (delta arithmetic): (1) sign convention — B bigger reads '+',
// B smaller reads '-', consistently across tabs; (2) a `null` field must
// read as "not reported", never as a literal zero that produces a bogus
// reduction; (3) with a specific period selected, both sides must read
// `by_period[period]`, never fall back to `total`.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/projects')

import { projectsApi } from '../api/projects'
import CompareView from './CompareView'
import type { CompareState, ResultsSummary } from '../api/types'

// Every tab wraps a recharts <ResponsiveContainer> (bar / line charts) around
// the tables we actually assert on. recharts calls `new ResizeObserver(...)`
// unconditionally on mount to track its container size; jsdom doesn't
// implement ResizeObserver at all, so without this stub CompareView's own
// <ErrorBoundary key={`${a}|${b}|${tab}`}> around each tab body catches
// "ResizeObserver is not defined" on EVERY tab and renders "This comparison
// failed to render" instead of any content — every test below fails at its
// first content assertion, not at the delta assertion, which is a strong
// enough signal that it is worth documenting rather than debugging blind.
class ResizeObserverStub {
  observe() { /* noop */ }
  unobserve() { /* noop */ }
  disconnect() { /* noop */ }
}
;(globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver = ResizeObserverStub

// CompareView persists A/B/tab selection under these localStorage keys (see
// CMP_A_KEY / CMP_B_KEY / CMP_TAB_KEY at the top of CompareView.tsx). They
// are module-private — not exported — so re-declared here; keep in sync if
// they ever change. Both projects must be picked (`bothPicked`) before any
// tab body renders at all.
const CMP_A_KEY = 'compare:a'
const CMP_B_KEY = 'compare:b'
const CMP_TAB_KEY = 'compare:tab'

// jsdom in this project used to expose `localStorage` as a bare `{}` (Node's
// inert stub shadowing jsdom's real Storage — see topologyLayoutStore.test.tsx
// and vitest.setup.ts). vitest.setup.ts now force-installs jsdom's real
// Storage onto globalThis before any test runs, so plain
// localStorage.getItem/setItem/clear work here without a manual stub —
// verified by running this file; if that ever regresses, every test below
// will fail in beforeEach with "localStorage.clear is not a function",
// which is the tell.

type Tab =
  | 'overview' | 'capacity' | 'dispatch' | 'loading' | 'prices'
  | 'emissions' | 'economics' | 'curtailment' | 'lost_load' | 'storage_cycling'

const ALL_TABS: Tab[] = [
  'overview', 'capacity', 'dispatch', 'loading', 'prices',
  'emissions', 'economics', 'curtailment', 'lost_load', 'storage_cycling',
]

const pv = (total: number, y2030: number, y2035: number) =>
  ({ total, by_period: { '2030': y2030, '2035': y2035 } })

/**
 * Non-trivial fixture: two periods, several carriers, non-zero everywhere,
 * and deliberately ASYMMETRIC by_period splits (2030 !== 2035 !== total) so
 * a fallback-to-total bug is visible rather than accidentally matching.
 * `scale` multiplies every numeric leaf by a constant factor — used to
 * build a "B bigger" or "B smaller" fixture for the sign-convention checks
 * while every internal ratio (including the asymmetric split) is preserved.
 * scale=1 for both sides (the beforeEach default) is the A/A identity
 * fixture.
 */
function summary(project: string, scale = 1): ResultsSummary {
  const s = scale
  return {
    project, is_multi_period: true, periods: [2030, 2035], has_solve: true,
    capacity: {
      available: true,
      capacity_mw_by_carrier: { solar: pv(200 * s, 200 * s, 0) },
      capex_meur_by_carrier: { solar: pv(82.5 * s, 27.5 * s, 55 * s) },
      new_capex_meur_by_carrier: { solar: pv(10 * s, 4 * s, 6 * s) },
      new_capacity_mw_by_carrier: { solar: pv(50 * s, 50 * s, 0) },
      storage_mw_by_carrier: { battery: pv(30 * s, 30 * s, 30 * s) },
      storage_mwh_by_carrier: { battery: pv(120 * s, 120 * s, 120 * s) },
      new_storage_mw_by_carrier: {},
      new_storage_mwh_by_carrier: {},
      link_capacity_mw_by_carrier: { electrolysis: pv(15 * s, 15 * s, 15 * s) },
      new_link_capacity_mw_by_carrier: {},
    },
    dispatch: {
      available: true,
      dispatch_gwh_by_carrier: { solar: pv(300 * s, 100 * s, 200 * s) },
      opex_meur: pv(12 * s, 4 * s, 8 * s),
      total_load_gwh: pv(300 * s, 100 * s, 200 * s),
      storage_cycles_by_carrier: {},
    },
    loading: {
      available: true,
      lines: [{
        name: 'Line1', s_nom_opt: 500, is_transformer: false, is_link: false, carrier: null,
        peak_loading: pv(0.8 * s, 0.6 * s, 0.9 * s),
        mean_loading: pv(0.5 * s, 0.4 * s, 0.6 * s),
        binding_hours: pv(100 * s, 40 * s, 60 * s),
      }],
    },
    prices: {
      available: true,
      duration_curve: Array.from({ length: 101 }, (_, i) => (100 - i) * s),
      mean_price: pv(45 * s, 40 * s, 50 * s),
      median_price: pv(44 * s, 39 * s, 49 * s),
      p90_price: pv(70 * s, 65 * s, 75 * s),
      max_price: 120 * s,
      min_price: -5 * s,
      bus_count: 3,
      by_carrier_stats: {
        electricity: {
          bus_count: 3,
          mean_price: pv(45 * s, 40 * s, 50 * s),
          median_price: pv(44 * s, 39 * s, 49 * s),
          p90_price: pv(70 * s, 65 * s, 75 * s),
        },
      },
    },
    emissions: {
      available: true,
      total_kt: pv(500 * s, 200 * s, 300 * s),
      by_carrier_kt: { gas: pv(500 * s, 200 * s, 300 * s) },
      intensity_kg_per_mwh: pv(250 * s, 200 * s, 270 * s),
    },
    economics: {
      available: true,
      by_carrier: {
        solar: {
          revenue_meur: pv(60 * s, 20 * s, 40 * s),
          opex_meur: pv(12 * s, 4 * s, 8 * s),
          gen_cost_meur: pv(8 * s, 3 * s, 5 * s),
          storage_charge_cost_meur: pv(1 * s, 0.4 * s, 0.6 * s),
          curtailment_cost_meur: pv(2 * s, 0.5 * s, 1.5 * s),
          lost_load_cost_meur: pv(1 * s, 0.1 * s, 0.9 * s),
          capex_meur: pv(82.5 * s, 27.5 * s, 55 * s),
          dispatch_gwh: pv(300 * s, 100 * s, 200 * s),
          lcoe_eur_per_mwh: pv(50 * s, 45 * s, 55 * s),  // re-derived by the component; input is a placeholder
        },
      },
      per_asset_lcoh: [{
        name: 'Electrolyser1', carrier: 'electrolysis', p_nom_opt: 15 * s,
        capex_meur: pv(5 * s, 2 * s, 3 * s),
        opex_meur: pv(1 * s, 0.4 * s, 0.6 * s),
        input_cost_meur: pv(3 * s, 1 * s, 2 * s),
        output_gwh: pv(20 * s, 8 * s, 12 * s),
        lcoh_eur_per_mwh: pv(60 * s, 55 * s, 65 * s),
      }],
    },
    curtailment: {
      available: true,
      total_gwh: pv(20 * s, 8 * s, 12 * s),
      by_carrier_gwh: { solar: pv(20 * s, 8 * s, 12 * s) },
      rate_pct_by_carrier: { solar: pv(6.25 * s, 7.4 * s, 5.7 * s) },
      system_rate_pct: pv(6.25 * s, 7.4 * s, 5.7 * s),
    },
    lost_load: {
      available: true,
      voll_eur_per_mwh: 3000,
      total_mwh: pv(10 * s, 4 * s, 6 * s),
      total_cost_meur: pv(0.03 * s, 0.012 * s, 0.018 * s),
      by_bus: [{ bus: 'Bus1', energy_mwh: pv(10 * s, 4 * s, 6 * s), cost_meur: pv(0.03 * s, 0.012 * s, 0.018 * s) }],
      by_carrier: [{
        carrier: 'electricity', bus_count: 1,
        energy_mwh: pv(10 * s, 4 * s, 6 * s), cost_meur: pv(0.03 * s, 0.012 * s, 0.018 * s),
      }],
    },
    storage_cycling: {
      available: true,
      cycles_by_carrier: { battery: pv(150 * s, 60 * s, 90 * s) },
      by_unit: [{
        name: 'Battery1', carrier: 'battery', p_nom_mw: 30 * s, energy_mwh: 120 * s,
        throughput_mwh: pv(500 * s, 200 * s, 300 * s), cycles: pv(150 * s, 60 * s, 90 * s),
      }],
    },
  }
}

function compareState(name: string, scale = 1): CompareState {
  const s = scale
  return {
    name,
    bus_count: 5, generator_count: 3, line_count: 4, load_count: 2,
    link_count: 1, storage_unit_count: 1, store_count: 0, snapshot_count: 8760,
    snapshot_start: '2030-01-01T00:00:00', snapshot_end: '2030-12-31T23:00:00',
    installed_capacity_by_carrier: { solar: 150 * s },
    optimised_capacity_by_carrier: { solar: 200 * s },
    storage_capacity_by_carrier: { battery: 30 * s },
    store_capacity_by_carrier: {},
    peak_demand_mw: 120 * s,
    total_energy_mwh: 300_000 * s,
    objective: 1_234_567 * s,
    solve_time: 12.3,
    dispatch_status: 'fresh',
    parent_project: null,
    scenario_description: null,
    created_at: null,
    last_saved: null,
  }
}

const renderCompare = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><CompareView /></QueryClientProvider>)
}

const seedPicker = (tab: Tab) => {
  localStorage.setItem(CMP_A_KEY, 'A')
  localStorage.setItem(CMP_B_KEY, 'B')
  localStorage.setItem(CMP_TAB_KEY, tab)
}

const mockBothSides = (scaleA: number, scaleB: number) => {
  vi.mocked(projectsApi.resultsSummary).mockImplementation(async (name: string) =>
    (name === 'A' ? summary('A', scaleA) : summary('B', scaleB)) as never)
  vi.mocked(projectsApi.compareState).mockImplementation(async (name: string) =>
    (name === 'A' ? compareState('A', scaleA) : compareState('B', scaleB)) as never)
}

// Every delta cell is rendered by <Delta>, whose only two DIRECT text-node
// children are the sign ('+' / '' — negatives get their '-' from fmt(v)
// itself) and the formatted number, with no wrapping markup around either.
// Testing Library's getNodeText only concatenates an element's OWN direct
// text-node children (not descendants' text), so this regex — anchored at
// the start of an element's text — matches Delta spans specifically and NOT
// e.g. the Prices tab's "min -5.0 €/MWh" badge, whose '-5.0' is a sibling
// text node inside a span that also contains "min " and other siblings, so
// that span's own concatenated text starts with "max ..." not with a sign.
const signedTexts = () => screen.queryAllByText(/^[+−-]\s*\d/).map(e => e.textContent)

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(projectsApi.list).mockResolvedValue([
    { name: 'A', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 5, snapshot_count: 8760, objective: 1 },
    { name: 'B', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 5, snapshot_count: 8760, objective: 1 },
  ] as never)
  mockBothSides(1, 1)  // default: A/A identity payload
})

// ── Task 17: A/A identity ────────────────────────────────────────────────

// A fixture-specific string that must appear on screen before we trust a
// "no signed deltas" result — otherwise a tab that failed to render at all
// (crashed into the ErrorBoundary, stuck on the loading spinner, or an
// UnsolvedBanner) would trivially pass with zero content.
const AA_ANCHOR: Record<Tab, RegExp> = {
  overview: /€1\.23M/,                 // compareState objective, SolverTable
  capacity: /82\.50 M€/,               // capex_meur_by_carrier.solar.total
  dispatch: /12\.00 M€/,               // opex_meur.total
  loading: /80\.0 %/,                  // peak_loading.total (0.8 -> 80.0%)
  prices: /45\.00 €\/MWh/,             // mean_price.total
  emissions: /500\.0 kt/,              // total_kt.total
  economics: /60\.00 M€/,              // by_carrier.solar.revenue_meur.total
  curtailment: /20\.0 GWh/,            // total_gwh.total
  lost_load: /10\.0 MWh/,              // total_mwh.total
  storage_cycling: /150\.0 cyc/,       // cycles_by_carrier.battery.total
}

describe('Task 17 — A/A identity', () => {
  it.each(ALL_TABS)('%s: identical A/B payloads produce no signed deltas', async (tab) => {
    seedPicker(tab)
    renderCompare()
    // 1. Prove the tab actually rendered real fixture content...
    await waitFor(() => expect(screen.queryAllByText(AA_ANCHOR[tab]).length).toBeGreaterThan(0))
    // 2. ...THEN assert no delta cell carries a sign. Delta blanks to '—'
    //    only when |v| < 1e-9, so this is a real zero-arithmetic check, not
    //    a "the component happened not to render" false pass.
    expect(signedTexts()).toEqual([])
  })
})

// ── Task 18 — Step 1: sign convention ────────────────────────────────────
// bValue - aValue is the sign convention everywhere the source reads (vb -
// va, bValue - aValue, cb - ca, ...). `invert` on <Delta> only recolours a
// bad-direction change (e.g. cost going up) — it must NOT flip the sign
// text. Scaling every (non-negative) fixture leaf uniformly by a constant
// factor makes every non-zero delta in a tab share one sign: if the code
// ever computed A - B instead of B - A on some tab, or if `invert` leaked
// into the sign, this test catches it directly as a wrong-sign string.

describe('Task 18 — sign convention', () => {
  it.each(ALL_TABS)('%s: B bigger than A -> every rendered delta is positive', async (tab) => {
    seedPicker(tab)
    mockBothSides(1, 2)  // B = 2x A
    renderCompare()
    await waitFor(() => expect(signedTexts().length).toBeGreaterThan(0))
    const wrongSign = signedTexts().filter(t => !t!.startsWith('+'))
    expect(wrongSign).toEqual([])
  })

  it.each(ALL_TABS)('%s: B smaller than A -> every rendered delta is negative', async (tab) => {
    seedPicker(tab)
    mockBothSides(2, 1)  // B = 0.5x A
    renderCompare()
    await waitFor(() => expect(signedTexts().length).toBeGreaterThan(0))
    const wrongSign = signedTexts().filter(t => t!.startsWith('+'))
    expect(wrongSign).toEqual([])
  })
})

// ── Task 18 — Step 2: zero vs absent baseline ────────────────────────────
// A `null` category on one side means "not reported by this project" — it
// must never be silently read as "reported, value zero", because that
// manufactures a fake 100%-scale swing that isn't in the data at all.

describe('Task 18 — zero vs absent baseline', () => {
  it('emissions: a null side correctly bails to a message, not a fabricated -100%', async () => {
    seedPicker('emissions')
    vi.mocked(projectsApi.resultsSummary).mockImplementation(async (name: string) => {
      const s = summary(name, 1)
      if (name === 'B') s.emissions = null
      return s as never
    })
    renderCompare()
    await waitFor(() => expect(screen.getByText(/No emissions data/)).toBeTruthy())
    expect(screen.queryByText(/-100/)).toBeNull()
    // The whole tab bails to a text banner instead of computing per-carrier
    // deltas against a phantom zero — confirmed by there being NO delta
    // cells at all, not just none matching "-100".
    expect(signedTexts()).toEqual([])
  })

  // Was a standing `it.fails` documenting the defect: CurtailmentTab guarded
  // with `!hasAnyA && !hasAnyB` (AND — only trips when BOTH sides are empty)
  // where EmissionsTab uses OR. With only B's curtailment null the AND guard
  // did not fire, every `cB?.<field>` fell through readPV's `if (!pv) return 0`
  // to a literal 0, and against A's 100% rate the delta read -100.00%
  // ("B eliminated all curtailment") — not information the payload contains.
  // CompareView now bails on either side missing, so this asserts for real.
  it('curtailment: a null side bails to a message, not a fabricated -100%', async () => {
    seedPicker('curtailment')
    vi.mocked(projectsApi.resultsSummary).mockImplementation(async (name: string) => {
      const s = summary(name, 1)
      if (name === 'A') s.curtailment = { ...s.curtailment!, system_rate_pct: pv(100, 100, 100) }
      if (name === 'B') s.curtailment = null
      return s as never
    })
    renderCompare()
    await waitFor(() => expect(screen.getByText(/No curtailment data for/)).toBeTruthy())
    expect(screen.queryByText(/-100/)).toBeNull()
    expect(signedTexts()).toEqual([])
  })

  it('curtailment: the banner names the side that has no data', async () => {
    seedPicker('curtailment')
    vi.mocked(projectsApi.resultsSummary).mockImplementation(async (name: string) => {
      const s = summary(name, 1)
      if (name === 'B') s.curtailment = null
      return s as never
    })
    renderCompare()
    // Naming the absent side is the point — "neither project" would be a lie
    // when only one is missing, and leaves the user unable to tell which.
    const banner = await screen.findByText(/No curtailment data for/)
    expect(banner.textContent).toContain('B')
    expect(banner.textContent).not.toContain('neither')
  })

  it('curtailment: both sides present still renders the comparison', async () => {
    // Guards the fix from over-reach — the OR must not suppress the normal case.
    seedPicker('curtailment')
    mockBothSides(2, 1)
    renderCompare()
    await waitFor(() => expect(signedTexts().length).toBeGreaterThan(0))
    expect(screen.queryByText(/No curtailment data/)).toBeNull()
  })
})

// ── Task 18 — Step 3: period selection ───────────────────────────────────
// With the period selector on a specific period, both sides must read
// by_period[period] — never fall back to `total`. The fixture's asymmetric
// split (total 82.5 / 27.5 / 55, no two equal) makes a total-fallback
// visible: if either side leaked through as `total`, "82.50" would still be
// on screen after selecting period 2030, and "27.50" would be missing or
// wrong.

describe('Task 18 — period selection', () => {
  it('capacity CAPEX: selecting period 2030 reads by_period, not total', async () => {
    seedPicker('capacity')
    renderCompare()
    await waitFor(() => expect(screen.queryAllByText(/82\.50 M€/).length).toBeGreaterThan(0))
    await userEvent.click(screen.getByRole('button', { name: '2030' }))
    await waitFor(() => expect(screen.queryAllByText(/27\.50 M€/).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/82\.50 M€/).length).toBe(0)
  })

  it('dispatch OPEX: selecting period 2030 reads by_period, not total', async () => {
    seedPicker('dispatch')
    renderCompare()
    await waitFor(() => expect(screen.queryAllByText(/12\.00 M€/).length).toBeGreaterThan(0))
    await userEvent.click(screen.getByRole('button', { name: '2030' }))
    await waitFor(() => expect(screen.queryAllByText(/4\.00 M€/).length).toBeGreaterThan(0))
    expect(screen.queryAllByText(/12\.00 M€/).length).toBe(0)
  })
})
