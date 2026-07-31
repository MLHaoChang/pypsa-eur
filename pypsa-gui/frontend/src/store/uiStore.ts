import { create } from 'zustand'
import type { LockState } from '../utils/lockState'

interface SelectedComponent { type: string; name: string }
// CreationRequest is set when the user wants to add a new asset to the network.
// Two entry points:
//   • Click in AssetPalette → setCreationItem({id, label}) → renders as
//     side panel (legacy in-place form).
//   • Drag from AssetPalette → drop on canvas → setCreationItem({id, label,
//     dropPosition: {x, y}}) → renders as centred modal popup. The drop
//     coordinates are in React Flow space; the canvas applies them to the
//     created node's layout cache (via pendingNodePosition) once the API
//     call completes.
export interface CreationRequest {
  id: string
  label: string
  dropPosition?: { x: number; y: number }
}
// pendingNodePosition is a one-shot handoff used by the drag-drop flow.
// CreationForm sets it after a successful create; TopologyCanvas reads it
// when the next set of nodes mounts and applies the position to posCache
// (then clears it). Cannot piggyback on CreationRequest because that gets
// cleared as soon as the form closes — but the canvas needs the value AFTER
// the new component appears in the React Query cache.
export interface PendingNodePosition { name: string; position: { x: number; y: number } }
export type CanvasMode = 'select' | 'connect'
// `chat` is the chatbot integration v6 panel (Phase 3). It opens a
// half-width slide-panel on the right with the conversation, confirmation
// cards, and live tool-progress streams. Reset via chatStore on project switch.
export type SlidePanel = 'timeseries' | 'simparams' | 'horizon' | 'results' | 'snapshots' | 'issues' | 'overview' | 'scenarios' | 'compare' | 'capacityBounds' | 'solveQueue' | 'chat' | 'workspace'
// Command-palette open mode. `null` = closed. `'all'` = full surface (⌘K).
// `'projects'` = focused project switcher (⌘P).
export type PaletteMode = 'all' | 'projects' | null
export type SidebarMode = 'expanded' | 'icon' | 'hidden'
// Three canvas backgrounds:
//   blank      — current React Flow grid (full edit, drag-to-move)
//   satellite  — Leaflet + Esri World_Imagery tiles
//   hybrid     — satellite with place / boundary labels overlay
export type CanvasView = 'blank' | 'satellite' | 'hybrid'
export type Theme = 'light' | 'dark'
export type Density = 'comfortable' | 'compact'

const SIDEBAR_MODE_KEY = 'network-diagram:sidebar-mode'
const PROJECT_NAME_KEY = 'network-diagram:project-name'
const CURRENT_PROJECT_KEY = 'network-diagram:current-project'
const LAST_PROJECT_ID_KEY = 'network-diagram:last-project-id'
const AUTOSAVE_KEY = 'network-diagram:autosave'
const OPEN_TABS_KEY = 'network-diagram:open-tabs'
const CANVAS_VIEW_KEY = 'network-diagram:canvas-view'
const RESULTS_OVERLAY_KEY = 'network-diagram:results-overlay'
const LAST_SAVED_KEY = 'network-diagram:last-saved'
const RECENTS_KEY = 'network-diagram:recents'
const THEME_KEY = 'network-diagram:theme'
// Generation marker for the persisted theme preference. The store predates
// zustand's `persist` middleware (every preference is written to its own
// localStorage key by hand), so there is no `version`/`migrate` pair to bump —
// this is the minimal equivalent. Bump THEME_SCHEMA whenever the SHIPPED
// DEFAULT theme changes: on the next load a stored value from an older
// generation is dropped exactly once, so sessions that were silently sitting
// on the old default pick up the new one instead of being pinned forever.
// Generation 2 = the dark identity that matches the sign-in page (retuned
// from mint to red without changing the DEFAULT, so no bump was needed).
const THEME_SCHEMA_KEY = 'network-diagram:theme-schema'
const THEME_SCHEMA = '2'
const DENSITY_KEY = 'network-diagram:density'
const COMPARE_RAIL_KEY = 'network-diagram:compare-rail'
const COMPARE_RAIL_WIDTH_KEY = 'network-diagram:compare-rail-width'

