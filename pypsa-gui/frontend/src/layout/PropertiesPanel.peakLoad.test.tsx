// Bug 4 (guided-mode spec §2.3): a bus whose Load carries its demand in
// `loads_t.p_set` showed "Total load 0 MW" because the card summed the static
// `p_set`. The backend now sends `p_set_peak` (max-magnitude of the series, or
// the static value); the card sums `p_set_peak ?? p_set` as "Peak load".
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { Bus, Load } from '../api/types'
import PropertiesPanel from './PropertiesPanel'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getCarriers: vi.fn(), getGenerators: vi.fn(),
      getLoads: vi.fn(), getStorageUnits: vi.fn(), getStores: vi.fn(),
      getLinks: vi.fn(),
    },
  }
})

const BUS: Bus = { name: 'it_bus', v_nom: 11, carrier: 'AC', x: 0, y: 0, country: '',
  unit: '', control: 'PQ', sub_network: '' } as Bus

function load(extra: Partial<Load>): Load {
  return { name: 'it_load', bus: 'it_bus', carrier: 'AC', p_set: 0, q_set: 0, sign: -1, ...extra }
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

/** The "Peak load" row's text, once the loads query has filled it. */
async function peakRowText(expected: string): Promise<string> {
  let text = ''
  await vi.waitFor(() => {
    const row = screen.getByText('Peak load').closest('div.flex')!
    text = row.textContent ?? ''
    expect(text).toContain(expected)
  })
  return text
}

beforeEach(() => {
  for (const fn of ['getCarriers', 'getGenerators', 'getStorageUnits', 'getStores', 'getLinks'] as const) {
    vi.mocked(networkApi[fn]).mockReset().mockResolvedValue([])
  }
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([BUS])
  useUIStore.setState({ currentProject: 'dc', selectedComponent: { type: 'Bus', name: 'it_bus' } })
})

afterEach(() => {
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

describe('Bus card — Peak load (bug 4)', () => {
  it('renders the time-series peak, not the zero static p_set', async () => {
    vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([load({ p_set: 0, p_set_peak: 12.5 })])
    renderPanel()
    const text = await peakRowText('12.5')
    expect(text).toBe('Peak load12.5 MW')
    expect(screen.queryByText('Total load')).toBeNull()
  })

  it('falls back to the static p_set when p_set_peak is absent', async () => {
    vi.mocked(networkApi.getLoads).mockReset().mockResolvedValue([
      load({ name: 'a', p_set: 4 }), load({ name: 'b', p_set: -3, p_set_peak: null }),
    ])
    renderPanel()
    expect(await peakRowText('7')).toBe('Peak load7 MW')
  })
})
