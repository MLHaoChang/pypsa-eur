import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BuildingInputs, Proposal, SceneBuilding, SiteScene } from '../api/siteScene'

const flowState: { flow: unknown; isPending: boolean } = { flow: null, isPending: false }
vi.mock('../hooks/useProjectFlow', () => ({ useProjectFlow: () => flowState }))
vi.mock('./sceneActions', async (orig) => ({
  ...(await orig<typeof import('./sceneActions')>()),
  propose: vi.fn(), applyProposal: vi.fn(), discardProposal: vi.fn(),
}))
vi.mock('../api/siteScene', async (orig) => ({
  ...(await orig<typeof import('../api/siteScene')>()),
  siteSceneApi: { getScene: vi.fn() },
}))

import { siteSceneApi } from '../api/siteScene'
import { applyProposal, discardProposal, propose } from './sceneActions'
import { useUIStore } from '../store/uiStore'
import { useSceneProposal } from './useSiteScene'
import { rectangleLocal } from './nativeSite'
import SiteScenePanel from './SiteScenePanel'
import type { Site } from './types'

const site: Site = { id: 'campus', name: 'Campus', buses: [], origin: { lng: 11, lat: 48 }, placements: {},
  boundary: [[10.999, 47.999], [11.001, 47.999], [11.001, 48.001], [10.999, 48.001]] }
const osm: SceneBuilding = { id: 'osm:7', source: { namespace: 'osm', source_id: '7' }, footprint: [[0, 0], [10, 0], [10, 10], [0, 10]],
  height_m: 6, height_evidence: 'fallback', levels: null }
const gf: SceneBuilding = { id: 'greenfield:b1', source: { namespace: 'greenfield', source_id: 'b1' },
  footprint: rectangleLocal({ x: 5, y: 5 }, 20, 10, 0).map(p => [p.x, p.y]), height_m: 9, height_evidence: 'greenfield', levels: null }
const enabled = (buildings: SceneBuilding[]): SiteScene => ({ state: 'enabled', site_id: 'campus', revision: 2, generation: 'g',
  site: {}, buildings, asset_footprints: {}, last_receipt: null, unbound_keys: [] })
const proposal: Proposal = { id: 'p-1', site_id: 'campus', action: 'generate_native', base_revision: 2, input_hash: 'h', status: 'ready',
  preview: { buildings: [osm], stats: { buildings: 1, triangles: 16, bytes: 300 } } }
const available = (name: string) => ({ action: name, available: true, blocker: null, required_inputs: [], computes: '' })
const view = () => render(<QueryClientProvider client={new QueryClient()}><SiteScenePanel site={site} contextBuildings={[]} /></QueryClientProvider>)

beforeEach(() => {
  vi.clearAllMocks()
  useSceneProposal.setState({ proposal: null })
  useUIStore.setState({ currentProject: 'p', sitePart: 'generate', sitePartSelection: null, readOnly: false, readOnlyReason: 'writable', uiMode: 'guided' })
  flowState.flow = { scene: { entry: {}, anchor_moved: [], actions: [available('generate_native'), {
    action: 'assign_native', available: false, required_inputs: [], computes: '',
    blocker: { code: 'no_model_targets', reason: 'Buildings only: configure model elements to map them', link: '›',
      home: { guided: { section: 'loads', drawer: null, open: 'request_then_scroll' }, expert: { table: 'Loads' } } } }] } }
  vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([]))
})

