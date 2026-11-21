import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NAVIGATE_KINDS, applyUiNavigate } from './uiNavigate'
import * as catalogueApi from '../catalogue/api'
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
  it('is one of the handled navigate kinds', () => {
    expect(NAVIGATE_KINDS).toContain('parameter')
  })

  it('opens the entry (by current or former id) on that asset', async () => {
    useUIStore.setState({ uiMode: 'expert', propertiesEditRequest: null })
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue({
      version: 'v', groups: [], functions: [], asset_types: [],
      entries: [entry({ id: 'generator.p_nom', plain_name: 'Capacity', former_ids: ['generator.size'] })],
    })
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.size', component_class: 'Generator', name: 'PV roof' })
    await vi.waitFor(() => expect(useUIStore.getState().propertiesEditRequest)
      .toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' }))
    expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Generator', name: 'PV roof' })
  })

  it('an unknown entry, a class that disagrees, or a later navigation moves nothing', async () => {
    useUIStore.setState({ uiMode: 'expert', propertiesEditRequest: null })
    const fetch = vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue({
      version: 'v', groups: [], functions: [], asset_types: [],
      entries: [entry({ id: 'generator.p_nom', plain_name: 'Capacity' })],
    })
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.nope', name: 'PV roof' })
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.p_nom', component_class: 'Line', name: 'PV roof' })
    applyUiNavigate({ kind: 'parameter', entry_id: 'generator.p_nom', name: 'PV roof' })
    manualNavigation()
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
    await new Promise(r => setTimeout(r, 0))
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
    expect(useUIStore.getState().selectedComponent).toBeNull()
  })
})
