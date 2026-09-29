import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn().mockResolvedValue({ saved: 'p', sites: 1 }) } }))

import SiteDraftPanel from './SiteDraftPanel'
import { useUIStore } from '../store/uiStore'
import { useSitesStore } from '../site3d/sitesStore'
import { fromLocal } from '../site3d/geo'
import type { LngLatTuple } from '../site3d/types'
import type { Bus } from '../api/types'

const ORIGIN = { lng: 6.83, lat: 53.44 }
const boundary: LngLatTuple[] = ([[-100, 100], [100, 100], [100, -100], [-100, -100]] as const)
  .map(([x, y]) => { const ll = fromLocal(ORIGIN, { x, y }); return [ll.lng, ll.lat] as LngLatTuple })
const far = fromLocal(ORIGIN, { x: 900, y: 0 })
const buses = [
  { name: 'inside-bus', x: ORIGIN.lng, y: ORIGIN.lat },
  { name: 'outside-bus', x: far.lng, y: far.lat },
  { name: 'unplaced', x: 0, y: 0 },
] as Bus[]

beforeEach(() => {
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'p', readOnly: false, readOnlyReason: 'writable', activeSiteId: null })
})

describe('SiteDraftPanel', () => {
  it('defaults the name to "Site n", pre-checks the buses inside, and never lists unplaced buses', () => {
    render(<SiteDraftPanel boundary={boundary} buses={buses} onDone={() => {}} />)
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('Site 1')
    expect((screen.getByRole('checkbox', { name: /inside-bus/ }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByRole('checkbox', { name: /outside-bus/ }) as HTMLInputElement).checked).toBe(false)
    expect(screen.queryByText('unplaced')).toBeNull()
  })

  it('Create writes the site to the store with the checked buses, sets it active, and reports it', () => {
    const onDone = vi.fn()
    render(<SiteDraftPanel boundary={boundary} buses={buses} onDone={onDone} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Campus' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /outside-bus/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    const doc = useSitesStore.getState().docFor('p')
    expect(doc.sites).toHaveLength(1)
    expect(doc.sites[0].name).toBe('Campus')
    expect(doc.sites[0].buses).toEqual(['inside-bus', 'outside-bus'])
    expect(doc.sites[0].origin.lng).toBeCloseTo(ORIGIN.lng, 6)
    expect(useUIStore.getState().activeSiteId).toBe(doc.sites[0].id)
    expect(localStorage.getItem('network-diagram:active-site:p')).toBe(doc.sites[0].id)
    expect(onDone).toHaveBeenCalledWith(doc.sites[0])
  })

  it('Create is disabled with the read-only reason as its title, and writes nothing', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    render(<SiteDraftPanel boundary={boundary} buses={buses} onDone={() => {}} />)
    const btn = screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(btn.title.length).toBeGreaterThan(0)
    fireEvent.click(btn)
    expect(useSitesStore.getState().docFor('p').sites).toHaveLength(0)
  })

  it('edit mode keeps the id and origin and updates name and buses', () => {
    render(<SiteDraftPanel boundary={boundary} buses={buses} onDone={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    const created = useSitesStore.getState().docFor('p').sites[0]
    const onDone = vi.fn()
    render(<SiteDraftPanel boundary={created.boundary} existing={created} buses={buses} onDone={onDone} />)
    const inputs = screen.getAllByRole('textbox')
    fireEvent.change(inputs[inputs.length - 1], { target: { value: 'Renamed' } })
    fireEvent.click(screen.getAllByRole('checkbox', { name: /outside-bus/ }).at(-1)!)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    const doc = useSitesStore.getState().docFor('p')
    expect(doc.sites).toHaveLength(1)
    expect(doc.sites[0].id).toBe(created.id)
    expect(doc.sites[0].name).toBe('Renamed')
    expect(doc.sites[0].buses).toEqual(['inside-bus', 'outside-bus'])
    expect(doc.sites[0].origin).toEqual(created.origin)
  })

  it('Cancel reports null and writes nothing', () => {
    const onDone = vi.fn()
    render(<SiteDraftPanel boundary={boundary} buses={buses} onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onDone).toHaveBeenCalledWith(null)
    expect(useSitesStore.getState().docFor('p').sites).toHaveLength(0)
  })
})
