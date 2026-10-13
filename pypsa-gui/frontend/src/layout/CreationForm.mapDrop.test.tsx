// A palette drop on the map carries a coordinate (plan M4, reverting D26):
// a dropped Bus is placed there, any other asset dropped on empty map gets
// the nearest PLACED bus as its terminal, and a drop on a marker still wins.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore, type CreationRequest } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm, { nearestPlacedBus } from './CreationForm'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, getCarriers: vi.fn(async () => []), createBus: vi.fn(), createGenerator: vi.fn() } }
})

const BUSES = [
  { name: 'Far', carrier: 'AC', x: 7.0, y: 53.0 },
  { name: 'Near', carrier: 'AC', x: 6.001, y: 53.0 },
  { name: 'Unplaced', carrier: 'AC', x: 0, y: 0 },
  { name: 'H2 near', carrier: 'H2', x: 6.0005, y: 53.0 },
]

function renderForm(item: CreationRequest) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  return render(<QueryClientProvider client={client}><CreationForm item={item} /></QueryClientProvider>)
}

function inputUnder(label: string): HTMLInputElement {
  return (screen.getByText(label).parentElement as HTMLElement).querySelector('input') as HTMLInputElement
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo', creationItem: null }) })
afterEach(() => { vi.restoreAllMocks(); useUIStore.setState({ currentProject: null, creationItem: null }) })

describe('nearestPlacedBus', () => {
  it('ignores unplaced buses and picks the closest placed one', () => {
    expect(nearestPlacedBus({ lat: 53.0, lng: 6.0 }, BUSES)).toBe('H2 near')
    expect(nearestPlacedBus({ lat: 53.0, lng: 6.0 }, BUSES.filter(b => b.carrier === 'AC'))).toBe('Near')
    expect(nearestPlacedBus({ lat: 53.0, lng: 6.0 }, [BUSES[2]])).toBeNull()
  })
})

describe('CreationForm — map drops', () => {
  it('a Bus dropped on the map opens with its longitude and latitude seeded', () => {
    renderForm({ id: 'bus', label: 'Bus', dropLatLng: { lat: 53.4412345678, lng: 6.8321 } })
    expect(inputUnder('Longitude').value).toBe('6.8321')
    expect(inputUnder('Latitude').value).toBe('53.441235')
  })

  it('a Generator dropped on empty map gets the nearest placed bus that passes the filter', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropLatLng: { lat: 53.0, lng: 6.0 } })
    const wrapper = screen.getByText('Attach to Bus *').parentElement as HTMLElement
    expect((wrapper.querySelector('input[type="text"]') as HTMLInputElement).value).toBe('H2 near')
  })

  it('an Electrolyzer dropped on empty map skips the H2 bus its input cannot use', () => {
    renderForm({ id: 'electrolyzer', label: 'Electrolyzer', dropLatLng: { lat: 53.0, lng: 6.0 } })
    const wrapper = screen.getByText('Electricity bus (input) *').parentElement as HTMLElement
    expect((wrapper.querySelector('input[type="text"]') as HTMLInputElement).value).toBe('Near')
  })

  it('a drop on a marker keeps that bus, even when another is nearer the pointer', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropLatLng: { lat: 53.0, lng: 6.0 }, dropBusName: 'Far' })
    const wrapper = screen.getByText('Attach to Bus *').parentElement as HTMLElement
    expect((wrapper.querySelector('input[type="text"]') as HTMLInputElement).value).toBe('Far')
  })
})