const RECENTS_MAX = 5
// Floor for the docked comparison rail width — keeps both the rail and the
// live Results pane usable when the splitter is dragged to an extreme.
const COMPARE_RAIL_MIN_W = 360

function storedSidebarMode(): SidebarMode {
  try {
    const v = localStorage.getItem(SIDEBAR_MODE_KEY)
    if (v === 'expanded' || v === 'icon' || v === 'hidden') return v
  } catch { /* noop */ }
  return 'expanded'
}

function storedProjectName(): string {
  try { return localStorage.getItem(PROJECT_NAME_KEY) || 'Unnamed Network' } catch { return 'Unnamed Network' }
}

function storedCurrentProject(): string | null {
  try { return localStorage.getItem(CURRENT_PROJECT_KEY) || null } catch { return null }
}

function storedLastProjectId(): string | null {
  try { return localStorage.getItem(LAST_PROJECT_ID_KEY) || storedCurrentProject() || null } catch { return storedCurrentProject() }
}

function storedAutosave(): boolean {
  try { return localStorage.getItem(AUTOSAVE_KEY) === 'true' } catch { return false }
}

// An open project tab. `lastInteractedAt` is an LRU signal (B9 eviction) —
// epoch-ms of the last time the user switched TO this tab; 0 when unknown
// (e.g. migrated from the legacy `string[]` shape). In-memory consumers read
// `.name`; the timestamp is persisted so LRU survives reloads.
export interface OpenTab { name: string; lastInteractedAt: number }

function storedOpenTabs(): OpenTab[] {
  try {
    const raw = localStorage.getItem(OPEN_TABS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Migrate BOTH shapes so existing users' localStorage doesn't break:
    //   * legacy `string[]`  → wrap each as {name, lastInteractedAt: 0}
    //   * current `OpenTab[]` → validate name + coerce a finite timestamp
    const out: OpenTab[] = []
    for (const entry of parsed) {
      if (typeof entry === 'string') {
        out.push({ name: entry, lastInteractedAt: 0 })
      } else if (entry && typeof entry === 'object' && typeof (entry as { name?: unknown }).name === 'string') {
        const t = (entry as { lastInteractedAt?: unknown }).lastInteractedAt
        out.push({ name: (entry as { name: string }).name, lastInteractedAt: typeof t === 'number' && Number.isFinite(t) ? t : 0 })
      }
    }
    return out
  } catch { return [] }
}

function storedCanvasView(): CanvasView {
  try {
    const v = localStorage.getItem(CANVAS_VIEW_KEY)
    if (v === 'blank' || v === 'satellite' || v === 'hybrid') return v
  } catch { /* noop */ }
  return 'blank'
}

function storedResultsOverlay(): boolean {
  try { return localStorage.getItem(RESULTS_OVERLAY_KEY) === 'true' } catch { return false }
}

function storedTheme(): Theme {
  try {
    if (localStorage.getItem(THEME_SCHEMA_KEY) !== THEME_SCHEMA) {
      localStorage.removeItem(THEME_KEY)
      localStorage.setItem(THEME_SCHEMA_KEY, THEME_SCHEMA)
    }
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch { /* noop */ }
  // Dark is the product identity — it is what the sign-in page hands off to,
  // so the workbench opens in it regardless of the OS preference. The toggle
  // (and its localStorage write) still lets anyone stay in light.
  return 'dark'
}

function storedDensity(): Density {
  try {
    const v = localStorage.getItem(DENSITY_KEY)
    if (v === 'comfortable' || v === 'compact') return v
  } catch { /* noop */ }
  return 'comfortable'
}

function storedCompareRailOpen(): boolean {
  try { return localStorage.getItem(COMPARE_RAIL_KEY) === 'true' } catch { return false }
}

function storedCompareRailWidth(): number {
  try {
    const v = Number(localStorage.getItem(COMPARE_RAIL_WIDTH_KEY))
    if (Number.isFinite(v) && v >= COMPARE_RAIL_MIN_W) return v
  } catch { /* noop */ }
  return 560
}

function persistOpenTabs(tabs: OpenTab[]) {
  try { localStorage.setItem(OPEN_TABS_KEY, JSON.stringify(tabs)) } catch { /* noop */ }
}

// Map of project-name → ISO timestamp of the last successful save. Kept in
// localStorage so reloading the browser still shows "Saved Xm ago" for known
// projects. Capped to the recents list size so it can't grow unbounded.
type LastSavedMap = Record<string, string>

function storedLastSaved(): LastSavedMap {
  try {
    const raw = localStorage.getItem(LAST_SAVED_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const out: LastSavedMap = {}
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        if (typeof k === 'string' && typeof v === 'string') out[k] = v
      }
      return out
    }
  } catch { /* noop */ }
  return {}
}

function persistLastSaved(map: LastSavedMap) {
  try { localStorage.setItem(LAST_SAVED_KEY, JSON.stringify(map)) } catch { /* noop */ }
}

function storedRecents(): string[] {
  try {
    const raw = localStorage.getItem(RECENTS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed)
      ? parsed.filter((s: unknown): s is string => typeof s === 'string').slice(0, RECENTS_MAX)
      : []
  } catch { return [] }
}

function persistRecents(list: string[]) {
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(list.slice(0, RECENTS_MAX))) } catch { /* noop */ }
}

