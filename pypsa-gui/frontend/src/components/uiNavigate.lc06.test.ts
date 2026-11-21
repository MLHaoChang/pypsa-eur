import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import toast from 'react-hot-toast'
import {
  CATALOGUE_UNAVAILABLE_NOTICE, NAVIGATE_KINDS, PARAMETER_NOTICE_OPTIONS, NOT_OPENABLE_NOTICE, UNKNOWN_PARAMETER_NOTICE, applyUiNavigate,
} from './uiNavigate'
import { takePaletteSeed } from '../catalogue/openParameter'
import * as catalogueApi from '../catalogue/api'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))
import { entry } from '../catalogue/testEntries'
import { manualNavigation, navigationEpoch } from '../utils/navigationEpoch'
import { useUIStore } from '../store/uiStore'

afterEach(() => { vi.restoreAllMocks() })

// LC-06 S11, LC-D24: the `ui_event` frames the assistant's UI tools emit
// (shapes asserted in backend/tests/test_lc06_d24_ui_events.py) drive exactly the
// store state they name, and a stale or malformed one moves nothing.
beforeEach(() => {
  useUIStore.setState({
    activeSlidePanel: null, selectedComponent: null, assetDetailRequest: null,
    resultsSnapshotIdx: 0, rightPanelOpen: false,
  })
})

it.each([0, 1])('set_snapshot index %i lands on that snapshot', index => {
  useUIStore.setState({ resultsSnapshotIdx: 7 })
  expect(applyUiNavigate({ kind: 'set_snapshot', snapshot_iso: '2026-01-01T01:00:00', period: null,
    snapshot_index: index })).toBe(true)
  expect(useUIStore.getState().resultsSnapshotIdx).toBe(index)
})

it('a set_snapshot without a numeric index moves nothing', () => {
  useUIStore.setState({ resultsSnapshotIdx: 1 })
  applyUiNavigate({ kind: 'set_snapshot', snapshot_iso: '2026-01-01T00:00:00' })
  expect(useUIStore.getState().resultsSnapshotIdx).toBe(1)
})

it('select_component selects that asset and opens the right panel', () => {
  applyUiNavigate({ kind: 'select_component', component_class: 'Generator', name: 'G1' })
  const ui = useUIStore.getState()
  expect(ui.selectedComponent).toEqual({ type: 'Generator', name: 'G1' })
  expect(ui.rightPanelOpen).toBe(true)
})

it('a navigate frame for the properties panel opens the right panel', () => {
  applyUiNavigate({ kind: 'navigate', panel_id: 'properties' })
  expect(useUIStore.getState().rightPanelOpen).toBe(true)
})

it('open_asset_detail requests that asset with the metrics and mode asked', () => {
  applyUiNavigate({ kind: 'open_asset_detail', component_class: 'Generator', name: 'G1',
    metrics: ['p'], mode: 'duration' })
  expect(useUIStore.getState().assetDetailRequest).toMatchObject({
    componentClass: 'Generator', name: 'G1', metrics: ['p'], mode: 'duration' })
})

it('a frame older than the user\'s own navigation is dropped', () => {
  const issued = navigationEpoch()
  manualNavigation()
  useUIStore.setState({ resultsSnapshotIdx: 0, selectedComponent: null })
  expect(applyUiNavigate({ kind: 'set_snapshot', snapshot_index: 1 }, issued)).toBe(false)
  expect(applyUiNavigate({ kind: 'select_component', component_class: 'Generator', name: 'G1' }, issued)).toBe(false)
  const ui = useUIStore.getState()
  expect(ui.resultsSnapshotIdx).toBe(0)
  expect(ui.selectedComponent).toBeNull()
  // The same frame under the current epoch is applied.
  expect(applyUiNavigate({ kind: 'set_snapshot', snapshot_index: 1 }, navigationEpoch())).toBe(true)
  expect(useUIStore.getState().resultsSnapshotIdx).toBe(1)
})

// expert-catalogue §7.1: `open_parameter` emits kind `parameter`; it is on the navigate list.
describe('navigate kind parameter', () => {
  const CAT = {
    version: 'v', groups: [], functions: [], asset_types: [],
    entries: [entry({ id: 'generator.p_nom', plain_name: 'Capacity', former_ids: ['generator.size'] })],
  }
  beforeEach(() => {
    useUIStore.setState({ uiMode: 'expert', propertiesEditRequest: null, paletteMode: null })
    vi.mocked(toast).mockClear()
  })

  it('the handled kinds, exactly', () => {
    expect([...NAVIGATE_KINDS]).toEqual([
      'navigate', 'open_panel', 'select_component', 'open_properties', 'open_asset_detail',
      'open_run_result', 'set_snapshot', 'parameter',
    ])
  })

  it('opens the entry (by current or former id) on that asset', async () => {
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CAT)
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.size', component_class: 'Generator', name: 'PV roof' })
    await vi.waitFor(() => expect(useUIStore.getState().propertiesEditRequest)
      .toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' }))
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Generator', name: 'PV roof' })
  })

  it('without an asset, opens the palette on the plain name to pick one', async () => {
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CAT)
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.p_nom' })
    await vi.waitFor(() => expect(useUIStore.getState().paletteMode).toBe('all'))
    expect(takePaletteSeed()).toBe('Capacity')
    expect(useUIStore.getState().selectedComponent).toBeNull()
  })

  it.each([
    ['an unknown entry', { entry_id: 'generator.nope', name: 'PV roof' }],
    ['a class that disagrees', { entry_id: 'generator.p_nom', component_class: 'Line', name: 'PV roof' }],
  ])('%s shows a plain notice and moves nothing', async (_, d) => {
    const notice = vi.mocked(toast)
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CAT)
    applyUiNavigate({ kind: 'parameter', ...d })
    await vi.waitFor(() => expect(notice).toHaveBeenCalledWith(UNKNOWN_PARAMETER_NOTICE, PARAMETER_NOTICE_OPTIONS))
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
    expect(useUIStore.getState().selectedComponent).toBeNull()
  })

  it('a setting with no field on an asset says so and moves nothing', async () => {
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue({ ...CAT,
      entries: [entry({ id: 'globalconstraint.constant', plain_name: 'Limit', scope: 'GlobalConstraint' })] })
    applyUiNavigate({ kind: 'parameter', entry_id: 'globalconstraint.constant', name: 'co2' })
    await vi.waitFor(() => expect(vi.mocked(toast)).toHaveBeenCalledWith(NOT_OPENABLE_NOTICE, PARAMETER_NOTICE_OPTIONS))
    expect(useUIStore.getState().selectedComponent).toBeNull()
  })

  it('a catalogue that cannot be loaded says so, not that the setting is unknown', async () => {
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockRejectedValue(new Error('500'))
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.p_nom', name: 'PV roof' })
    await vi.waitFor(() => expect(vi.mocked(toast)).toHaveBeenCalledWith(CATALOGUE_UNAVAILABLE_NOTICE, PARAMETER_NOTICE_OPTIONS))
    expect(vi.mocked(toast)).not.toHaveBeenCalledWith(UNKNOWN_PARAMETER_NOTICE, PARAMETER_NOTICE_OPTIONS)
  })

  it('a navigation after it wins: the late catalogue moves nothing', async () => {
    let resolve: (c: typeof CAT) => void = () => {}
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockReturnValue(new Promise(r => { resolve = r }))
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.p_nom', name: 'PV roof' })
    manualNavigation()
    resolve(CAT)
    await new Promise(r => setTimeout(r, 0))
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
    expect(vi.mocked(toast)).not.toHaveBeenCalled()
  })
})
