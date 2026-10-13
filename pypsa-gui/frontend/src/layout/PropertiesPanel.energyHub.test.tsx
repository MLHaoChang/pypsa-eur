// P14 — Energy Hub tags on the Bus and Link property cards.
//
// Bites: (1) the tag must be seeded into the form (else a remove+add save
// silently clears it); (2) an untouched ordinary bus/link must NOT send eh_*
// keys (else every edit creates the columns network-wide); (3) an existing
// tag can be cleared.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { Bus, Link } from '../api/types'
import PropertiesPanel from './PropertiesPanel'
import { ehBusPayload, ehLinkPayload } from './properties/cardKit'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getCarriers: vi.fn(), getGenerators: vi.fn(),
      getLoads: vi.fn(), getStorageUnits: vi.fn(), getStores: vi.fn(),
      getLinks: vi.fn(), updateBus: vi.fn(), updateLink: vi.fn(),
    },
  }
})

function bus(extra: Partial<Bus> = {}): Bus {
  return { name: 'hub', v_nom: 1, carrier: 'AC', x: 0, y: 0, country: '',
           unit: '', control: 'PQ', sub_network: '', ...extra }
}

function link(extra: Partial<Link> = {}): Link {
  return {
    name: 'imp', bus0: 'grid', bus1: 'hub', carrier: 'AC', p_nom: 100,
    p_nom_extendable: false, p_nom_min: 0, p_nom_max: null, p_min_pu: 0,
    p_max_pu: 1, efficiency: 1, marginal_cost: 0, capital_cost: 0,
    build_year: 2025, lifetime: null, ...extra,
  } as Link
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

beforeEach(() => {
  for (const fn of ['getCarriers', 'getGenerators', 'getLoads',
    'getStorageUnits', 'getStores'] as const) {
    vi.mocked(networkApi[fn]).mockReset().mockResolvedValue([])
  }
  vi.mocked(networkApi.getBuses).mockReset().mockResolvedValue([bus()])
  vi.mocked(networkApi.getLinks).mockReset().mockResolvedValue([link()])
  vi.mocked(networkApi.updateBus).mockReset()
    .mockResolvedValue({ data: { name: 'hub', rescale: [] } } as never)
  vi.mocked(networkApi.updateLink).mockReset()
    .mockResolvedValue({ data: { name: 'imp' } } as never)
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

describe('EH payload helpers', () => {
  it('sends nothing for an untouched, untagged row', () => {
    expect(ehBusPayload({ eh_poc: 'false', eh_critical: 'false',
      eh_sk_mva: '', eh_ibr_mva: '' }, bus())).toEqual({})
    expect(ehLinkPayload({ eh_role: '' }, link())).toEqual({})
  })

  it('clears an existing tag', () => {
    expect(ehBusPayload({ eh_poc: 'false', eh_critical: 'false',
      eh_sk_mva: '', eh_ibr_mva: '' }, bus({ eh_poc: true, eh_sk_mva: 200 })))
      .toEqual({ eh_poc: false, eh_sk_mva: null })
    expect(ehLinkPayload({ eh_role: '' }, link({ eh_role: 'grid_import' })))
      .toEqual({ eh_role: '' })
  })

  it('does not re-send an unchanged role (study-internal roles are refused)', () => {
    expect(ehLinkPayload({ eh_role: 'eh_n1_conversion' },
      link({ eh_role: 'eh_n1_conversion' }))).toEqual({})
    expect(ehLinkPayload({ eh_role: 'grid_import' },
      link({ eh_role: 'eh_n1_conversion' }))).toEqual({ eh_role: 'grid_import' })
  })
})

describe('Bus card — Energy Hub section', () => {
  beforeEach(() => {
    useUIStore.setState({ currentProject: 'Demo',
      selectedComponent: { type: 'Bus', name: 'hub' } })
  })

  it('tags the bus as critical with a short-circuit level', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    await userEvent.click(screen.getByRole('checkbox', { name: /critical bus/i }))
    const sk = screen.getByText(/short-circuit level/i).closest('label')!
      .querySelector('input')!
    await userEvent.type(sk, '250')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await vi.waitFor(() => expect(networkApi.updateBus).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(networkApi.updateBus).mock.calls[0]
    expect(payload).toMatchObject({ eh_critical: true, eh_sk_mva: 250 })
    expect(payload).not.toHaveProperty('eh_poc')
  })

  it('keeps an existing tag through an unrelated save', async () => {
    vi.mocked(networkApi.getBuses).mockResolvedValue([bus({ eh_poc: true })])
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit bus/i }))
    expect((screen.getByRole('checkbox', { name: /point of connection/i }) as
      HTMLInputElement).checked).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await vi.waitFor(() => expect(networkApi.updateBus).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(networkApi.updateBus).mock.calls[0]
    expect(payload).toMatchObject({ eh_poc: true })
  })
})

describe('Link card — Energy Hub role', () => {
  it('sets the grid-import role', async () => {
    useUIStore.setState({ currentProject: 'Demo',
      selectedComponent: { type: 'Link', name: 'imp' } })
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit/i }))
    const role = screen.getByText(/energy hub role/i).closest('label')!
      .querySelector('select')!
    await userEvent.selectOptions(role, 'grid_import')
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }))
    await vi.waitFor(() => expect(networkApi.updateLink).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(networkApi.updateLink).mock.calls[0]
    expect(payload).toMatchObject({ eh_role: 'grid_import' })
  })
})
