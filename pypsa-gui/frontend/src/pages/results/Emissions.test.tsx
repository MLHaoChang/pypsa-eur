import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import Emissions from './Emissions'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return { ...actual, resultsApi: { ...actual.resultsApi, getEmissions: vi.fn() } }
})

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  // getEmissions' real return type (api/simulation.ts:260-306) requires
  // by_generator/cap/caps/is_multi_period/by_period beyond what the brief
  // supplied. The brief's literal `{ total_tCO2, by_carrier }` fails tsc
  // (missing required fields) AND would crash at runtime: with no
  // ResultsFilterProvider in this test, useResultsFilter() returns the
  // context default `selectedPeriod: null` (filterContext.tsx:23), so
  // Emissions.tsx's `view` memo takes the horizon branch, and the very next
  // memo unconditionally calls `emissions.caps.filter(...)`
  // (Emissions.tsx:70) — a missing `caps` throws
  // "Cannot read properties of undefined (reading 'filter')". The
  // unconditionally-rendered "By generator" section similarly calls
  // `view.by_generator.filter(...)` (Emissions.tsx:295), i.e.
  // `emissions.by_generator.filter(...)` on the horizon branch.
  // total_tCO2/by_carrier are the values under test (kept exactly as the
  // brief specified); the rest are inert defaults:
  //   - by_generator: [] keeps the "By generator" section hidden
  //     (Emissions.tsx:295 guard: `.filter(r => r.tCO2 > 0).length > 0`).
  //   - cap: { active: false } is never read by Emissions.tsx at all (only
  //     the plural `caps[]` is; grepped to confirm).
  //   - caps: [] keeps scopeCaps/allCaps empty, so the caps table and the
  //     two extra KPIs (shadow price / cap slack) stay hidden.
  //   - is_multi_period: false keeps the per-period section
  //     (Emissions.tsx:200) hidden.
  //   - by_period: [] is never reached: is_multi_period === false
  //     short-circuits the `&&` chain that would read it.
  vi.mocked(resultsApi.getEmissions).mockReset().mockResolvedValue({
    total_tCO2: 424.24,
    by_carrier: [{ carrier: 'gas', tCO2: 424.24, share_pct: 100.0 }],
    by_generator: [],
    cap: { active: false },
    caps: [],
    is_multi_period: false,
    by_period: [],
  })
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <Emissions />
    </QueryClientProvider>,
  )
}

it('renders the distinctive total emissions KPI sourced from the mocked results API', async () => {
  renderPage()
  // total_tCO2.toLocaleString(undefined, { maximumFractionDigits: 1 }) rounds
  // 424.24 to "424.2" (Emissions.tsx:120-122).
  const match = await screen.findByText((text) => text.includes('424.2') && text.includes('tCO'))
  expect(match).toBeTruthy()
})
