import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SiteOverlay from './SiteOverlay'
import { useUIStore } from '../store/uiStore'
import type { Site } from '../site3d/types'

const site: Site = { id: 'a', name: 'Campus', buses: ['B1'], boundary: [[0, 0], [0.01, 0], [0.01, 0.01]], origin: { lng: 0.005, lat: 0.003 }, placements: {} }
const fit = { landM2: 178_000, plotM2: 113_000, over: true, outside: ['Generator:PV field'] }

beforeEach(() => { useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' }) })

describe('SiteOverlay', () => {
  it('shows the fit check in red when over, the outside count, and enables Arrange / Reset', () => {
    const onArrange = vi.fn(), onReset = vi.fn()
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={5} fit={fit} unplacedMembers={[]} selectedPlacedKey="Generator:PV field" canArrange onArrange={onArrange} onResetPlacement={onReset} />)
    expect(screen.getByTestId('fit-status').textContent).toBe('land 17.8 ha · plot 11.3 ha · does not fit')
    expect(screen.getByTestId('fit-status').className).toContain('text-accent')
    expect(screen.getByText('· 1 outside')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reset placement' }))
    expect(onArrange).toHaveBeenCalledTimes(1)
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('Reset is disabled without a placed selection', () => {
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={1} fit={{ ...fit, over: false, outside: [] }} unplacedMembers={['B2']} selectedPlacedKey={null} canArrange onArrange={() => {}} onResetPlacement={() => {}} />)
    expect((screen.getByRole('button', { name: 'Reset placement' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/1 member bus not placed/)).toBeTruthy()
  })

  it('read-only disables Arrange and Reset with the reason as their title, and nothing fires', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    const onArrange = vi.fn()
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={1} fit={fit} unplacedMembers={[]} selectedPlacedKey="x" canArrange onArrange={onArrange} onResetPlacement={() => {}} />)
    const arrange = screen.getByRole('button', { name: 'Arrange' }) as HTMLButtonElement
    expect(arrange.disabled).toBe(true)
    expect(arrange.title.length).toBeGreaterThan(0)
    expect(arrange.title).not.toContain('packed position')
    fireEvent.click(arrange)
    expect(onArrange).not.toHaveBeenCalled()
  })

  it('picking a site reports its id', () => {
    const other: Site = { ...site, id: 'b', name: 'Other' }
    const onPick = vi.fn()
    render(<SiteOverlay sites={[site, other]} site={site} onPickSite={onPick} assetCount={0} fit={{ ...fit, over: false, outside: [] }} unplacedMembers={[]} selectedPlacedKey={null} canArrange onArrange={() => {}} onResetPlacement={() => {}} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Site' }), { target: { value: 'b' } })
    expect(onPick).toHaveBeenCalledWith('b')
  })
})

describe('SiteOverlay — Arrange while loading', () => {
  it('is disabled until every component list has loaded', () => {
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={0} fit={{ ...fit, over: false, outside: [] }} unplacedMembers={[]} selectedPlacedKey={null} canArrange={false} onArrange={() => {}} onResetPlacement={() => {}} />)
    const b = screen.getByRole('button', { name: 'Arrange' }) as HTMLButtonElement
    expect(b.disabled).toBe(true)
    expect(b.title).toMatch(/load/)
  })
})