describe('Site 3D scene panel', () => {
  it('shows the four parts as one row, not a stepper', () => {
    view()
    expect(screen.getAllByRole('tab').map(t => t.textContent)).toEqual(['Map', 'Generate', 'Model', 'Results'])
    expect(screen.getByRole('tablist').textContent).not.toContain('·')
    fireEvent.click(screen.getByTestId('site-part-model'))
    expect(screen.getByTestId('scene-blocker-assign_native').textContent).toBe('Buildings only: configure model elements to map them › Loads')
  })

  it('Model: with assign_native available on an enabled scene, the attachments panel replaces the blocker', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([osm]))
    const flow = flowState.flow as { scene: { actions: { action: string }[] } }
    flow.scene.actions = flow.scene.actions.map(a => (a.action === 'assign_native' ? available('assign_native') : a))
    useUIStore.setState({ sitePart: 'model' })
    view()
    await screen.findByTestId('association-panel')
    expect(screen.queryByTestId('scene-blocker-assign_native')).toBeNull()
    expect(screen.getByTestId('site-part-model-body').textContent).not.toContain('Not available yet')
  })

  it('the unbound banner offers › Attach again per placed key, opening the Model part to pick', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue({ ...enabled([osm]), unbound_keys: ['StorageUnit:bess'] } as SiteScene)
    const flow = flowState.flow as { scene: { actions: { action: string }[] } }
    flow.scene.actions = flow.scene.actions.map(a => (a.action === 'assign_native' ? available('assign_native') : a))
    view()
    const link = await screen.findByTestId('site-unbound-attach-StorageUnit:bess')
    expect(screen.getByTestId('site-unbound').textContent).toContain('1 placed item is no longer in the model: the scene keeps its place until you attach it again')
    expect(screen.getByTestId('site-unbound').textContent).toContain('bess · storage unit › Attach again')
    expect(screen.getByTestId('site-unbound').textContent).not.toContain('StorageUnit:bess')
    fireEvent.click(link)
    expect(useUIStore.getState().sitePart).toBe('model')
    const { useModelProposal } = await import('./useSiteScene')
    expect(useModelProposal.getState().placementRebind).toEqual({ siteId: 'campus', key: 'StorageUnit:bess' })
  })

  it('the unbound banner counts in the plural for several placed items', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue({ ...enabled([osm]), unbound_keys: ['StorageUnit:bess', 'Generator:pv'] } as SiteScene)
    view()
    expect((await screen.findByTestId('site-unbound')).textContent).toContain('2 placed items are no longer in the model: the scene keeps their places until you attach them again')
  })

  it('the part body scrolls inside itself under the tabs, and is keyboard-focusable', async () => {
    view()
    const body = screen.getByTestId('site-part-body')
    expect(body.getAttribute('role')).toBe('tabpanel')
    expect(body.tabIndex).toBe(0)
    expect(body.className).toContain('overflow-y-auto')
    expect(body.className).toContain('max-h-[40vh]')
    expect(body.contains(screen.getByRole('tablist'))).toBe(false)
  })

  it('no mapped buildings: says so, and generates a proposed building', async () => {
    vi.mocked(propose).mockResolvedValue(proposal)
    view()
    expect(screen.getByTestId('scene-sources').textContent).toBe('No mapped buildings: refresh the site context or add buildings by hand')
    expect((screen.getByTestId('scene-generate') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByTestId('scene-add-building'))
    fireEvent.click(screen.getByTestId('scene-generate'))
    await screen.findByTestId('scene-proposal')
    const [, , siteId, action, inputs] = vi.mocked(propose).mock.calls[0]
    expect([siteId, action]).toEqual(['campus', 'generate_native'])
    expect((inputs as BuildingInputs).greenfield).toHaveLength(1)
    expect(screen.getByTestId('scene-proposal').textContent).toContain('Preview: 1 building')
  })

  it('Apply commits the proposal under review; Discard cancels it', async () => {
    useSceneProposal.setState({ proposal })
    view()
    fireEvent.click(screen.getByTestId('scene-apply'))
    await waitFor(() => expect(applyProposal).toHaveBeenCalledWith(expect.anything(), 'p', proposal))
    await waitFor(() => expect(useSceneProposal.getState().proposal).toBeNull())
    useSceneProposal.setState({ proposal })
    fireEvent.click(await screen.findByTestId('scene-discard'))
    await waitFor(() => expect(discardProposal).toHaveBeenCalled())
  })

  it('corrects a mapped building\'s height through edit_buildings', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([osm]))
    vi.mocked(propose).mockResolvedValue({ ...proposal, action: 'edit_buildings' })
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'osm:7' } })
    view()
    expect((await screen.findByTestId('scene-correction')).textContent).toContain('OSM building 7 · estimated (6 m default)')
    fireEvent.click(screen.getByTestId('scene-correct'))
    fireEvent.change(screen.getByTestId('scene-correct-height'), { target: { value: '0' } })
    expect((screen.getByTestId('scene-preview-change') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('scene-preview-reason').textContent).toBe('Correct the field marked in red to continue')
    fireEvent.change(screen.getByTestId('scene-correct-height'), { target: { value: '14' } })
    fireEvent.click(screen.getByTestId('scene-preview-change'))
    await waitFor(() => expect(propose).toHaveBeenCalled())
    const [, , , action, inputs] = vi.mocked(propose).mock.calls[0]
    expect(action).toBe('edit_buildings')
    expect((inputs as BuildingInputs).sources?.[0]).toMatchObject({ namespace: 'osm', source_id: '7', height_m: 14 })
    expect((inputs as BuildingInputs).sources?.[0].ring).toHaveLength(4)
  })

  it('a proposed building is corrected with its six numbers', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([gf]))
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'greenfield:b1' } })
    view()
    fireEvent.click(await screen.findByTestId('scene-correct'))
    expect((screen.getByTestId('greenfield-b1-height') as HTMLInputElement).value).toBe('9')
  })

  it('the preview names its building count only', () => {
    useSceneProposal.setState({ proposal })
    view()
    expect(screen.getByTestId('scene-proposal').textContent).toContain('Preview: 1 building')
    expect(screen.getByTestId('scene-proposal').textContent).not.toContain('triangles')
  })

  it('Results shows the served reason, with one › link to Results', () => {
    useUIStore.setState({ sitePart: 'results' })
    const flow = flowState.flow as { scene: { actions: unknown[] } }
    flow.scene.actions.push({ action: 'play_results', available: false, required_inputs: [], computes: '',
      blocker: { code: 'not_in_this_slice', reason: 'Not available yet: comes in a later version', link: '›',
        home: { guided: { section: 'results', drawer: null, open: 'request_then_scroll' }, expert: { panel: 'results' } } } })
    view()
    expect(screen.getByTestId('scene-blocker-play_results').textContent)
      .toBe('Not available yet: comes in a later version › Results')
  })

  it('an expired preview is dropped with its error, and the error clears on a part change', async () => {
    const { AxiosError, AxiosHeaders } = await import('axios')
    const headers = new AxiosHeaders()
    vi.mocked(applyProposal).mockRejectedValue(new AxiosError('x', 'ERR_BAD_REQUEST', { headers }, null,
      { status: 410, statusText: '', headers: {}, config: { headers },
        data: { detail: { error_kind: 'proposal_expired', message: 'This preview expired: generate again' } } }))
    useSceneProposal.setState({ proposal })
    view()
    fireEvent.click(screen.getByTestId('scene-apply'))
    expect((await screen.findByTestId('scene-error')).textContent).toBe('This preview expired: generate again')
    expect(useSceneProposal.getState().proposal).toBeNull()
    expect(screen.queryByTestId('scene-apply')).toBeNull()
    fireEvent.click(screen.getByTestId('site-part-results'))
    await waitFor(() => expect(screen.queryByTestId('scene-error')).toBeNull())
  })

  it('one accepted building reads in the singular; a proposed one is not named by its id', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([gf]))
    view()
    expect((await screen.findByTestId('scene-accepted')).textContent).toContain('1 building accepted')
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'greenfield:b1' } })
    expect((await screen.findByTestId('scene-correction')).textContent).toMatch(/^Proposed building(Height|$)/)
  })

  it('an unreadable scene offers › Snapshots', async () => {
    const { AxiosError, AxiosHeaders } = await import('axios')
    const headers = new AxiosHeaders()
    vi.mocked(siteSceneApi.getScene).mockRejectedValue(new AxiosError('x', 'ERR_BAD_RESPONSE', { headers }, null,
      { status: 500, statusText: '', headers: {}, config: { headers }, data: { detail: { error_kind: 'integrity_error' } } }))
    view()
    expect((await screen.findByTestId('scene-read-error')).textContent)
      .toBe('This site’s 3D scene could not be read: restore a snapshot › Snapshots')
    fireEvent.click(screen.getByTestId('scene-snapshots-link'))
    expect(useUIStore.getState().activeSlidePanel).toBe('snapshots')
    expect(useUIStore.getState().flowReturn?.label).toBe('Site 3D')
  })

  it('a managed-site refusal points to Generate', async () => {
    const { useSitesStore, MANAGED_SITE_COPY } = await import('./sitesStore')
    useUIStore.setState({ sitePart: 'map' })
    useSitesStore.setState({ sceneRefusal: { p: MANAGED_SITE_COPY } })
    view()
    expect(screen.getByText(MANAGED_SITE_COPY)).toBeTruthy()
    expect(MANAGED_SITE_COPY).not.toMatch(/reload/i)
    fireEvent.click(screen.getByTestId('scene-refusal-generate'))
    expect(useUIStore.getState().sitePart).toBe('generate')
    expect(useSitesStore.getState().sceneRefusal.p).toBeUndefined()
  })

  it('read-only: the reason is visible and nothing can be generated', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    view()
    expect((screen.getByTestId('scene-add-building') as HTMLButtonElement).disabled).toBe(true)
  })
})
