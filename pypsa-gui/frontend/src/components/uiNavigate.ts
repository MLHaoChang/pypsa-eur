import { useUIStore } from '../store/uiStore'

/**
 * Map chat tool panel_id → SlidePanel / special navigation targets.
 *
 * Not every value here is a `SlidePanel`. 'topology', 'map', 'properties',
 * 'palette', 'bottom', 'import_export', 'project_picker', 'new_project' and
 * 'chat' name surfaces that live outside `activeSlidePanel`; applyUiNavigate
 * dispatches on each of them explicitly before falling through to the
 * setSlidePanel branch. 'chat' in particular now resolves to the assistant
 * dock, not to a slide panel.
 *
 * Shared with the live companion so a spoken walk uses the same function the
 * chat stream calls when the harness tool `ui_open_panel` emits a ui_event.
 */
function _normalizePanelId(raw: string): string {
  const key = raw.trim()
  const aliases: Record<string, string> = {
    Results: 'results', results: 'results',
    SolverSettings: 'simparams', simparams: 'simparams',
    TimeSeriesManager: 'timeseries', LoadProfileManager: 'timeseries', timeseries: 'timeseries',
    VintagePeriodBoundsModal: 'capacityBounds', capacityBounds: 'capacityBounds',
    OverviewPanel: 'overview', overview: 'overview',
    IssuesPanel: 'issues', issues: 'issues',
    Scenarios: 'scenarios', scenarios: 'scenarios',
    Snapshots: 'snapshots', snapshots: 'snapshots',
    Horizon: 'horizon', horizon: 'horizon',
    SolveQueue: 'solveQueue', solveQueue: 'solveQueue',
    Chat: 'chat', chat: 'chat',
    Compare: 'compare', compare: 'compare',
    Topology: 'topology', topology: 'topology',
    MapCanvas: 'map', map: 'map',
    PropertiesPanel: 'properties', properties: 'properties',
    BottomPanel: 'bottom',
    CommandPalette: 'palette', palette: 'palette',
    ImportExport: 'import_export', import_export: 'import_export',
    GenerationStack: 'results',
    ProjectPicker: 'project_picker', project_picker: 'project_picker',
    OpenProject: 'project_picker',
    NewProject: 'new_project', new_project: 'new_project',
    NewProjectWizard: 'new_project',
    // Guided-mode spec §3.6 — the hub-design panel slot.
    HubDesign: 'hubDesign', hubDesign: 'hubDesign', hub_design: 'hubDesign',
    Workspace: 'workspace', workspace: 'workspace',
    Settings: 'settings', settings: 'settings',
    GridSpine: 'gridspine', gridspine: 'gridspine',
    Reports: 'reports', reports: 'reports',
    CampusElectrical: 'campusElectrical', campusElectrical: 'campusElectrical',
  }
  return aliases[key] ?? key
}

export function applyUiNavigate(d: {
  kind?: string
  panel_id?: string
  results_tab?: string
  bottom_tab?: string
  compare_rail?: boolean
  compare_a?: string
  compare_b?: string
  compare_tab?: string
  component_class?: string
  name?: string
  snapshot_iso?: string
  period?: number | null
  category?: string
  metrics?: string[]
  mode?: 'chronological' | 'duration' | 'monthly'
  chart?: boolean
}) {
  const ui = useUIStore.getState()
  if (d.kind === 'select_component' && d.component_class && d.name) {
    ui.setSelectedComponent({ type: d.component_class, name: d.name })
    ui.openRightPanel()
    return
  }
  if (d.kind === 'open_asset_detail' && d.component_class && d.name) {
    ui.requestAssetDetail({
      componentClass: d.component_class,
      name: d.name,
      category: d.category,
      metrics: d.metrics,
      mode: d.mode,
      chart: d.chart,
    })
    return
  }
  if (d.kind === 'set_snapshot' && d.snapshot_iso) {
    // Snapshot picker is driven by ISO + optional period; store the index
    // is unknown here — open Results so the user sees the picker context.
    ui.setSlidePanel('results')
    return
  }
  // kind === 'navigate' | legacy 'open_panel'
  const panel = d.panel_id ? _normalizePanelId(d.panel_id) : null
  if (panel === 'topology') {
    ui.setSlidePanel(null)
    ui.setCanvasView('blank')
  } else if (panel === 'map') {
    ui.setSlidePanel(null)
    ui.setCanvasView('satellite')
  } else if (panel === 'properties') {
    ui.openRightPanel()
  } else if (panel === 'palette') {
    ui.setPaletteMode('all')
  } else if (panel === 'import_export') {
    ui.requestIoModal('import')
  } else if (panel === 'project_picker') {
    window.dispatchEvent(new CustomEvent('chat:open-project-picker'))
  } else if (panel === 'new_project') {
    window.dispatchEvent(new CustomEvent('chat:open-new-project-wizard'))
  } else if (panel === 'bottom') {
    // Expand bottom panel on a default tab if none specified below.
    if (!d.bottom_tab) ui.requestBottomTab('Buses')
  } else if (panel === 'compare') {
    ui.setSlidePanel('results')
    ui.setCompareRailOpen(true)
  } else if (panel === 'chat') {
    // 'chat' is no longer a SlidePanel member — it resolves to the dock. The
    // agent can still be asked to open the assistant, and doing so no longer
    // evicts whatever view is currently on screen.
    ui.setAssistantDockOpen(true)
  } else if (
    panel === 'results' || panel === 'simparams' || panel === 'timeseries'
    || panel === 'capacityBounds' || panel === 'overview' || panel === 'issues'
    || panel === 'scenarios' || panel === 'snapshots' || panel === 'horizon'
    || panel === 'solveQueue' || panel === 'hubDesign'
    || panel === 'workspace' || panel === 'settings' || panel === 'gridspine'
    || panel === 'reports' || panel === 'campusElectrical'
  ) {
    ui.setSlidePanel(panel as NonNullable<typeof ui.activeSlidePanel>)
  }

  if (d.results_tab) ui.requestResultsTab(d.results_tab)
  if (d.bottom_tab) ui.requestBottomTab(d.bottom_tab)
  if (typeof d.compare_rail === 'boolean') ui.setCompareRailOpen(d.compare_rail)
  if (d.compare_a || d.compare_b || d.compare_tab) {
    ui.requestCompareNav({
      a: d.compare_a,
      b: d.compare_b,
      tab: d.compare_tab,
    })
  }
}
