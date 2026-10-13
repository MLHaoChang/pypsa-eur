// Visual-layers plan 3 S4: the readiness checklist and Generate 3D site.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn().mockResolvedValue({ saved: 'p', sites: 1 }) } }))

import SiteEmptyState from './SiteEmptyState'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { centroid } from '../site3d/boundary'
import { toLocal } from '../site3d/geo'
import type { SiteObject } from '../site3d/layout'
import type { Bus } from '../api/types'

const buses = [{ name: 'unplaced', x: null, y: null }, { name: 'B1', x: 6.83, y: 53.44 }, { name: 'B2', x: 6.9, y: 53.5 }] as unknown as Bus[]
const branches = [{ bus0: 'B1', bus1: 'B2' }]
const obj = (type: SiteObject['type'], name: string, origin: [number, number], elevation?: number) =>
  ({ type, name, origin, heading: 0, elevation } as unknown as SiteObject)
const layoutFor = () => ({
  bounds: { x0: 100, x1: 300, y0: -50, y1: 50 },
  objects: [obj('Bus', 'B2', [0, 0]), obj('Generator', 'pv', [150, 20]), obj('Generator', 'roof', [150, 20], 12)],
})

beforeEach(() => {
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'p', readOnly: false, readOnlyReason: 'writable', activeSiteId: null, canvasView: 'site', canvasMode: 'select' })
})

describe('SiteEmptyState', () => {
  it('lists what is missing, as buttons to where it is fixed, and offers no Generate', () => {
    render(<SiteEmptyState buses={[buses[0]]} branches={[]} layoutFor={layoutFor} />)
    const list = screen.getByRole('list', { name: 'Site readiness' })
    expect(list.querySelectorAll('li')).toHaveLength(3)
    expect(screen.getByTestId('readiness-coordinates').dataset.met).toBe('false')
    expect(screen.getByTestId('readiness-branch').dataset.met).toBe('false')
    expect(screen.getByTestId('readiness-boundary').dataset.met).toBe('false')
    expect(screen.queryByTestId('generate-site')).toBeNull()
    fireEvent.click(screen.getAllByRole('button', { name: 'Place a bus on the Satellite map' })[0])
    expect(useUIStore.getState().canvasView).toBe('satellite')
  })

  it('the connect line sends the user to the connect tool on the schematic', () => {
    render(<SiteEmptyState buses={buses} branches={[]} layoutFor={layoutFor} />)
    expect(screen.getByTestId('readiness-coordinates').dataset.met).toBe('true')
    expect(screen.getByTestId('readiness-branch').dataset.met).toBe('false')
    expect(screen.queryByTestId('generate-site')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Connect two buses' }))
    expect(useUIStore.getState().canvasMode).toBe('connect')
    expect(useUIStore.getState().canvasView).toBe('blank')
  })

  it('shows "Checking the model…" instead of a half-read checklist while lists load', () => {
    render(<SiteEmptyState buses={buses} branches={[]} layoutFor={layoutFor} loading />)
    expect(screen.getByText('Checking the model…')).toBeTruthy()
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('every line met: Generate draws the default rectangle around the chosen bus, arranges by the rules and makes the site active', () => {
    render(<SiteEmptyState buses={buses} branches={branches} layoutFor={layoutFor} />)
    expect(screen.getByRole('list', { name: 'Site readiness' }).querySelectorAll('[data-met="false"]')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /Place a bus/ })).toBeNull()
    fireEvent.change(screen.getByRole('combobox', { name: /Bus for the new site/ }), { target: { value: 'B2' } })
    fireEvent.click(screen.getByTestId('generate-site'))
    const doc = useSitesStore.getState().docFor('p')
    expect(doc.sites).toHaveLength(1)
    const s = doc.sites[0]
    expect(s.buses).toEqual(['B2'])
    expect(s.boundary).toHaveLength(4)
    // The rectangle is centred on the packed bounds' centre (200 m east of the bus), so the
    // site origin (its centroid) is east of the bus — the bus is not the origin.
    const c = centroid(s.boundary)
    const off = toLocal({ lng: 6.9, lat: 53.5 }, c)
    expect(off.x).toBeCloseTo(200, 0)
    expect(off.y).toBeCloseTo(0, 0)
    // Arranged at birth: every ground object has a placement in the SITE frame
    // (the bus 200 m west of the origin), the rooftop object none.
    expect(Object.keys(s.placements).sort()).toEqual(['Bus:B2', 'Generator:pv'])
    expect(s.placements['Bus:B2'].x).toBeCloseTo(-200, 0)
    expect(s.placements['Generator:pv'].x).toBeCloseTo(-50, 0)
    expect(s.placements['Generator:pv'].y).toBeCloseTo(20, 0)
    expect(useUIStore.getState().activeSiteId).toBe(s.id)
  })

  it('is disabled when read-only and writes nothing', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'solving' })
    render(<SiteEmptyState buses={buses} branches={branches} layoutFor={layoutFor} />)
    const btn = screen.getByTestId('generate-site') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title.length).toBeGreaterThan(0)
    fireEvent.click(btn)
    expect(useSitesStore.getState().docFor('p').sites).toHaveLength(0)
  })
})
