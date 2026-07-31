// Regression coverage for the false-zero guard (C2): a bare "0 tCO2" must
// only be explained as "nobody told the model what this fuel emits" when
// that is actually true. Added after a review found this property — "a
// genuinely clean network must not be told its data is missing" — had no
// test. Follows the render/mock recipe in OverviewPanel.download.test.tsx.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { networkApi } from '../../api/network'
import { useUIStore } from '../../store/uiStore'
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

function renderEmissions() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <Emissions />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(resultsApi.getEmissions).mockReset()
  vi.mocked(networkApi.getCarriers).mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

describe('Emissions — the zero-emissions explanation', () => {
  it('does NOT explain the zero when a carrier genuinely carries a CO2 intensity', async () => {
    // total_tCO2 is 0 here because nothing on this carrier was dispatched —
    // NOT because nobody told the model what it emits. Telling the user
    // their data is missing when it is present would send them chasing a
    // problem that doesn't exist.
    vi.mocked(resultsApi.getEmissions).mockResolvedValue(ZERO_EMISSIONS as never)
    vi.mocked(networkApi.getCarriers).mockResolvedValue([
      { name: 'gas', co2_emissions: 0.5, color: '', nice_name: 'gas', unit: 'MWh' } as Carrier,
    ])

    renderEmissions()

    await screen.findByText('0 tCO₂')
    expect(screen.queryByTitle(EXPLANATION)).toBeNull()
  })

  it('explains the zero when no carrier has any CO2 intensity', async () => {
    vi.mocked(resultsApi.getEmissions).mockResolvedValue(ZERO_EMISSIONS as never)
    vi.mocked(networkApi.getCarriers).mockResolvedValue([
      { name: 'gas', co2_emissions: 0, color: '', nice_name: 'gas', unit: 'MWh' } as Carrier,
      { name: 'solar', co2_emissions: 0, color: '', nice_name: 'solar', unit: 'MWh' } as Carrier,
    ])

    renderEmissions()

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

    renderEmissions()

    await screen.findByText('123.4 tCO₂')
    expect(screen.queryByTitle(EXPLANATION)).toBeNull()
  })
})
