// SC-01b rule 2: the Site 3D empty state shows the entry state and one link to
// the one "Create 3D site" in Project & site; it is no creation control. After
// Create it shows the New 3D site panel (numeric boundary, Save site).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const flowState: { flow: unknown; isPending: boolean } = { flow: null, isPending: false }
vi.mock('../hooks/useProjectFlow', () => ({ useProjectFlow: () => flowState }))
vi.mock('../api/siteScene', async (orig) => ({
  ...(await orig<typeof import('../api/siteScene')>()),
  siteSceneApi: { getAnchor: vi.fn() },
}))
vi.mock('../site3d/sceneActions', async (orig) => ({
  ...(await orig<typeof import('../site3d/sceneActions')>()),
  createSite: vi.fn(),
}))

import { siteSceneApi } from '../api/siteScene'
import { createSite } from '../site3d/sceneActions'
import { useUIStore } from '../store/uiStore'
import { useSiteCreation } from '../site3d/siteCreation'
import { useSitesStore } from '../site3d/sitesStore'
import SiteEmptyState from './SiteEmptyState'

const view = () => render(<QueryClientProvider client={new QueryClient()}><SiteEmptyState /></QueryClientProvider>)
const entry = (e: object) => { flowState.flow = { scene: { entry: e, anchor_moved: [], actions: [] } } }
const anchored = { version: 1 as const, revision: 1, anchor: { lat: 48, lng: 11, source: 'manual' as const, confirmed: true } }

beforeEach(() => {
  vi.clearAllMocks()
  useSitesStore.getState().resetForTests()
  useSiteCreation.setState({ open: false })
  useUIStore.setState({ currentProject: 'p', uiMode: 'guided', canvasView: 'site', readOnly: false, readOnlyReason: 'writable',
    activeSlidePanel: null, activeSection: null, flowReturn: null, fieldDrawer: null })
  vi.mocked(siteSceneApi.getAnchor).mockResolvedValue(anchored)
})

describe('Site 3D empty state', () => {
  it('has no creation button of its own, only the entry state and its link', () => {
    entry({ label: 'Create 3D site', enabled: false, blocker: { code: 'site_anchor_missing', reason: 'Add site coordinates to create a 3D site', link: '›', home: {} } })
    view()
    expect(screen.getByTestId('site-empty-entry-state').textContent).toBe('Add site coordinates to create a 3D site')
    expect(screen.queryByText('Generate 3D site')).toBeNull()
    expect(screen.queryByText(/New site/)).toBeNull()
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  it('the link opens the entry in place in Guided, with Back to Site 3D', () => {
    entry({ label: 'Create 3D site', enabled: true, blocker: null })
    view()
    expect(screen.getByTestId('site-empty-entry-state').textContent).toBe('Next: Create 3D site')
    fireEvent.click(screen.getByTestId('site-empty-entry-link'))
    expect(useUIStore.getState().activeSection).toBe('project')
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().flowReturn?.label).toBe('Site 3D')
  })

  it('unsaved project: the save-first reason', () => {
    useUIStore.setState({ currentProject: null })
    view()
    expect(screen.getByTestId('site-empty-entry-state').textContent).toBe('Unsaved project: save it to keep a 3D site')
  })
})

describe('New 3D site', () => {
  beforeEach(() => useSiteCreation.setState({ open: true }))

  it('saves an empty Site around the coordinates and opens Generate', async () => {
    vi.mocked(createSite).mockResolvedValue({ state: 'unadopted', site_id: 'x', digest: '' })
    view()
    await screen.findByTestId('site-create-save')
    fireEvent.click(screen.getByTestId('site-create-save'))
    await waitFor(() => expect(createSite).toHaveBeenCalled())
    const site = vi.mocked(createSite).mock.calls[0][2]
    expect(site.buses).toEqual([])
    expect(site.boundary).toHaveLength(4)
    await waitFor(() => expect(useUIStore.getState().sitePart).toBe('generate'))
    expect(useSiteCreation.getState().open).toBe(false)
  })

  it('an out-of-range side disables Save with a visible reason', async () => {
    view()
    fireEvent.change(await screen.findByTestId('site-create-width'), { target: { value: '5' } })
    expect((screen.getByTestId('site-create-save') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Enter 10–5,000 m')).toBeTruthy()
  })

  it('without confirmed coordinates: the reason and a link to the field', async () => {
    vi.mocked(siteSceneApi.getAnchor).mockResolvedValue({ version: 1, revision: 0, anchor: null })
    view()
    expect((await screen.findByTestId('site-create-no-anchor')).textContent).toBe('Add site coordinates to create a 3D site › Site coordinates')
  })
})