export interface HighlightedComponent { type: string; name: string; busName?: string }

// Deep-link request into the Asset Detail results tab (Task 13). Four entry
// points funnel through the single `requestAssetDetail` action below so the
// panel, the tab and the selection always move together — see the action's
// own comment for why that matters.
export interface AssetDetailRequest {
  componentClass: string
  name: string
  category?: string
  metrics?: string[]
  mode?: 'chronological' | 'duration' | 'monthly'
  chart?: boolean
}

interface UIStore {
  sidebarExpanded: boolean
  sidebarMode: SidebarMode
  rightPanelOpen: boolean
  selectedComponent: SelectedComponent | null
  highlightedComponent: HighlightedComponent | null
  projectName: string
  bottomPanelHeight: number
  resultsOverlayEnabled: boolean
  // Which result set to read from /results/* — flipped by the AC PF toggle
  // in TopologyCanvas / LoadFlow when Stage 2 is available. Always 'lopf'
  // when no AC PF has been run (the toggle UI is hidden in that case).
  // PER-PROJECT (B8): this top-level field always reflects the CURRENT
  // project's choice; `setCurrentProject` reloads it from
  // `resultSourceByProject` so an instant switch restores the right source.
  // Consumers read this field unchanged.
  resultSource: 'lopf' | 'ac_pf'
  // Per-project store of the result-source choice. In-memory only (resets on
  // reload — a fresh session always defaults to 'lopf' per project). Written by
  // setResultSource alongside the top-level mirror; read by setCurrentProject.
  resultSourceByProject: Record<string, 'lopf' | 'ac_pf'>
  // Which physical quantity the canvas edge overlay shows when results are
  // active. 'p' is the LP and AC-PF default (active power, MW). 'q' is
  // populated only by AC PF (reactive power, MVAr); the toggle UI is hidden
  // when no AC PF run has happened.
  flowOverlayKind: 'p' | 'q'
  // Snapshot index used by the canvas results overlay. Bound to the
  // SnapshotPicker component above the canvas. Persisted in-memory only —
  // resets to 0 on reload, intentional so a fresh session always starts at t=0.
  resultsSnapshotIdx: number
  creationItem: CreationRequest | null
  pendingNodePosition: PendingNodePosition | null
  canvasMode: CanvasMode
  canvasView: CanvasView
  activeSlidePanel: SlidePanel | null
  // True while a project switch/load is mid-flight (between the moment the
  // backend starts swapping the in-memory network and the moment
  // currentProject is updated to the new project). Autosave checks this and
  // skips, so a periodic tick landing in that window can't serialise the
  // newly-loaded network under the OLD project's name (silent cross-project
  // overwrite). In-memory only; defaults false.
  projectSwitchInProgress: boolean
  // Docked comparison rail (lives inside the Results view). Independent of
  // activeSlidePanel so the A-vs-B CompareView can coexist with the live
  // Results tabs and persist across result-tab switches until explicitly
  // closed. Both flags are localStorage-backed so the rail survives reloads.
  compareRailOpen: boolean
  compareRailWidth: number
  bottomTabRequest: string | null
  // Chat / agent navigation: Results sub-tab id (capex, dispatch, …).
  resultsTabRequest: string | null
  // Deep-link into the Asset Detail tab (Task 13). Consumed then cleared by
  // AssetDetail.tsx's effect. Set only via `requestAssetDetail`.
  assetDetailRequest: AssetDetailRequest | null
  // Chat / agent: seed CompareView A/B + tab (consumed then cleared).
  compareNavRequest: { a?: string; b?: string; tab?: string } | null
  // Chat / agent: open Import/Export modal ('import' | 'export').
  ioModalRequest: 'import' | 'export' | null
  // Multi-user edit lock (Task 14). `readOnly` is true when another user holds
  // the active project's lock (or acquisition failed) — every destructive /
  // mutating affordance is gated on it. `lockHolderEmail` names the current
  // holder for the read-only banner. In-memory only (a session concept); auth
  // is required for either to ever be non-default, so the legacy single-user
  // workbench is always writable.
  readOnly: boolean
  lockHolderEmail: string | null
  currentProject: string | null
  // Resume target for auth / projects-home flows. Prefer a stable UUID when a
  // caller knows it, but keep the project name as a compatible fallback so
  // single-user mode and older persisted sessions still resume cleanly.
  lastProjectId: string | null
  autosaveEnabled: boolean
  openTabs: OpenTab[]
  // ISO timestamp of the last save for each known project. UI consumers
  // (StatusBar, ProjectSection) derive "Saved Xm ago" from the active
  // project's entry. Updated by saveMut.onSuccess and project load.
  lastSavedByProject: Record<string, string>
  // Last ~5 recently-opened project names, most-recent-first. Pushed by
  // setCurrentProject; deletions are removed implicitly when missing from
  // the backend list.
  recents: string[]
  // Command palette open mode. Set via the global Ctrl/Cmd+K / Ctrl/Cmd+P
  // handler in App.tsx, plus an explicit "Open palette" entry in the menus.
  // In-memory only — every session starts with the palette closed.
  paletteMode: PaletteMode
  // Theme + density preferences. Persisted; applied to `<html>` via the
  // useEffect in App.tsx that mirrors these into `data-theme` / `data-density`
  // attributes which the CSS-var overrides in index.css key off.
  theme: Theme
  density: Density
  setTheme: (t: Theme) => void
  setDensity: (d: Density) => void
  toggleTheme: () => void
  toggleDensity: () => void
  toggleSidebar: () => void
  setSidebarMode: (mode: SidebarMode) => void
  toggleRightPanel: () => void
  openRightPanel: () => void
  setSelectedComponent: (c: SelectedComponent | null) => void
  setHighlightedComponent: (c: HighlightedComponent | null) => void
  setProjectName: (name: string) => void
  setBottomPanelHeight: (h: number) => void
  setResultsOverlay: (v: boolean) => void
  setResultSource: (s: 'lopf' | 'ac_pf') => void
  setFlowOverlayKind: (k: 'p' | 'q') => void
  setResultsSnapshotIdx: (i: number) => void
  setCreationItem: (item: CreationRequest | null) => void
  setPendingNodePosition: (p: PendingNodePosition | null) => void
  setCanvasMode: (mode: CanvasMode) => void
  setCanvasView: (view: CanvasView) => void
  setSlidePanel: (p: SlidePanel | null) => void
  setProjectSwitchInProgress: (v: boolean) => void
  setCompareRailOpen: (v: boolean) => void
  toggleCompareRail: () => void
  setCompareRailWidth: (px: number) => void
  requestBottomTab: (tab: string) => void
  clearBottomTabRequest: () => void
  requestResultsTab: (tab: string) => void
  clearResultsTabRequest: () => void
  // ONE path for all four entry points (Properties, bottom table, map,
  // chatbot). Each of them only has to call this — the panel, the tab and
  // the selection all move together, so none of them can drift out of step.
  requestAssetDetail: (req: AssetDetailRequest) => void
  clearAssetDetailRequest: () => void
  requestCompareNav: (nav: { a?: string; b?: string; tab?: string }) => void
  clearCompareNavRequest: () => void
  requestIoModal: (tab: 'import' | 'export') => void
  clearIoModalRequest: () => void
  // Apply a derived lock state (from utils/lockState.lockStateFromAcquire).
  setLockState: (s: LockState) => void
  setCurrentProject: (name: string | null, preferredId?: string | null) => void
  setLastProjectId: (id: string | null) => void
  setAutosaveEnabled: (v: boolean) => void
  addTab: (name: string) => void
  closeTab: (name: string) => void
  renameTab: (oldName: string, newName: string) => void
  // Stamp `lastInteractedAt = Date.now()` on the named tab (B9 LRU signal).
  // Called by the switch flow when a project becomes active. No-op if the
  // tab isn't open (defensive).
  touchTab: (name: string) => void
  // Single atomic update for after a successful backend rename: rewrites
  // currentProject (if it was the renamed one), openTabs, recents, and the
  // lastSavedByProject map's key. Saves one render vs. four separate setter
  // calls — and avoids the intermediate state where currentProject points
  // to oldName but openTabs already carries newName.
  renameProject: (oldName: string, newName: string) => void
  markProjectSaved: (name: string, iso?: string) => void
  pruneRecents: (validNames: readonly string[]) => void
  setPaletteMode: (m: PaletteMode) => void
}

