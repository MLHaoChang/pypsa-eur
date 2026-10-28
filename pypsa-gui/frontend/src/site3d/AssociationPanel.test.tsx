// SC-02b: the Model part, each state (UX SC-02 plan, conditions 1–7).
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelProposal, SceneAssociation, SceneBuilding, SceneTarget, SiteScene } from '../api/siteScene'

vi.mock('./sceneActions', async (orig) => ({
  ...(await orig<typeof import('./sceneActions')>()),
  propose: vi.fn(), applyProposal: vi.fn(), discardProposal: vi.fn(),
}))
vi.mock('../api/network', () => ({
  networkApi: {
    getLoads: vi.fn(), getLinks: vi.fn(), getStores: vi.fn(), getGenerators: vi.fn(), getStorageUnits: vi.fn(),
  },
}))

import { networkApi } from '../api/network'
import { applyProposal, propose } from './sceneActions'
import { useUIStore } from '../store/uiStore'
import { useModelProposal } from './useSiteScene'
import AssociationPanel from './AssociationPanel'
import type { Site } from './types'

const site: Site = { id: 'campus', name: 'Campus', buses: [], origin: { lng: 11, lat: 48 }, placements: {},
  boundary: [[10.999, 47.999], [11.001, 47.999], [11.001, 48.001], [10.999, 48.001]] }
const osm: SceneBuilding = { id: 'osm:7', source: { namespace: 'osm', source_id: '7' }, footprint: [[0, 0], [20, 0], [20, 20], [0, 20]],
  height_m: 6, height_evidence: 'fallback', levels: null }
const flat: SceneBuilding = { id: 'greenfield:b1', source: { namespace: 'greenfield', source_id: 'b1' }, footprint: [[40, 0], [60, 0], [60, 20], [40, 20]],
  height_m: 0, height_evidence: 'greenfield', levels: null }

const assoc = (o: Partial<SceneAssociation>): SceneAssociation => ({
  id: 'a1', feature: 'osm:7', class: 'Load', uid: 'u1', name: 'cooling_1', name_at_bind: 'cooling_1', role: 'load',
  weight: null, carrier: 'heat', unit: 'MW', status: 'bound', reason: null, rebound_from: null, ...o,
})
const target: SceneTarget = { class: 'Load', name: 'cooling_1', features: ['osm:7'], enclosures: 1,
  capacity: { value: 3, available: true, reason: null, unit: 'MW' }, allocation: { basis: 'none', shares: [] } }

type Enabled = Extract<SiteScene, { state: 'enabled' }>
const scene = (o: Partial<Enabled> = {}): Enabled => ({ state: 'enabled', site_id: 'campus', revision: 3, generation: 'g',
  site: { placements: {} }, buildings: [osm, flat], asset_footprints: {}, last_receipt: null, unbound_keys: [],
  associations: [], targets: [], groups: [], ...o })

const view = (s: Enabled, canWrite = true) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AssociationPanel site={site} scene={s} canWrite={canWrite} blockedReason={canWrite ? null : 'Read-only: ask an editor'} />
  </QueryClientProvider>,
)

const refused = (status: number, detail: Record<string, unknown>) => {
  const headers = new AxiosHeaders()
  return new AxiosError('refused', 'ERR_BAD_REQUEST', { headers }, null,
    { status, statusText: '', headers: {}, config: { headers }, data: { detail } })
}

const disabled = (id: string) => (screen.getByTestId(id) as HTMLButtonElement).disabled

