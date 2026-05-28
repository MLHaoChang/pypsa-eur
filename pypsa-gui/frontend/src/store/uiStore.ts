import { create } from 'zustand'

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
export type SlidePanel = 'timeseries' | 'simparams' | 'horizon' | 'results' | 'snapshots' | 'issues' | 'overview' | 'scenarios' | 'compare' | 'capacityBounds'
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
const AUTOSAVE_KEY = 'network-diagram:autosave'
const OPEN_TABS_KEY = 'network-diagram:open-tabs'
const CANVAS_VIEW_KEY = 'network-diagram:canvas-view'
const RESULTS_OVERLAY_KEY = 'network-diagram:results-overlay'
const LAST_SAVED_KEY = 'network-diagram:last-saved'
const RECENTS_KEY = 'network-diagram:recents'
const THEME_KEY = 'network-diagram:theme'
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

function storedAutosave(): boolean {
  try { return localStorage.getItem(AUTOSAVE_KEY) === 'true' } catch { return false }
}

function storedOpenTabs(): string[] {
  try {
    const raw = localStorage.getItem(OPEN_TABS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((s: unknown): s is string => typeof s === 'string') : []
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
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark') return v
  } catch { /* noop */ }
  // No persisted preference — respect the OS-level setting if available.
  // `matchMedia` is undefined under SSR and on very old browsers; the
  // optional chain falls through to `light` in that case.
  try {
    if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) return 'dark'
  } catch { /* noop */ }
  return 'light'
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

function persistOpenTabs(tabs: string[]) {
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
  resultSource: 'lopf' | 'ac_pf'
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
  currentProject: string | null
  autosaveEnabled: boolean
  openTabs: string[]
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
  setCurrentProject: (name: string | null) => void
  setAutosaveEnabled: (v: boolean) => void
  addTab: (name: string) => void
  closeTab: (name: string) => void
  renameTab: (oldName: string, newName: string) => void
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
  currentProject: storedCurrentProject(),
  autosaveEnabled: storedAutosave(),
  openTabs: (() => {
    const tabs = storedOpenTabs()
    const cur = storedCurrentProject()
    if (cur && !tabs.includes(cur)) tabs.push(cur)
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
  setResultSource: (s) => set({ resultSource: s === 'ac_pf' ? 'ac_pf' : 'lopf' }),
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
  setCurrentProject: (name) => {
    try {
      if (name) localStorage.setItem(CURRENT_PROJECT_KEY, name)
      else localStorage.removeItem(CURRENT_PROJECT_KEY)
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
      }
      if (name && !s.openTabs.includes(name)) {
        const nextTabs = [...s.openTabs, name]
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
  setAutosaveEnabled: (v) => {
    try { localStorage.setItem(AUTOSAVE_KEY, String(v)) } catch { /* noop */ }
    set({ autosaveEnabled: v })
  },
  addTab: (name) => set(s => {
    if (s.openTabs.includes(name)) return s
    const next = [...s.openTabs, name]
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  closeTab: (name) => set(s => {
    const next = s.openTabs.filter(t => t !== name)
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  renameTab: (oldName, newName) => set(s => {
    const next = s.openTabs.map(t => t === oldName ? newName : t)
    persistOpenTabs(next)
    return { openTabs: next }
  }),
  renameProject: (oldName, newName) => set(s => {
    const wasCurrent = s.currentProject === oldName
    // openTabs / recents — substitute the name in-place to preserve order.
    const nextTabs = s.openTabs.map(t => t === oldName ? newName : t)
    const nextRecents = s.recents.map(r => r === oldName ? newName : r)
    // lastSavedByProject — move the timestamp from old key to new key.
    const nextLastSaved: LastSavedMap = {}
    for (const [k, v] of Object.entries(s.lastSavedByProject)) {
      nextLastSaved[k === oldName ? newName : k] = v
    }
    // Persist whichever sub-stores actually changed. Skip a localStorage
    // write if the entry was already absent (saves a no-op write).
    if (wasCurrent) {
      try { localStorage.setItem(CURRENT_PROJECT_KEY, newName) } catch { /* noop */ }
    }
    persistOpenTabs(nextTabs)
    persistRecents(nextRecents)
    persistLastSaved(nextLastSaved)
    return {
      currentProject: wasCurrent ? newName : s.currentProject,
      openTabs: nextTabs,
      recents: nextRecents,
      lastSavedByProject: nextLastSaved,
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
