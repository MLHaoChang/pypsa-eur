import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Proposal, SceneBuilding, SiteScene } from '../api/siteScene'

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
    fireEvent.click(screen.getByTestId('site-part-model'))
    expect(screen.getByTestId('scene-blocker-assign_native').textContent).toBe('Buildings only: configure model elements to map them › Loads')
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
    expect(inputs.greenfield).toHaveLength(1)
    expect(screen.getByTestId('scene-proposal').textContent).toContain('Preview: 1 buildings · 16 triangles')
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
    expect((await screen.findByTestId('scene-correction')).textContent).toContain('OSM building 7 · height not mapped: 6 m assumed')
    fireEvent.click(screen.getByTestId('scene-correct'))
    fireEvent.change(screen.getByTestId('scene-correct-height'), { target: { value: '0' } })
    expect((screen.getByTestId('scene-preview-change') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByTestId('scene-correct-height'), { target: { value: '14' } })
    fireEvent.click(screen.getByTestId('scene-preview-change'))
    await waitFor(() => expect(propose).toHaveBeenCalled())
    const [, , , action, inputs] = vi.mocked(propose).mock.calls[0]
    expect(action).toBe('edit_buildings')
    expect(inputs.sources?.[0]).toMatchObject({ namespace: 'osm', source_id: '7', height_m: 14 })
    expect(inputs.sources?.[0].ring).toHaveLength(4)
  })

  it('a proposed building is corrected with its six numbers', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([gf]))
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'greenfield:b1' } })
    view()
    fireEvent.click(await screen.findByTestId('scene-correct'))
    expect((screen.getByTestId('greenfield-b1-height') as HTMLInputElement).value).toBe('9')
  })

  it('read-only: the reason is visible and nothing can be generated', () => {
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    view()
    expect((screen.getByTestId('scene-add-building') as HTMLButtonElement).disabled).toBe(true)
  })
})
