// Terminal prefill (spec D27) and the coordinate-seed removal (D28).
//
// The bus list CreationForm reads is the React Query cache under
// nk(currentProject, 'buses') — seeded directly with setQueryData rather than
// through a mocked fetch, because the form reads it with getQueryData, not
// useQuery (CreationForm.tsx:366-369).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      createBus: vi.fn(),
      createGenerator: vi.fn(),
      createLink: vi.fn(),
    },
  }
})

const BUSES = [
  { name: 'Elec A', carrier: 'AC' },
  { name: 'H2 A', carrier: 'H2' },
]

function renderForm(item: { id: string; label: string; dropBusName?: string; dropPosition?: { x: number; y: number } }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  return render(
    <QueryClientProvider client={client}>
      <CreationForm item={item} />
    </QueryClientProvider>,
  )
}

/** The BusAutocomplete input rendered under a given field label. */
function busInputFor(label: string): HTMLInputElement {
  const wrapper = screen.getByText(label).parentElement as HTMLElement
  return wrapper.querySelector('input[type="text"]') as HTMLInputElement
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, creationItem: null })
})

describe('terminal prefill', () => {
  it('a Generator dropped on a bus opens with `bus` prefilled', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropBusName: 'Elec A' })
    expect(busInputFor('Attach to Bus *').value).toBe('Elec A')
  })

  it('an Electrolyzer dropped on a hydrogen bus leaves bus0 EMPTY', () => {
    // bus0 is filtered to non-h2 (CreationForm.tsx:133). Prefilling it with an
    // H2 bus would write a terminal the backend cannot use.
    renderForm({ id: 'electrolyzer', label: 'Electrolyzer', dropBusName: 'H2 A' })
    expect(busInputFor('Electricity bus (input) *').value).toBe('')
  })

  it('an Electrolyzer dropped on an electricity bus DOES prefill bus0', () => {
    renderForm({ id: 'electrolyzer', label: 'Electrolyzer', dropBusName: 'Elec A' })
    expect(busInputFor('Electricity bus (input) *').value).toBe('Elec A')
  })

  it('a bus name that is not in the network is not prefilled', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropBusName: 'Ghost' })
    expect(busInputFor('Attach to Bus *').value).toBe('')
  })

  it('a drop with no bus leaves the terminal empty', () => {
    renderForm({ id: 'thermal', label: 'Thermal' })
    expect(busInputFor('Attach to Bus *').value).toBe('')
  })

  it('a Bus dropped on a bus prefills nothing — it IS the terminal', () => {
    renderForm({ id: 'bus', label: 'Bus', dropBusName: 'Elec A' })
    // The Bus form has no bus field at all; the assertion is that rendering
    // does not throw and the name field holds the auto-generated name.
    expect((screen.getByDisplayValue(/^Bus \d+$/) as HTMLInputElement).value)
      .toMatch(/^Bus \d+$/)
  })
})
