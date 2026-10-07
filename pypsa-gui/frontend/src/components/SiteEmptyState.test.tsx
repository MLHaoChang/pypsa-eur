import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn().mockResolvedValue({ saved: 'p', sites: 1 }) } }))

import SiteEmptyState from './SiteEmptyState'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { centroid } from '../site3d/boundary'
import { toLocal } from '../site3d/geo'
import type { Bus } from '../api/types'

const buses = [{ name: 'unplaced', x: 0, y: 0 }, { name: 'B1', x: 6.83, y: 53.44 }, { name: 'B2', x: 6.9, y: 53.5 }] as Bus[]
const boundsFor = () => ({ x0: 100, x1: 300, y0: -50, y1: 50 })

beforeEach(() => {
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'p', readOnly: false, readOnlyReason: 'writable', activeSiteId: null })
})

describe('SiteEmptyState', () => {
  it('explains itself when nothing is placed', () => {
    render(<SiteEmptyState buses={[buses[0]]} boundsFor={boundsFor} />)
    expect(screen.getByText(/No placed bus/)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('creates a site around the chosen bus from the default rectangle and makes it active', () => {
    render(<SiteEmptyState buses={buses} boundsFor={boundsFor} />)
    fireEvent.change(screen.getByRole('combobox', { name: /Bus for the new site/ }), { target: { value: 'B2' } })
    fireEvent.click(screen.getByRole('button', { name: /Create a site around this bus/ }))
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
    expect(useUIStore.getState().activeSiteId).toBe(s.id)
  })

  it('is disabled when read-only and writes nothing', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'solving' })
    render(<SiteEmptyState buses={buses} boundsFor={boundsFor} />)
    const btn = screen.getByRole('button', { name: /Create a site/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title.length).toBeGreaterThan(0)
    fireEvent.click(btn)
    expect(useSitesStore.getState().docFor('p').sites).toHaveLength(0)
  })
})
