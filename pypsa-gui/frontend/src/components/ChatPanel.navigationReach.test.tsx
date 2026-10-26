import { beforeEach, describe, expect, it } from 'vitest'
import { APPLY_UI_NAVIGATE_FOR_TEST as applyUiNavigate } from './ChatPanel'
import { useUIStore } from '../store/uiStore'
import { returnToOrigin } from '../layout/flowNavigation'

beforeEach(() => {
  useUIStore.setState({
    currentProject: 'Demo', activeSlidePanel: null, flowReturn: null, fieldDrawer: null,
    selectedComponent: null, propertiesEditRequest: null, rightPanelOpen: false,
    resultsSnapshotIdx: 0, resultsOverlayEnabled: false, canvasView: 'blank',
  })
})

describe.each(['expert', 'guided'] as const)('navigation reach in %s', mode => {
  beforeEach(() => useUIStore.setState({ uiMode: mode }))

  it('sets the canvas snapshot, shows its overlay and remembers the closed panel', () => {
    useUIStore.setState({ activeSlidePanel: 'results' })
    applyUiNavigate({ kind: 'set_snapshot', snapshot_iso: '2026-06-01T12:00:00', snapshot_index: 12 })
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBeNull()
    expect(ui.resultsSnapshotIdx).toBe(12)
    expect(ui.resultsOverlayEnabled).toBe(true)
    expect(ui.flowReturn?.panel).toBe('results')
    returnToOrigin()
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })

  it('leaves every navigation field unchanged for an old snapshot event without an index', () => {
    useUIStore.setState({ activeSlidePanel: 'timeseries' })
    applyUiNavigate({ kind: 'set_snapshot', snapshot_iso: '2026-06-01T12:00:00' })
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('timeseries')
    expect(ui.resultsSnapshotIdx).toBe(0)
    expect(ui.resultsOverlayEnabled).toBe(false)
    expect(ui.flowReturn).toBeNull()
    const origin = { label: 'Results', mode, section: null, panel: 'results' as const,
      drawer: null, selected: null, scroll: null }
    useUIStore.setState({ flowReturn: origin })
    applyUiNavigate({ kind: 'set_snapshot', snapshot_iso: '2026-06-01T12:00:00' })
    expect(useUIStore.getState().flowReturn).toEqual(origin)
  })

  it('reveals Properties behind a panel and Back restores the panel and Guided drawer', () => {
    const drawer = { section: 'profiles' as const, panel: 'timeseries', component: null, entity: null, field: 'p_set' }
    useUIStore.setState({ activeSlidePanel: 'timeseries', fieldDrawer: mode === 'guided' ? drawer : null })
    applyUiNavigate({ kind: 'select_component', component_class: 'Line', name: 'L2' })
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBeNull()
    expect(ui.selectedComponent).toEqual({ type: 'Line', name: 'L2' })
    expect(ui.rightPanelOpen).toBe(true)
    expect(ui.flowReturn?.panel).toBe('timeseries')
    returnToOrigin()
    expect(useUIStore.getState().activeSlidePanel).toBe('timeseries')
    if (mode === 'guided') expect(useUIStore.getState().fieldDrawer).toEqual(drawer)
    for (const panel of ['issues', null] as const) {
      useUIStore.setState({ activeSlidePanel: panel, flowReturn: null })
      applyUiNavigate({ kind: 'select_component', component_class: 'Line', name: 'L2' })
      expect(useUIStore.getState().activeSlidePanel).toBe(panel)
      expect(useUIStore.getState().flowReturn).toBeNull()
    }
  })

  it('opens the selected component edit form and remembers the closed panel', () => {
    useUIStore.setState({ activeSlidePanel: 'results' })
    applyUiNavigate({ kind: 'open_properties', component_class: 'Line', name: 'L2' })
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBeNull()
    expect(ui.flowReturn?.panel).toBe('results')
    expect(ui.selectedComponent).toEqual({ type: 'Line', name: 'L2' })
    expect(ui.propertiesEditRequest).toEqual({ type: 'Line', name: 'L2' })
    returnToOrigin()
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })

  it('opens every canvas view, preserving defaults and Back to the closed panel', () => {
    for (const view of ['hybrid', 'site', 'satellite'] as const) {
      useUIStore.setState({ activeSlidePanel: 'overview', flowReturn: null })
      applyUiNavigate({ kind: 'navigate', panel_id: 'map', canvas_view: view })
      expect(useUIStore.getState().canvasView).toBe(view)
      expect(useUIStore.getState().activeSlidePanel).toBeNull()
      expect(useUIStore.getState().flowReturn?.panel).toBe('overview')
      returnToOrigin()
      expect(useUIStore.getState().activeSlidePanel).toBe('overview')
    }
    for (const [panel, view] of [['map', 'satellite'], ['topology', 'blank']] as const) {
      useUIStore.setState({ activeSlidePanel: 'overview', flowReturn: null })
      applyUiNavigate({ kind: 'navigate', panel_id: panel })
      expect(useUIStore.getState().canvasView).toBe(view)
      expect(useUIStore.getState().flowReturn?.panel).toBe('overview')
      useUIStore.setState({ activeSlidePanel: null, flowReturn: null })
      applyUiNavigate({ kind: 'navigate', panel_id: panel })
      expect(useUIStore.getState().flowReturn).toBeNull()
    }
  })
})
