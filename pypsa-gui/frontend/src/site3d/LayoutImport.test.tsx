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
import { propose } from './sceneActions'
import { useUIStore } from '../store/uiStore'
import { useSceneProposal } from './useSiteScene'
import SiteScenePanel from './SiteScenePanel'
import { layoutReady } from './LayoutImport'
import type { Site } from './types'

const site: Site = { id: 'campus', name: 'Campus', buses: [], origin: { lng: 11, lat: 48 }, placements: {},
  boundary: [[10.999, 47.999], [11.001, 47.999], [11.001, 48.001], [10.999, 48.001]] }
const hall: SceneBuilding = { id: 'layout:hall', source: { namespace: 'layout', source_id: 'hall' },
  footprint: [[0, 0], [20, 0], [20, 10], [0, 10]], height_m: 8, height_evidence: 'tagged', levels: null }
const enabled = (buildings: SceneBuilding[]): SiteScene => ({ state: 'enabled', site_id: 'campus', revision: 2, generation: 'g',
  site: {}, buildings, asset_footprints: {}, last_receipt: null, unbound_keys: [] })
const available = (name: string) => ({ action: name, available: true, blocker: null, required_inputs: [], computes: '' })
const preview: Proposal = { id: 'p-1', site_id: 'campus', action: 'import_layout', base_revision: 2, input_hash: 'h', status: 'ready',
  preview: { buildings: [hall], stats: { buildings: 1, triangles: 12, bytes: 300 },
    layout: { kind: 'raster', metres_per_pixel: 0.1, crs: null, extent_m: [20, 10], footprints: 1, skipped: {} } } }
const view = () => render(<QueryClientProvider client={new QueryClient()}><SiteScenePanel site={site} contextBuildings={[]} /></QueryClientProvider>)

const enc = (s: string) => Array.from(s, c => c.charCodeAt(0))
const be32 = (n: number) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]
const png = (w: number, h: number) => new File([new Uint8Array([0x89, ...enc('PNG\r\n\x1a\n'), 0, 0, 0, 13, ...enc('IHDR'), ...be32(w), ...be32(h), 8, 6, 0, 0, 0])], 'plan.png')
const choose = async (f: File) => fireEvent.change(await screen.findByTestId('layout-import-file'), { target: { files: [f] } })

beforeEach(() => {
  vi.clearAllMocks()
  useSceneProposal.setState({ proposal: null })
  useUIStore.setState({ currentProject: 'p', sitePart: 'generate', sitePartSelection: null, readOnly: false, readOnlyReason: 'writable', uiMode: 'guided' })
  flowState.flow = { scene: { entry: {}, anchor_moved: [], actions: [available('generate_native'), available('process_layout')] } }
  vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([]))
  URL.createObjectURL = vi.fn(() => 'blob:plan')
  URL.revokeObjectURL = vi.fn()
})