beforeEach(() => {
  vi.clearAllMocks()
  useModelProposal.setState({ proposal: null, addAssetFrom: null, placementRebind: null })
  useUIStore.setState({ currentProject: 'p', sitePart: 'model', sitePartSelection: { kind: 'building', id: 'osm:7' },
    readOnly: false, readOnlyReason: 'writable', uiMode: 'guided', flowReturn: null, activeSection: null })
  vi.mocked(networkApi.getLoads).mockResolvedValue([{ name: 'cooling_1', bus: 'h', carrier: 'heat' }, { name: 'office', bus: 'e', carrier: 'AC' }] as never)
  vi.mocked(networkApi.getLinks).mockResolvedValue([{ name: 'haber', bus0: 'e', bus1: 'n', carrier: 'nh3', eh_process: true }] as never)
  vi.mocked(networkApi.getStores).mockResolvedValue([] as never)
  vi.mocked(networkApi.getGenerators).mockResolvedValue([{ name: 'pv', bus: 'e', carrier: 'solar' }] as never)
  vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ name: 'bess', bus: 'e', carrier: 'battery' }] as never)
})

describe('Model part: attach', () => {
  it('unbound building (Guided): next task, a picker with nothing chosen, Preview disabled with its reason', async () => {
    view(scene())
    expect(screen.getByTestId('scene-next-task').textContent).toBe('Next: Attach buildings to loads and processes')
    expect(screen.getByTestId('assoc-building').textContent).toContain('OSM building 7')
    expect(screen.getByTestId('assoc-none').textContent).toBe('Not attached to anything yet')
    const picker = screen.getByTestId('assoc-attach-picker') as HTMLSelectElement
    expect(picker.value).toBe('')
    await waitFor(() => expect(picker.querySelectorAll('option:not([disabled])').length).toBe(5))
    expect([...picker.options].map(o => o.textContent)).toContain('haber · process unit')
    expect([...picker.options].map(o => o.textContent).join()).not.toMatch(/Load:|Link:/)
    expect(disabled('assoc-preview')).toBe(true)
    expect(screen.getByTestId('assoc-preview-reason').textContent).toBe('Choose what to attach first')
    expect(screen.queryByTestId('assoc-discard')).toBeNull()
  })

  it('picker open → Preview → review rows and findings → Apply', async () => {
    const preview: ModelProposal = { id: 'm1', site_id: 'campus', action: 'bind_targets', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { groups: [], associations: [
        { feature: 'osm:7', class: 'Load', name: 'office', carrier: 'AC', unit: 'MW', role: 'load', weight: null, status: 'new', reason: null },
      ], findings: [{ code: 'double_counted_demand', severity: 'warn', feature: 'osm:7', objects: ['Link:haber', 'Load:office'],
        message: 'office · load and haber · process unit both draw electricity here: this demand may be counted twice' }] } }
    vi.mocked(propose).mockResolvedValue(preview as never)
    view(scene())
    const picker = screen.getByTestId('assoc-attach-picker') as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(6))
    fireEvent.change(picker, { target: { value: 'Load:office' } })
    expect(disabled('assoc-preview')).toBe(false)
    fireEvent.click(screen.getByTestId('assoc-preview'))
    await screen.findByTestId('assoc-proposal')
    const [, , siteId, action, inputs] = vi.mocked(propose).mock.calls[0]
    expect([siteId, action]).toEqual(['campus', 'bind_targets'])
    expect(inputs).toEqual({ associations: [{ feature: 'osm:7', target: { class: 'Load', name: 'office' } }], groups: [] })
    expect(screen.getByTestId('assoc-proposal').textContent).toContain('Attach OSM building 7 to office · load')
    expect(screen.getByTestId('assoc-finding-0').textContent).toContain('may be counted twice')
    useUIStore.setState({ sitePartSelection: null })
    fireEvent.click(screen.getByTestId('assoc-finding-0'))
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Link', name: 'haber' })
    // The building the finding is about is selected too (condition 5: both highlight).
    expect(useUIStore.getState().sitePartSelection).toEqual({ kind: 'building', id: 'osm:7' })
    fireEvent.click(screen.getByTestId('assoc-apply'))
    await waitFor(() => expect(applyProposal).toHaveBeenCalledWith(expect.anything(), 'p', expect.objectContaining({ id: 'm1' })))
    await waitFor(() => expect(useModelProposal.getState().proposal).toBeNull())
  })

  it('attached (Guided): names only, carrier and unit; next task is Place equipment', () => {
    view(scene({ associations: [assoc({})], targets: [target] }))
    expect(screen.getByTestId('scene-next-task').textContent).toBe('Next: Place equipment')
    expect(screen.getByTestId('assoc-row-a1').textContent).toBe('cooling_1 · load · Heat · MW')
    expect(screen.queryByTestId('assoc-target-detail-a1')).toBeNull()
  })

  it('attached (Expert): class, enclosures and capacity once', () => {
    useUIStore.setState({ uiMode: 'expert' })
    view(scene({ associations: [assoc({})], targets: [target] }))
    expect(screen.queryByTestId('scene-next-task')).toBeNull()
    expect(screen.getByTestId('assoc-row-a1').textContent).toContain('cooling_1 · Load · Heat · MW')
    expect(screen.getByTestId('assoc-target-detail-a1').textContent).toBe('Enclosures 1 · Capacity 3 MW')
  })

  it('unbound: "Not attached: …" › Attach again opens the picker as a rebind the user confirms', async () => {
    const a = assoc({ status: 'unbound', name: null, reason: 'cooling_1 · load was deleted or replaced' })
    vi.mocked(propose).mockResolvedValue({ id: 'm2', site_id: 'campus', action: 'rebind', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { association: 'a1', feature: 'osm:7', class: 'Load', name: 'office', was: 'cooling_1', findings: [] } } as never)
    view(scene({ associations: [a] }))
    fireEvent.click(await screen.findByTestId('assoc-fix-a1'))
    expect(screen.getByTestId('assoc-row-a1').textContent).toBe('Not attached: cooling_1 · load was deleted or replaced › Attach again')
    expect(screen.getByTestId('assoc-rebind').textContent).toContain('choose the load to use instead of cooling_1')
    const picker = screen.getByTestId('assoc-attach-picker') as HTMLSelectElement
    expect([...picker.options].filter(o => !o.disabled).map(o => o.textContent)).toEqual(['cooling_1 · load', 'office · load'])
    fireEvent.change(picker, { target: { value: 'Load:office' } })
    fireEvent.click(screen.getByTestId('assoc-preview'))
    await screen.findByTestId('assoc-proposal')
    expect(vi.mocked(propose).mock.calls[0].slice(3)).toEqual(['rebind', { association: 'a1', target: { class: 'Load', name: 'office' }, confirmed: true }])
    expect(screen.getByTestId('assoc-proposal').textContent).toContain('Attach OSM building 7 to office · load again · was cooling_1')
  })

  it('unavailable (Guided): "Unavailable: …" › Loads opens the Loads card in Guided, Back to Site 3D', () => {
    view(scene({ associations: [assoc({ status: 'unavailable', uid: null, name: null, reason: 'No heat load in the model' })] }))
    expect(screen.getByTestId('assoc-row-a1').textContent).toBe('Unavailable: no heat load in the model › Loads')
    fireEvent.click(screen.getByTestId('assoc-fix-a1'))
    const ui = useUIStore.getState()
    expect([ui.uiMode, ui.activeSection, ui.activeSlidePanel]).toEqual(['guided', 'loads', 'hubDesign'])
    expect(ui.flowReturn?.label).toBe('Site 3D')
  })

  it('unavailable (Expert): › Loads opens the Loads table in Expert', () => {
    useUIStore.setState({ uiMode: 'expert' })
    view(scene({ associations: [assoc({ status: 'unavailable', uid: null, name: null, reason: 'No heat load in the model' })] }))
    fireEvent.click(screen.getByTestId('assoc-fix-a1'))
    expect([useUIStore.getState().uiMode, useUIStore.getState().activeSection]).toEqual(['expert', 'loads'])
    expect(useUIStore.getState().flowReturn?.label).toBe('Site 3D')
  })

  it('not checked: says so, no link', () => {
    view(scene({ associations: [assoc({ status: 'not_checked', name: null })] }))
    expect(screen.getByTestId('assoc-row-a1').textContent).toBe('Not checked: open the project’s model to check attachments')
    expect(screen.queryByTestId('assoc-fix-a1')).toBeNull()
  })

  it('weights not 100 %: shares per building, a visible total, Preview disabled with that reason', async () => {
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'greenfield:b1' } })
    view(scene({ associations: [assoc({ feature: 'osm:7' })], targets: [target] }))
    const picker = screen.getByTestId('assoc-attach-picker') as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(6))
    fireEvent.change(picker, { target: { value: 'Load:cooling_1' } })
    expect(screen.getByTestId('assoc-share-total').textContent).toBe('Not split: every building counts the whole item')
    fireEvent.change(screen.getByTestId('assoc-share-osm:7'), { target: { value: '60' } })
    fireEvent.change(screen.getByTestId('assoc-share-greenfield:b1'), { target: { value: '25' } })
    expect(screen.getByTestId('assoc-share-total').textContent).toBe('Total 85 %: shares should add up to 100 %')
    expect(disabled('assoc-preview')).toBe(true)
    expect(screen.getByTestId('assoc-preview-reason').textContent).toBe('Total 85 %: shares should add up to 100 %')
    fireEvent.change(screen.getByTestId('assoc-share-greenfield:b1'), { target: { value: '40' } })
    expect(screen.getByTestId('assoc-share-total').textContent).toBe('Total 100 %')
    expect(disabled('assoc-preview')).toBe(false)
  })

  it('no candidates: the picker is disabled with a visible reason', async () => {
    for (const fn of [networkApi.getLoads, networkApi.getLinks, networkApi.getGenerators, networkApi.getStorageUnits]) vi.mocked(fn).mockResolvedValue([] as never)
    view(scene())
    await waitFor(() => expect(networkApi.getStorageUnits).toHaveBeenCalled())
    expect((screen.getByTestId('assoc-attach-picker') as HTMLSelectElement).disabled).toBe(true)
    expect(screen.getByTestId('assoc-attach-reason').textContent).toContain('Nothing to attach: the model has no loads')
  })

  it('an ambiguous target lists the candidates to pick; nothing is picked for the user', async () => {
    vi.mocked(propose).mockRejectedValue(refused(422, { error_kind: 'invalid_input', code: 'ambiguous_target',
      message: 'Several heat loads: choose one', candidates: ['cooling_1', 'cooling_2'] }))
    view(scene())
    const picker = screen.getByTestId('assoc-attach-picker') as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(6))
    fireEvent.change(picker, { target: { value: 'Load:office' } })
    fireEvent.click(screen.getByTestId('assoc-preview'))
    expect((await screen.findByTestId('assoc-error')).textContent).toContain('Several heat loads: choose one')
    expect(screen.getByTestId('assoc-candidate-cooling_2').textContent).toBe('cooling_2 · load')
  })

  it('read-only: Preview disabled with the reason', async () => {
    view(scene(), false)
    expect(disabled('assoc-preview')).toBe(true)
    expect((screen.getByTestId('assoc-attach-picker') as HTMLSelectElement).disabled).toBe(true)
  })

  it('no building selected: a hint, and the count attached', () => {
    useUIStore.setState({ sitePartSelection: null })
    view(scene({ associations: [assoc({})] }))
    expect(screen.getByTestId('assoc-select-hint').textContent).toBe('Select a building in the scene to attach it · 1 attached')
  })
})

