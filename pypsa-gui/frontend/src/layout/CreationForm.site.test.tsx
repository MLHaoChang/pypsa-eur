// A palette item dropped on the 3D site view (design D16, plan Tasks 4.3–4.4):
// the terminal is prefilled with the site's primary bus and restricted to
// its members, a dropped bus is seeded with the ground point's coordinates,
// and on success the asset is placed where it landed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'
import { useSitesStore } from '../site3d/sitesStore'
import { fromLocal } from '../site3d/geo'
import type { Site } from '../site3d/types'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn().mockResolvedValue({ saved: 'Demo', sites: 1 }) } }))
vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      createBus: vi.fn(async (b: { name: string }) => ({ name: b.name })),
      createGenerator: vi.fn(async (g: { name: string }) => ({ name: g.name })),
      createStorageUnit: vi.fn(async (g: { name: string }) => ({ name: g.name })),
      createLink: vi.fn(),
    },
  }
})

const BUSES = [
  { name: 'Elec A', carrier: 'AC' },
  { name: 'Elec B', carrier: 'AC' },
  { name: 'Outsider', carrier: 'AC' },
  { name: 'H2 A', carrier: 'H2' },
]
const ORIGIN = { lng: 6.83, lat: 53.44 }
const SITE: Site = {
  id: 'site_a', name: 'Campus', buses: ['Elec B', 'Elec A', 'H2 A'],
  boundary: [[6.82, 53.45], [6.84, 53.45], [6.84, 53.43], [6.82, 53.43]], origin: ORIGIN, placements: {},
}

function renderForm(item: Parameters<typeof CreationForm>[0]['item']) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  return render(<QueryClientProvider client={client}><CreationForm item={item} /></QueryClientProvider>)
}
function fieldInput(labelRe: RegExp): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label')).find(l => labelRe.test(l.textContent ?? ''))
  if (!label) throw new Error(`no field labelled ${labelRe}`)
  const scope = (label.querySelector('input') ? label : label.parentElement) as HTMLElement
  return scope.querySelector('input') as HTMLInputElement
}

beforeEach(() => {
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'Demo', creationItem: null, readOnly: false, readOnlyReason: 'writable' })
  useSitesStore.getState().upsertSite('Demo', SITE)
})
afterEach(() => { vi.restoreAllMocks(); useUIStore.setState({ currentProject: null, creationItem: null }) })

const drop = { siteId: 'site_a', ground: { x: 120, y: -40 } }

describe('a drop onto the 3D site', () => {
  it('prefills the terminal with the primary bus (first member), not the first bus in the network', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropSite: drop })
    expect(fieldInput(/^Attach to Bus/).value).toBe('Elec B')
  })

  it('an electrolyser prefills its electricity side with the primary bus and leaves the H₂ side free', () => {
    renderForm({ id: 'electrolyzer', label: 'Electrolyzer', dropSite: drop })
    expect(fieldInput(/^Electricity bus/).value).toBe('Elec B')
    expect(fieldInput(/^H₂ bus/).value).toBe('')
  })

  it('a dropped bus is seeded with the ground point\'s longitude and latitude', () => {
    renderForm({ id: 'bus', label: 'Bus', dropSite: drop })
    const ll = fromLocal(ORIGIN, drop.ground)
    expect(Number(fieldInput(/^Longitude/).value)).toBeCloseTo(ll.lng, 5)
    expect(Number(fieldInput(/^Latitude/).value)).toBeCloseTo(ll.lat, 5)
  })

  it('on success the asset is placed at the drop point', async () => {
    renderForm({ id: 'battery', label: 'Battery', dropSite: drop })
    fireEvent.change(fieldInput(/^Name/), { target: { value: 'BESS new' } })
    await userEvent.click(screen.getByText('Add to Network'))
    await waitFor(() => expect(useSitesStore.getState().docFor('Demo').sites[0].placements['StorageUnit:BESS new']).toEqual({ x: 120, y: -40, heading: 0 }))
    expect(useUIStore.getState().creationItem).toBeNull()
  })

  it('a dropped bus joins the site and is placed', async () => {
    renderForm({ id: 'bus', label: 'Bus', dropSite: drop })
    fireEvent.change(fieldInput(/^Name/), { target: { value: 'New bus' } })
    await userEvent.click(screen.getByText('Add to Network'))
    await waitFor(() => expect(useSitesStore.getState().docFor('Demo').sites[0].buses).toEqual(['Elec B', 'Elec A', 'H2 A', 'New bus']))
    expect(useSitesStore.getState().docFor('Demo').sites[0].placements['Bus:New bus']).toEqual({ x: 120, y: -40, heading: 0 })
  })

  it('a drop naming a site that no longer exists behaves like a plain palette click', () => {
    renderForm({ id: 'thermal', label: 'Thermal', dropSite: { siteId: 'gone', ground: { x: 0, y: 0 } } })
    expect(fieldInput(/^Attach to Bus/).value).toBe('')
  })
})