describe('layout import (SC-06-layout)', () => {
  it('is offered only while process_layout is available', async () => {
    flowState.flow = { scene: { entry: {}, anchor_moved: [], actions: [available('generate_native')] } }
    view()
    await screen.findByTestId('scene-generate')
    expect(screen.queryByTestId('layout-import')).toBeNull()
  })

  it('previews a GeoJSON layout as an import_layout proposal', async () => {
    vi.mocked(propose).mockResolvedValue(preview)
    view()
    const doc = { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: null, properties: {} }] }
    await choose(new File([JSON.stringify(doc)], 'campus.geojson'))
    expect((await screen.findByTestId('layout-import-geojson')).textContent).toContain('campus.geojson · 1 feature')
    fireEvent.click(screen.getByTestId('layout-import-geojson-preview'))
    await waitFor(() => expect(propose).toHaveBeenCalledWith(expect.anything(), 'p', 'campus', 'import_layout', { layout: { kind: 'geojson', document: doc } }))
  })

  it('names a refused file and sends nothing', async () => {
    view()
    await choose(new File(['0\nSECTION\n'], 'plan.dxf'))
    expect((await screen.findByTestId('layout-import-error')).textContent).toMatch(/^DXF files are not accepted/)
    expect(propose).not.toHaveBeenCalled()
  })

  it('traces a drawing with the keyboard and sends only the frame, the calibration and the outline', async () => {
    vi.mocked(propose).mockResolvedValue(preview)
    view()
    await choose(png(1000, 800))
    const canvas = await screen.findByTestId('layout-trace-canvas')
    expect((screen.getByTestId('layout-trace-preview') as HTMLButtonElement).disabled).toBe(true)
    const key = (k: string, n = 1, shiftKey = false) => { for (let i = 0; i < n; i++) fireEvent.keyDown(canvas, { key: k, shiftKey }) }
    // The crosshair starts at the centre (500, 400); a step is 2 px, Shift 20 px.
    key('Enter'); key('ArrowRight', 5, true); key('Enter')                       // scale: (500,400) → (600,400)
    fireEvent.change(screen.getByTestId('layout-scale-distance'), { target: { value: '10' } })
    fireEvent.click(screen.getByTestId('layout-tool-anchor')); key('ArrowLeft', 5, true); key('Enter')   // anchor (500,400)
    fireEvent.change(screen.getByTestId('layout-anchor-east'), { target: { value: '5' } })
    fireEvent.change(screen.getByTestId('layout-rotation'), { target: { value: '90' } })
    fireEvent.click(screen.getByTestId('layout-tool-trace'))
    key('Enter'); key('ArrowRight', 5, true); key('Enter'); key('ArrowUp', 5, true); key('Enter')
    fireEvent.click(screen.getByTestId('layout-trace-finish'))
    fireEvent.click(screen.getByTestId('layout-trace-preview'))
    await waitFor(() => expect(propose).toHaveBeenCalled())
    const inputs = vi.mocked(propose).mock.calls[0][4]
    expect(inputs).toEqual({ layout: { kind: 'raster', image: { width_px: 1000, height_px: 800, media_type: 'image/png' },
      calibration: { scale: { a_px: [500, 400], b_px: [600, 400], distance: 10, unit: 'm' }, anchor_px: [500, 400], anchor_local: [5, 0], rotation_deg: 90 },
      footprints: [{ id: 'building 1', ring_px: [[500, 400], [600, 400], [600, 300]] }] } })
    expect(JSON.stringify(inputs)).not.toContain('blob:')
    await waitFor(() => expect(screen.queryByTestId('layout-trace')).toBeNull())
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:plan')
  })

  it('reads the calibration back in the preview before Apply', async () => {
    useSceneProposal.setState({ proposal: preview })
    view()
    expect((await screen.findByTestId('scene-layout-readback')).textContent)
      .toBe('Drawing at 0.1 m per pixel · spans 20 × 10 m (east × north)')
  })

  it('corrects a layout building corner by hand through edit_buildings, keeping its id', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled([hall]))
    vi.mocked(propose).mockResolvedValue({ ...preview, action: 'edit_buildings' })
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'layout:hall' } })
    view()
    fireEvent.click(await screen.findByTestId('scene-correct'))
    fireEvent.change(screen.getByTestId('scene-corner-1-e'), { target: { value: '22' } })
    fireEvent.click(screen.getByTestId('scene-preview-change'))
    await waitFor(() => expect(propose).toHaveBeenCalled())
    const [, , , action, inputs] = vi.mocked(propose).mock.calls[0]
    expect(action).toBe('edit_buildings')
    const src = (inputs as { sources: { namespace: string; source_id: string; ring: [number, number][] }[] }).sources[0]
    expect([src.namespace, src.source_id, src.ring.length]).toEqual(['layout', 'hall', 4])
    expect(src.ring[1][0]).toBeGreaterThan(src.ring[0][0])
  })

  it('needs a scale, an anchor and a finished outline', () => {
    const fp = [{ id: 'a', ring_px: [[0, 0], [1, 0], [1, 1]] as [number, number][] }]
    expect(layoutReady([[0, 0], [1, 0]], 10, [0, 0], fp)).toBe(true)
    expect(layoutReady([[0, 0]], 10, [0, 0], fp)).toBe(false)
    expect(layoutReady([[0, 0], [1, 0]], 0, [0, 0], fp)).toBe(false)
    expect(layoutReady([[0, 0], [1, 0]], 10, null, fp)).toBe(false)
    expect(layoutReady([[0, 0], [1, 0]], 10, [0, 0], [])).toBe(false)
  })
})