export const useUIStore = create<UIStore>((set) => ({
  sidebarExpanded: storedSidebarMode() !== 'hidden',
  sidebarMode: storedSidebarMode(),
  rightPanelOpen: true,
  selectedComponent: null,
  highlightedComponent: null,
  projectName: storedProjectName(),
  bottomPanelHeight: 200,
  resultsOverlayEnabled: storedResultsOverlay(),
  resultSource: 'lopf',
  resultSourceByProject: {},
  flowOverlayKind: 'p',
  resultsSnapshotIdx: 0,
  creationItem: null,
  pendingNodePosition: null,
  canvasMode: 'select',
  canvasView: storedCanvasView(),
  activeSlidePanel: null,
  projectSwitchInProgress: false,
  compareRailOpen: storedCompareRailOpen(),
  compareRailWidth: storedCompareRailWidth(),
  bottomTabRequest: null,
  resultsTabRequest: null,
  assetDetailRequest: null,
  compareNavRequest: null,
  ioModalRequest: null,
  readOnly: false,
  lockHolderEmail: null,
  currentProject: storedCurrentProject(),
  lastProjectId: storedLastProjectId(),
  autosaveEnabled: storedAutosave(),
  openTabs: (() => {
    const tabs = storedOpenTabs()
    const cur = storedCurrentProject()
    if (cur && !tabs.some(t => t.name === cur)) tabs.push({ name: cur, lastInteractedAt: Date.now() })
    return tabs
  })(),
  lastSavedByProject: storedLastSaved(),
  recents: (() => {
    const list = storedRecents()
    const cur = storedCurrentProject()
    // The current project should always be in recents — pin it to the front
    // on first load (covers the case where it was opened via a flow that
    // didn't go through setCurrentProject's recents update path).
    if (cur && !list.includes(cur)) list.unshift(cur)
    return list.slice(0, RECENTS_MAX)
  })(),
  paletteMode: null,
  theme: storedTheme(),
  density: storedDensity(),
  setTheme: (t) => {
    try { localStorage.setItem(THEME_KEY, t) } catch { /* noop */ }
    set({ theme: t })
  },
  setDensity: (d) => {
    try { localStorage.setItem(DENSITY_KEY, d) } catch { /* noop */ }
    set({ density: d })
  },
  toggleTheme: () => set(s => {
    const next: Theme = s.theme === 'light' ? 'dark' : 'light'
    try { localStorage.setItem(THEME_KEY, next) } catch { /* noop */ }
    return { theme: next }
  }),
  toggleDensity: () => set(s => {
    const next: Density = s.density === 'comfortable' ? 'compact' : 'comfortable'
    try { localStorage.setItem(DENSITY_KEY, next) } catch { /* noop */ }
    return { density: next }
  }),
  toggleSidebar: () => set(s => {
    // [ key: cycle icon ↔ hidden; if currently expanded go to icon first
    const next: SidebarMode = s.sidebarMode === 'hidden' ? 'icon' : s.sidebarMode === 'icon' ? 'hidden' : 'icon'
    try { localStorage.setItem(SIDEBAR_MODE_KEY, next) } catch { /* noop */ }
    return { sidebarMode: next, sidebarExpanded: next !== 'hidden' }
  }),
  setSidebarMode: (mode) => {
    try { localStorage.setItem(SIDEBAR_MODE_KEY, mode) } catch { /* noop */ }
    set({ sidebarMode: mode, sidebarExpanded: mode !== 'hidden' })
  },
  toggleRightPanel: () => set(s => ({ rightPanelOpen: !s.rightPanelOpen })),
  openRightPanel: () => set({ rightPanelOpen: true }),
  setSelectedComponent: (c) => set({ selectedComponent: c }),
  setHighlightedComponent: (c) => set({ highlightedComponent: c }),
  setProjectName: (name) => {
    try { localStorage.setItem(PROJECT_NAME_KEY, name) } catch { /* noop */ }
    document.title = name + ' — PyPSA Studio'
    set({ projectName: name })
  },
  setBottomPanelHeight: (h) => set({ bottomPanelHeight: h }),
  setResultsOverlay: (v) => {
    try { localStorage.setItem(RESULTS_OVERLAY_KEY, v ? 'true' : 'false') } catch { /* noop */ }
    set({ resultsOverlayEnabled: v })
  },
  setResultSource: (s) => set(state => {
    const v: 'lopf' | 'ac_pf' = s === 'ac_pf' ? 'ac_pf' : 'lopf'
    // Per-project (B8): write the active project's entry alongside the
    // top-level mirror so a later instant switch restores the right source.
    const byProject = state.currentProject
      ? { ...state.resultSourceByProject, [state.currentProject]: v }
      : state.resultSourceByProject
    return { resultSource: v, resultSourceByProject: byProject }
  }),
  setFlowOverlayKind: (k) => set({ flowOverlayKind: k === 'q' ? 'q' : 'p' }),
  setResultsSnapshotIdx: (i) => set({ resultsSnapshotIdx: Math.max(0, Math.floor(i)) }),
  setCreationItem: (item) => set({ creationItem: item }),
  setPendingNodePosition: (p) => set({ pendingNodePosition: p }),
  setCanvasMode: (mode) => set({ canvasMode: mode }),
  setCanvasView: (view) => {
    try { localStorage.setItem(CANVAS_VIEW_KEY, view) } catch { /* noop */ }
    set({ canvasView: view })
  },
  setSlidePanel: (p) => set({ activeSlidePanel: p }),
  setProjectSwitchInProgress: (v) => set({ projectSwitchInProgress: v }),
  setCompareRailOpen: (v) => {
    try { localStorage.setItem(COMPARE_RAIL_KEY, v ? 'true' : 'false') } catch { /* noop */ }
    set({ compareRailOpen: v })
  },
  toggleCompareRail: () => set(s => {
    const next = !s.compareRailOpen
    try { localStorage.setItem(COMPARE_RAIL_KEY, next ? 'true' : 'false') } catch { /* noop */ }
    return { compareRailOpen: next }
  }),
  setCompareRailWidth: (px) => {
    const w = Math.max(COMPARE_RAIL_MIN_W, Math.round(px))
    try { localStorage.setItem(COMPARE_RAIL_WIDTH_KEY, String(w)) } catch { /* noop */ }
    set({ compareRailWidth: w })
  },
  requestBottomTab: (tab) => set({ bottomTabRequest: tab }),
  clearBottomTabRequest: () => set({ bottomTabRequest: null }),
  requestResultsTab: (tab) => set({ resultsTabRequest: tab }),
  clearResultsTabRequest: () => set({ resultsTabRequest: null }),
  requestAssetDetail: (req) => set({
    assetDetailRequest: req,
    selectedComponent: { type: req.componentClass, name: req.name },
    activeSlidePanel: 'results',
    resultsTabRequest: 'asset',
  }),
  clearAssetDetailRequest: () => set({ assetDetailRequest: null }),
  requestCompareNav: (nav) => set({ compareNavRequest: nav }),
  clearCompareNavRequest: () => set({ compareNavRequest: null }),
  requestIoModal: (tab) => set({ ioModalRequest: tab }),
  clearIoModalRequest: () => set({ ioModalRequest: null }),
  setLockState: (s) => set({ readOnly: s.readOnly, lockHolderEmail: s.holderEmail }),
  setCurrentProject: (name, preferredId) => {
    try {
      if (name) {
        localStorage.setItem(CURRENT_PROJECT_KEY, name)
        localStorage.setItem(LAST_PROJECT_ID_KEY, preferredId ?? name)
      } else {
        localStorage.removeItem(CURRENT_PROJECT_KEY)
      }
    } catch { /* noop */ }
    set(s => {
      // Clear selection on every project switch — the previous project's
      // selected/highlighted component names mean nothing in project B
      // (they may not exist, or worse, may resolve to a DIFFERENT asset
      // that happens to share the name). The PropertiesPanel auto-opens
      // when `selectedComponent` is set; surviving across switches would
      // surface a stale slide-out for an asset the user didn't pick.
      const patch: Partial<UIStore> = {
        currentProject: name,
        selectedComponent: null,
        highlightedComponent: null,
        // Per-project result-source (B8): restore the new project's choice so
        // an instant switch lands on the source the user last picked there.
        // Defaults to 'lopf' for a never-visited / fresh project.
        resultSource: name ? (s.resultSourceByProject[name] ?? 'lopf') : 'lopf',
      }
      if (name) patch.lastProjectId = preferredId ?? name
      if (name && !s.openTabs.some(t => t.name === name)) {
        const nextTabs = [...s.openTabs, { name, lastInteractedAt: Date.now() }]
        persistOpenTabs(nextTabs)
        patch.openTabs = nextTabs
      }
      if (name) {
        // Move-to-front recents update. Dedupe by stripping any existing
        // entry first so the active project is always at index 0.
        const nextRecents = [name, ...s.recents.filter(r => r !== name)].slice(0, RECENTS_MAX)
        persistRecents(nextRecents)
        patch.recents = nextRecents
      }
      return patch
    })
  },
  setLastProjectId: (id) => {
    try {
      if (id) localStorage.setItem(LAST_PROJECT_ID_KEY, id)
      else localStorage.removeItem(LAST_PROJECT_ID_KEY)
    } catch { /* noop */ }
    set({ lastProjectId: id })
  },
  setAutosaveEnabled: (v) => {
    try { localStorage.setItem(AUTOSAVE_KEY, String(v)) } catch { /* noop */ }
    set({ autosaveEnabled: v })
  },
  addTab: (name) => set(s => {
    if (s.openTabs.some(t => t.name === name)) return s
    const next = [...s.openTabs, { name, lastInteractedAt: Date.now() }]
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  closeTab: (name) => set(s => {
    const next = s.openTabs.filter(t => t.name !== name)
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  renameTab: (oldName, newName) => set(s => {
    const next = s.openTabs.map(t => t.name === oldName ? { ...t, name: newName } : t)
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  touchTab: (name) => set(s => {
    let changed = false
    const next = s.openTabs.map(t => {
      if (t.name === name) { changed = true; return { ...t, lastInteractedAt: Date.now() } }
      return t
    })
    if (!changed) return s
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  renameProject: (oldName, newName) => set(s => {
    const wasCurrent = s.currentProject === oldName
    const nextLastProjectId = s.lastProjectId === oldName ? newName : s.lastProjectId
    // openTabs / recents — substitute the name in-place to preserve order
    // (and the tab's LRU timestamp).
    const nextTabs = s.openTabs.map(t => t.name === oldName ? { ...t, name: newName } : t)
    const nextRecents = s.recents.map(r => r === oldName ? newName : r)
    // lastSavedByProject — move the timestamp from old key to new key.
    const nextLastSaved: LastSavedMap = {}
    for (const [k, v] of Object.entries(s.lastSavedByProject)) {
      nextLastSaved[k === oldName ? newName : k] = v
    }
    // resultSourceByProject (B8) — re-key the per-project result source too so
    // the renamed project keeps its lopf/ac_pf choice (in-memory only).
    const nextResultSource: Record<string, 'lopf' | 'ac_pf'> = {}
    for (const [k, v] of Object.entries(s.resultSourceByProject)) {
      nextResultSource[k === oldName ? newName : k] = v
    }
    // Persist whichever sub-stores actually changed. Skip a localStorage
    // write if the entry was already absent (saves a no-op write).
    if (wasCurrent) {
      try { localStorage.setItem(CURRENT_PROJECT_KEY, newName) } catch { /* noop */ }
    }
    if (nextLastProjectId !== s.lastProjectId) {
      try {
        if (nextLastProjectId) localStorage.setItem(LAST_PROJECT_ID_KEY, nextLastProjectId)
        else localStorage.removeItem(LAST_PROJECT_ID_KEY)
      } catch { /* noop */ }
    }
    persistOpenTabs(nextTabs)
    persistRecents(nextRecents)
    persistLastSaved(nextLastSaved)
    return {
      currentProject: wasCurrent ? newName : s.currentProject,
      lastProjectId: nextLastProjectId,
      openTabs: nextTabs,
      recents: nextRecents,
      lastSavedByProject: nextLastSaved,
      resultSourceByProject: nextResultSource,
      // Window title is driven by `projectName` (separate from currentProject);
      // refresh it too so the document title updates immediately.
      projectName: wasCurrent ? newName : s.projectName,
    }
  }),
  markProjectSaved: (name, iso) => set(s => {
    const stamp = iso ?? new Date().toISOString()
    // Don't overwrite a newer in-memory stamp with an older backend value —
    // seeding from the projects list must never roll back a fresh save.
    const existing = s.lastSavedByProject[name]
    if (iso && existing && Date.parse(existing) > Date.parse(iso)) return s
    const nextMap = { ...s.lastSavedByProject, [name]: stamp }
    // Trim to recents so we don't accumulate timestamps for forever-deleted
    // projects. The active project is included automatically because it's
    // pinned to recents[0] by setCurrentProject.
    const allowed = new Set([...s.recents, name])
    const trimmed: LastSavedMap = {}
    for (const k of Object.keys(nextMap)) {
      if (allowed.has(k)) trimmed[k] = nextMap[k]
    }
    persistLastSaved(trimmed)
    return { lastSavedByProject: trimmed }
  }),
  pruneRecents: (validNames) => set(s => {
    const valid = new Set(validNames)
    const next = s.recents.filter(r => valid.has(r))
    if (next.length === s.recents.length) return s
    persistRecents(next)
    return { recents: next }
  }),
  setPaletteMode: (m) => set({ paletteMode: m }),
}))
