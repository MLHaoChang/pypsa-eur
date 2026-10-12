// Visual-layers plan 3 S5 (UX IA-6 / M7): the Site map card under the hub-design
// step — only when a site exists, with the thumbnail when one was captured,
// with the "no picture yet" line when not, and Open in 3D either way.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('../../../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn() } }))

import { useUIStore } from '../../../store/uiStore'
import { useSitesStore } from '../../../site3d/sitesStore'
import { useThumbnailStore } from '../../../site3d/thumbnailStore'
import { readActiveSite } from '../../../site3d/activeSite'
import type { Site } from '../../../site3d/types'
import { SiteThumbnailCard } from './SiteThumbnailCard'

const site: Site = { id: 'site_a', name: 'Campus', buses: ['B1'], boundary: [[0, 0], [0.01, 0], [0.01, 0.01]], origin: { lng: 0.005, lat: 0.003 }, placements: {} }
const other: Site = { ...site, id: 'site_b', name: 'Annex' }

beforeEach(() => {
  useSitesStore.getState().resetForTests()
  useThumbnailStore.getState().resetForTests()
  useUIStore.setState({ currentProject: 'Demo', activeSiteId: null, canvasView: 'blank', activeSlidePanel: 'hubDesign', readOnly: false, readOnlyReason: 'writable' })
})
afterEach(() => cleanup())

const seed = (sites: Site[]) => useSitesStore.setState(s => ({ docs: { ...s.docs, Demo: { version: 1, sites } }, loaded: { ...s.loaded, Demo: true } }))

describe('SiteThumbnailCard', () => {
  it('renders nothing without a site, or without a project', () => {
    const { container, rerender } = render(<SiteThumbnailCard />)
    expect(container.innerHTML).toBe('')
    seed([site])
    useUIStore.setState({ currentProject: null })
    rerender(<SiteThumbnailCard />)
    expect(container.innerHTML).toBe('')
  })

  it('shows the site’s thumbnail, versioned by the capture count, with Open in 3D', () => {
    seed([site])
    render(<SiteThumbnailCard />)
    expect(screen.getByTestId('hub-site-thumbnail').textContent).toContain('Site map · Campus')
    const img = screen.getByTestId('hub-site-thumbnail-img') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/api/projects/Demo/sites/site_a/thumbnail?v=0')
    expect(img.alt).toContain('Campus')
    fireEvent.load(img)
    expect(screen.queryByTestId('hub-site-thumbnail-none')).toBeNull()
  })

  it('a 404 (no capture yet) shows the "no picture yet" line and keeps Open in 3D', () => {
    seed([site])
    render(<SiteThumbnailCard />)
    fireEvent.error(screen.getByTestId('hub-site-thumbnail-img'))
    expect(screen.getByTestId('hub-site-thumbnail-none').textContent).toMatch(/No picture yet/)
    expect(screen.queryByTestId('hub-site-thumbnail-img')).toBeNull()
    expect(screen.getByTestId('hub-site-open-3d')).toBeTruthy()
  })

  it('a capture in this session re-reads the image (the version bumps) after a 404', () => {
    seed([site])
    render(<SiteThumbnailCard />)
    fireEvent.error(screen.getByTestId('hub-site-thumbnail-img'))
    expect(screen.getByTestId('hub-site-thumbnail-none')).toBeTruthy()
    act(() => { useThumbnailStore.getState().bump('Demo', 'site_a') })
    const img = screen.getByTestId('hub-site-thumbnail-img') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/api/projects/Demo/sites/site_a/thumbnail?v=1')
  })

  it('shows the active site when one is chosen, else the first', () => {
    seed([site, other])
    const { rerender } = render(<SiteThumbnailCard />)
    expect(screen.getByTestId('hub-site-thumbnail').textContent).toContain('Campus')
    useUIStore.setState({ activeSiteId: 'site_b' })
    rerender(<SiteThumbnailCard />)
    expect(screen.getByTestId('hub-site-thumbnail').textContent).toContain('Annex')
  })

  it('Open in 3D makes the site active, closes the hub panel and switches to the site view', () => {
    seed([site, other])
    useUIStore.setState({ activeSiteId: 'site_b' })
    render(<SiteThumbnailCard />)
    fireEvent.click(screen.getByTestId('hub-site-open-3d'))
    const s = useUIStore.getState()
    expect(s.activeSiteId).toBe('site_b')
    expect(readActiveSite('Demo')).toBe('site_b')
    expect(s.canvasView).toBe('site')
    expect(s.activeSlidePanel).toBeNull()
  })
})