describe('Model part: place equipment', () => {
  it('nothing chosen: Preview disabled with its reason; approximate size and no rule on the chosen item', async () => {
    view(scene())
    expect(disabled('place-preview')).toBe(true)
    expect(screen.getByTestId('place-preview-reason').textContent).toBe('Choose what to place first')
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'StorageUnit:bess' } })
    expect(screen.getByTestId('place-size').textContent).toBe('Approximate size')
    expect(screen.getByTestId('place-no-rule').textContent).toMatch(/^No rule checked: /)
    expect(screen.getByTestId('place-on-reason').textContent).toBe('Roof: only solar panels go on a roof')
    expect([...(screen.getByTestId('place-on') as HTMLSelectElement).options].map(o => o.textContent)).toEqual(['Ground'])
  })

  it('an exact catalogue size shows no "Approximate size"', async () => {
    view(scene({ asset_footprints: { 'StorageUnit:bess': { width_m: 6, depth_m: 2.5, source: 'catalogue' } } }))
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'StorageUnit:bess' } })
    expect(screen.queryByTestId('place-size')).toBeNull()
    expect(screen.queryByTestId('place-no-rule')).toBeNull()
  })

  it('a solar item on an eligible roof: "Roof of OSM building 7", previewed as place_existing', async () => {
    vi.mocked(propose).mockResolvedValue({ id: 'm3', site_id: 'campus', action: 'place_existing', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { placements: { 'Generator:pv': { x: 10, y: 10, heading: 0, on: 'roof:osm:7' } }, findings: [] } } as never)
    view(scene())
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'Generator:pv' } })
    fireEvent.change(screen.getByTestId('place-on'), { target: { value: 'roof' } })
    expect((screen.getByTestId('place-x') as HTMLInputElement).value).toBe('10')
    fireEvent.click(screen.getByTestId('place-preview'))
    await screen.findByTestId('assoc-proposal')
    expect(vi.mocked(propose).mock.calls[0].slice(3)).toEqual(['place_existing',
      { placements: { 'Generator:pv': { x: 10, y: 10, heading: 0, on: 'roof:osm:7' } } }])
    expect(screen.getByTestId('assoc-proposal').textContent).toContain('pv · generator · on the roof of OSM building 7')
    expect(screen.getByTestId('assoc-no-findings').textContent).toBe('No overlap with buildings found')
    expect(screen.getByTestId('proposal-size').textContent).toBe('Approximate size')
  })

  it('a building without a height offers no roof, and says why', async () => {
    useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'greenfield:b1' } })
    view(scene())
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'Generator:pv' } })
    expect(screen.getByTestId('place-on-reason').textContent).toBe('Roof: Building 2 has no height to stand on')
  })

  it('overlap: the finding names the building; selecting it selects both objects', async () => {
    vi.mocked(propose).mockResolvedValue({ id: 'm4', site_id: 'campus', action: 'place_existing', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { placements: { 'StorageUnit:bess': { x: 5, y: 5, heading: 0, on: 'ground' } },
        findings: [{ code: 'overlap', severity: 'warn', objects: ['StorageUnit:bess', 'osm:7'], message: 'bess · storage unit overlaps building osm:7' }] } } as never)
    useUIStore.setState({ sitePartSelection: null })
    view(scene())
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'StorageUnit:bess' } })
    fireEvent.click(screen.getByTestId('place-preview'))
    const f = await screen.findByTestId('assoc-finding-0')
    expect(f.textContent).toBe('Overlaps OSM building 7')
    fireEvent.click(f)
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'StorageUnit', name: 'bess' })
    expect(useUIStore.getState().sitePartSelection).toEqual({ kind: 'building', id: 'osm:7' })
  })

  it('a refusal reads in plain words: off the roof', async () => {
    vi.mocked(propose).mockRejectedValue(refused(422, { error_kind: 'invalid_input', code: 'off_roof', message: 'pv · generator: not within the roof of building osm:7' }))
    view(scene())
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'Generator:pv' } })
    fireEvent.change(screen.getByTestId('place-on'), { target: { value: 'roof' } })
    fireEvent.click(screen.getByTestId('place-preview'))
    expect((await screen.findByTestId('assoc-error')).textContent).toBe('Off the roof of OSM building 7')
  })

  it('an unbound placement refusal shows the server message', async () => {
    const message = 'bess · storage unit: this placement belonged to an item that was deleted or replaced: attach it again first'
    vi.mocked(propose).mockRejectedValue(refused(422, { error_kind: 'invalid_input', code: 'unbound_target', message }))
    view(scene())
    const item = screen.getByTestId('place-item') as HTMLSelectElement
    await waitFor(() => expect(item.options.length).toBe(4))
    fireEvent.change(item, { target: { value: 'StorageUnit:bess' } })
    fireEvent.click(screen.getByTestId('place-preview'))
    expect((await screen.findByTestId('assoc-error')).textContent).toBe(`${message} › Attach again`)
  })

  it('› Add model asset opens the Guided assets home with Back to Site 3D; a new item then offers › Place it', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><AssociationPanel site={site} scene={scene()} canWrite blockedReason={null} /></QueryClientProvider>)
    await waitFor(() => expect((screen.getByTestId('place-item') as HTMLSelectElement).options.length).toBe(4))
    fireEvent.click(screen.getByTestId('add-asset-link'))
    expect([useUIStore.getState().uiMode, useUIStore.getState().activeSection]).toEqual(['guided', 'assets'])
    expect(useUIStore.getState().flowReturn?.label).toBe('Site 3D')
    expect(screen.queryByTestId('place-it-link')).toBeNull()
    vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ name: 'bess' }, { name: 'bess_2', carrier: 'battery' }] as never)
    await qc.invalidateQueries()
    const link = await screen.findByTestId('place-it-link')
    expect(screen.getByTestId('place-it').textContent).toBe('bess_2 · storage unit is in the model and not placed › Place it')
    fireEvent.click(link)
    expect((screen.getByTestId('place-item') as HTMLSelectElement).value).toBe('StorageUnit:bess_2')
    expect(screen.queryByTestId('place-it-link')).toBeNull()
  })
})

