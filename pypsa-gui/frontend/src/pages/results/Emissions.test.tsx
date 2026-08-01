import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import type { Carrier } from '../../api/types'
import Emissions from './Emissions'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getEmissions: vi.fn() } }
})

vi.mock('../../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn() } }
})

beforeEach(() => {
  vi.mocked(resultsApi.getEmissions).mockReset()
  vi.mocked(networkApi.getCarriers).mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <Emissions />
    </QueryClientProvider>,
  )
}

it('renders the distinctive total emissions KPI sourced from the mocked results API', async () => {
  // getEmissions' real return type (api/simulation.ts:260-306) requires
  // by_generator/cap/caps/is_multi_period/by_period beyond what the brief
  // supplied. The brief's literal `{ total_tCO2, by_carrier }` fails tsc
  // (missing required fields) AND would crash at runtime: with no
  // ResultsFilterProvider in this test, useResultsFilter() returns the
  // context default `selectedPeriod: null` (filterContext.tsx:23), so
  // Emissions.tsx's `view` memo takes the horizon branch, and the very next
  // memo unconditionally calls `emissions.caps.filter(...)`
  // (Emissions.tsx:77) — a missing `caps` throws
  // "Cannot read properties of undefined (reading 'filter')". The
  // unconditionally-rendered "By generator" section similarly calls
  // `view.by_generator.filter(...)` (Emissions.tsx:297), i.e.
  // `emissions.by_generator.filter(...)` on the horizon branch.
  // total_tCO2/by_carrier are the values under test (kept exactly as the
  // brief specified); the rest are inert defaults:
  //   - by_generator: [] keeps the "By generator" section hidden
  //     (Emissions.tsx:297 guard: `.filter(r => r.tCO2 > 0).length > 0`).
  //   - cap: { active: false } is never read by Emissions.tsx at all (only
  //     the plural `caps[]` is; grepped to confirm).
  //   - caps: [] keeps scopeCaps/allCaps empty, so the caps table and the
  //     two extra KPIs (shadow price / cap slack) stay hidden.
  //   - is_multi_period: false keeps the per-period section
  //     (Emissions.tsx:200) hidden.
  //   - by_period: [] is never reached: is_multi_period === false
  //     short-circuits the `&&` chain that would read it.
  vi.mocked(resultsApi.getEmissions).mockResolvedValue({
    total_tCO2: 424.24,
    by_carrier: [{ carrier: 'gas', tCO2: 424.24, share_pct: 100.0 }],
    by_generator: [],
    cap: { active: false },
    caps: [],
    is_multi_period: false,
    by_period: [],
  })
  // Emissions.tsx also queries networkApi.getCarriers (used solely to decide
  // whether a bare "0 tCO2" needs the false-zero explanation — see the
  // describe block below); total_tCO2 here is non-zero so that branch never
  // fires, and an empty carrier list is enough not to affect this assertion.
  vi.mocked(networkApi.getCarriers).mockResolvedValue([])

  renderPage()
  // total_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 }) rounds
  // 424.24 to "424.2" (Emissions.tsx:120-122).
  const match = await screen.findByText((text) => text.includes('424.2') && text.includes('tCO'))
  expect(match).toBeTruthy()
})

// Regression coverage for the false-zero guard (C2): a bare "0 tCO2" must
// only be explained as "nobody told the model what this fuel emits" when
// that is actually true. Added after a review found this property — "a
// genuinely clean network must not be told its data is missing" — had no
// test. Follows the render/mock recipe in OverviewPanel.download.test.tsx.
describe('Emissions — the zero-emissions explanation', () => {
  const ZERO_EMISSIONS = {
    total_tCO2: 0,
    by_carrier: [],
    by_generator: [],
    cap: { active: false },
    caps: [],
    is_multi_period: false,
    by_period: [],
  }

  const EXPLANATION = /No carrier in this network has a CO₂ intensity/

  it('does NOT explain the zero when a carrier genuinely carries a CO2 intensity', async () => {
    // total_tCO2 is 0 here because nothing on this carrier was dispatched —
    // NOT because nobody told the model what it emits. Telling the user
    // their data is missing when it is present would send them chasing a
    // problem that doesn't exist.
    vi.mocked(resultsApi.getEmissions).mockResolvedValue(ZERO_EMISSIONS as never)
    vi.mocked(networkApi.getCarriers).mockResolvedValue([
      { name: 'gas', co2_emissions: 0.5, color: '', nice_name: 'gas', unit: 'MWh' } as Carrier,
    ])

    renderPage()

    await screen.findByText('0 tCO₂')
    expect(screen.queryByTitle(EXPLANATION)).toBeNull()
  })

  it('explains the zero when no carrier has any CO2 intensity', async () => {
    vi.mocked(resultsApi.getEmissions).mockResolvedValue(ZERO_EMISSIONS as never)
    vi.mocked(networkApi.getCarriers).mockResolvedValue([
      { name: 'gas', co2_emissions: 0, color: '', nice_name: 'gas', unit: 'MWh' } as Carrier,
      { name: 'solar', co2_emissions: 0, color: '', nice_name: 'solar', unit: 'MWh' } as Carrier,
    ])

    renderPage()

    await screen.findByText('0 tCO₂')
    expect(screen.getByTitle(EXPLANATION)).toBeDefined()
  })

  it('never explains a genuinely non-zero total', async () => {
    // Guards against a careless `view.total_tCO2 === 0` check turning into
    // something looser that fires on any small/rounded value.
    vi.mocked(resultsApi.getEmissions).mockResolvedValue({
      ...ZERO_EMISSIONS,
      total_tCO2: 123.4,
      by_carrier: [{ carrier: 'gas', tCO2: 123.4, share_pct: 100 }],
    } as never)
    vi.mocked(networkApi.getCarriers).mockResolvedValue([
      { name: 'gas', co2_emissions: 0.187, color: '', nice_name: 'gas', unit: 'MWh' } as Carrier,
    ])

    renderPage()

    await screen.findByText('123.4 tCO₂')
    expect(screen.queryByTitle(EXPLANATION)).toBeNull()
  })
})
