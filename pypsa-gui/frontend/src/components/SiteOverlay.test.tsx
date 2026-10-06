import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SiteOverlay from './SiteOverlay'
import { useUIStore } from '../store/uiStore'
import type { Site } from '../site3d/types'
import { confirmToast } from '../utils/toasts'

// `<Toaster/>` lives in App.tsx, so a confirmToast never renders under this
// harness — the assertion is on the call (as BottomPanel.test does).
vi.mock('../utils/toasts', () => ({ confirmToast: vi.fn() }))

const site: Site = { id: 'a', name: 'Campus', buses: ['B1'], boundary: [[0, 0], [0.01, 0], [0.01, 0.01]], origin: { lng: 0.005, lat: 0.003 }, placements: {} }
const fit = { landM2: 178_000, plotM2: 113_000, over: true, outside: ['Generator:PV field'] }

beforeEach(() => { useUIStore.setState({ readOnly: false, readOnlyReason: 'writable', siteSizing: 'installed' }) })

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

describe('SiteOverlay — Arrange all (S2)', () => {
  beforeEach(() => { vi.mocked(confirmToast).mockReset() })
  it('is offered only when the canvas provides it, asks first naming the moved positions, and runs on confirm', () => {
    const onArrangeAll = vi.fn()
    const { unmount } = render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={1} fit={fit} unplacedMembers={[]} selectedPlacedKey={null} canArrange onArrange={() => {}} onResetPlacement={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Arrange all' })).toBeNull()
    unmount()
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={1} fit={fit} unplacedMembers={[]} selectedPlacedKey={null} canArrange onArrange={() => {}} onArrangeAll={onArrangeAll} placedCount={3} onResetPlacement={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Arrange all' }))
    expect(onArrangeAll).not.toHaveBeenCalled()
    expect(confirmToast).toHaveBeenCalledTimes(1)
    const [message, onConfirm, opts] = vi.mocked(confirmToast).mock.calls[0]
    expect(message).toMatch(/3 positions you moved/)
    expect(opts).toMatchObject({ confirmLabel: 'Arrange all', danger: true })
    onConfirm()
    expect(onArrangeAll).toHaveBeenCalledTimes(1)
  })
  it('is disabled while read-only and while the network loads', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={1} fit={fit} unplacedMembers={[]} selectedPlacedKey={null} canArrange onArrange={() => {}} onArrangeAll={() => {}} onResetPlacement={() => {}} />)
    const b = screen.getByRole('button', { name: 'Arrange all' }) as HTMLButtonElement
    expect(b.disabled).toBe(true)
    fireEvent.click(b)
    expect(confirmToast).not.toHaveBeenCalled()
  })
})

describe('SiteOverlay — Arrange while loading', () => {
  it('is disabled until every component list has loaded', () => {
    render(<SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={0} fit={{ ...fit, over: false, outside: [] }} unplacedMembers={[]} selectedPlacedKey={null} canArrange={false} onArrange={() => {}} onResetPlacement={() => {}} />)
    const b = screen.getByRole('button', { name: 'Arrange' }) as HTMLButtonElement
    expect(b.disabled).toBe(true)
    expect(b.title).toMatch(/load/)
  })

  describe('Sized: as built / optimised (spec E6)', () => {
    const renderWith = (dispatchFresh: boolean) => render(
      <SiteOverlay sites={[site]} site={site} onPickSite={() => {}} assetCount={1} fit={{ ...fit, over: false, outside: [] }} unplacedMembers={[]} selectedPlacedKey={null} canArrange onArrange={() => {}} onResetPlacement={() => {}} dispatchFresh={dispatchFresh} />,
    )
    it('is shown only while dispatch is fresh', () => {
      const { unmount } = renderWith(false)
      expect(screen.queryByRole('button', { name: /optimised/i })).toBeNull()
      unmount()
      renderWith(true)
      expect(screen.getByRole('button', { name: /optimised/i })).toBeTruthy()
    })
    it('toggling sets siteSizing, and the fit line says "(optimised)" in optimised mode', () => {
      renderWith(true)
      const optimised = screen.getByRole('button', { name: /optimised/i })
      expect(optimised.getAttribute('aria-pressed')).toBe('false')
      expect(screen.getByTestId('fit-status').textContent).not.toContain('(optimised)')
      fireEvent.click(optimised)
      expect(useUIStore.getState().siteSizing).toBe('optimised')
      expect(screen.getByRole('button', { name: /optimised/i }).getAttribute('aria-pressed')).toBe('true')
      expect(screen.getByTestId('fit-status').textContent).toContain('(optimised)')
      fireEvent.click(screen.getByRole('button', { name: /as built/i }))
      expect(useUIStore.getState().siteSizing).toBe('installed')
    })
    it('a stale dispatch hides the switch and drops "(optimised)" although siteSizing is still optimised', () => {
      useUIStore.setState({ siteSizing: 'optimised' })
      renderWith(false)
      expect(screen.getByTestId('fit-status').textContent).not.toContain('(optimised)')
      expect(useUIStore.getState().siteSizing).toBe('optimised')
    })
    it('stays enabled when read-only (it changes the view, not the network)', () => {
      useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
      renderWith(true)
      const optimised = screen.getByRole('button', { name: /optimised/i }) as HTMLButtonElement
      expect(optimised.disabled).toBe(false)
      fireEvent.click(optimised)
      expect(useUIStore.getState().siteSizing).toBe('optimised')
    })
  })
})