describe('Model part: review fixes', () => {
  const loaded = async () => {
    const picker = screen.getByTestId('assoc-attach-picker') as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBeGreaterThanOrEqual(6))
    return picker
  }

  it('another building starts with nothing chosen: no inherited pick, shares or roof', async () => {
    vi.mocked(propose).mockResolvedValue({ id: 'm5', site_id: 'campus', action: 'place_existing', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { placements: {}, findings: [] } } as never)
    view(scene())
    fireEvent.change(await loaded(), { target: { value: 'Load:office' } })
    fireEvent.change(screen.getByTestId('place-item'), { target: { value: 'Generator:pv' } })
    fireEvent.change(screen.getByTestId('place-on'), { target: { value: 'roof' } })
    act(() => useUIStore.setState({ sitePartSelection: { kind: 'building', id: 'greenfield:b1' } }))
    expect((screen.getByTestId('assoc-attach-picker') as HTMLSelectElement).value).toBe('')
    expect(screen.getByTestId('assoc-preview-reason').textContent).toBe('Choose what to attach first')
    expect((screen.getByTestId('place-on') as HTMLSelectElement).value).toBe('ground')
    fireEvent.click(screen.getByTestId('place-preview'))
    await waitFor(() => expect(propose).toHaveBeenCalled())
    expect(vi.mocked(propose).mock.calls[0][4]).toEqual({ placements: { 'Generator:pv': { x: 10, y: 10, heading: 0, on: 'ground' } } })
  })

  it('a placed key whose item is gone: › Attach again on the refusal, a same-class picker, rebind {placement}', async () => {
    vi.mocked(propose).mockRejectedValueOnce(refused(422, { error_kind: 'invalid_input', code: 'unbound_target', key: 'StorageUnit:bess',
      message: 'bess · storage unit: this placement belonged to an item that was deleted or replaced: attach it again first' }))
    vi.mocked(networkApi.getStorageUnits).mockResolvedValue([{ name: 'bess', carrier: 'battery' }, { name: 'bess_new', carrier: 'battery' }] as never)
    view(scene())
    await loaded()
    fireEvent.change(screen.getByTestId('place-item'), { target: { value: 'StorageUnit:bess' } })
    fireEvent.click(screen.getByTestId('place-preview'))
    fireEvent.click(await screen.findByTestId('assoc-error-attach-again'))
    expect(screen.queryByTestId('assoc-error-reload')).toBeNull()
    const picker = screen.getByTestId('place-rebind-picker') as HTMLSelectElement
    expect(picker.value).toBe('')
    expect([...picker.options].filter(o => !o.disabled).map(o => o.textContent)).toEqual(['bess · storage unit', 'bess_new · storage unit'])
    expect(disabled('place-rebind-preview')).toBe(true)
    expect(screen.getByTestId('place-rebind-preview-reason').textContent).toBe('Choose what to attach first')
    vi.mocked(propose).mockResolvedValueOnce({ id: 'm6', site_id: 'campus', action: 'rebind', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { placement: 'StorageUnit:bess', class: 'StorageUnit', name: 'bess_new', was: 'bess', findings: [] } } as never)
    fireEvent.change(picker, { target: { value: 'StorageUnit:bess_new' } })
    fireEvent.click(screen.getByTestId('place-rebind-preview'))
    await screen.findByTestId('assoc-proposal')
    expect(vi.mocked(propose).mock.calls[1].slice(3)).toEqual(['rebind',
      { placement: 'StorageUnit:bess', target: { class: 'StorageUnit', name: 'bess_new' }, confirmed: true }])
    expect(screen.getByTestId('assoc-proposal').textContent).toContain('Attach the placed bess to bess_new · storage unit again')
  })

  it('a placement rebind for another Site is not shown here', () => {
    useModelProposal.setState({ placementRebind: { siteId: 'elsewhere', key: 'StorageUnit:bess' } })
    view(scene())
    expect(screen.queryByTestId('place-rebind')).toBeNull()
  })

  it('a scene changed since the preview: Apply refused offers Reload, which drops the preview', async () => {
    useModelProposal.setState({ proposal: { id: 'm7', site_id: 'campus', action: 'bind_targets', base_revision: 3, input_hash: 'h', status: 'ready',
      preview: { associations: [], groups: [], findings: [] } } })
    vi.mocked(applyProposal).mockRejectedValue(refused(409, { error_kind: 'revision_conflict', code: 'target_changed',
      message: 'cooling_1 · load was replaced since you reviewed it: review again' }))
    view(scene())
    fireEvent.click(screen.getByTestId('assoc-apply'))
    expect((await screen.findByTestId('assoc-error')).textContent).toContain('was replaced since you reviewed it: review again')
    fireEvent.click(screen.getByTestId('assoc-error-reload'))
    expect(useModelProposal.getState().proposal).toBeNull()
    expect(screen.queryByTestId('assoc-error')).toBeNull()
  })

  it('an attachment gone since the preview (unknown_association) offers Reload too', async () => {
    vi.mocked(propose).mockRejectedValue(refused(422, { error_kind: 'invalid_input', code: 'unknown_association', message: 'That attachment no longer exists: reload' }))
    view(scene())
    fireEvent.change(await loaded(), { target: { value: 'Load:office' } })
    fireEvent.click(screen.getByTestId('assoc-preview'))
    expect(await screen.findByTestId('assoc-error-reload')).toBeTruthy()
  })

  it('Add model asset before the lists load takes no snapshot; a snapshot of another Site offers nothing', async () => {
    vi.mocked(networkApi.getStorageUnits).mockReturnValue(new Promise(() => {}) as never)
    view(scene())
    fireEvent.click(screen.getByTestId('add-asset-link'))
    expect(useModelProposal.getState().addAssetFrom).toBeNull()
  })

  it('a snapshot from another Site offers no › Place it', async () => {
    useModelProposal.setState({ addAssetFrom: { project: 'p', siteId: 'elsewhere', keys: [] } })
    view(scene())
    await loaded()
    expect(screen.queryByTestId('place-it-link')).toBeNull()
  })
})
