import React, { useEffect, useRef, useState } from 'react'
import { Toaster } from 'react-hot-toast'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import AppHeader from './layout/AppHeader'
import ProjectTabs from './layout/ProjectTabs'
import Sidebar from './layout/Sidebar'
import PropertiesPanel from './layout/PropertiesPanel'
import BottomPanel from './layout/BottomPanel'
import StatusBar from './components/StatusBar'
import MapModeSwitcher from './components/MapModeSwitcher'
import SnapshotPicker from './components/SnapshotPicker'
import TopologyCanvas from './pages/TopologyCanvas'
import MapCanvas from './pages/MapCanvas'
import TimeSeriesManager from './pages/TimeSeriesManager'
import SolverSettings from './pages/SolverSettings'
import ModelHorizon from './pages/ModelHorizon'
import CapacityBoundsEditor from './pages/CapacityBoundsEditor'
import Results from './pages/Results'
import SnapshotsPanel from './pages/SnapshotsPanel'
import IssuesPanel from './pages/IssuesPanel'
import OverviewPanel from './pages/OverviewPanel'
import ScenariosPanel from './pages/ScenariosPanel'
import CompareView from './pages/CompareView'
import CommandPalette from './components/CommandPalette'
import CrashRecoveryBanner from './components/CrashRecoveryBanner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useUIStore, type SlidePanel } from './store/uiStore'
import { networkApi } from './api/network'
import { projectsApi } from './api/projects'
import { simulationApi, createLogStream } from './api/simulation'
import { invalidateNetworkQueries } from './utils/projectActions'
import { appLog, useSimulationStore } from './store/simulationStore'

// Top-level boundary so any render error in the header/tabs/sidebar/etc. shows
// a visible error screen instead of whitescreening the entire app.
class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[AppErrorBoundary] caught', error, info)
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center h-screen gap-4 p-8 bg-bg">
          <p className="text-danger font-semibold text-lg">Application crashed</p>
          <pre className="text-xs text-text bg-panel border border-border rounded p-4 max-w-2xl max-h-64 overflow-auto whitespace-pre-wrap">
            {this.state.error.stack || this.state.error.message}
          </pre>
          <p className="text-xs text-muted max-w-md text-center">
            Open the browser DevTools console for details.
            Click below to attempt recovery — your work on the backend is unaffected.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => this.setState({ error: null })}
              className="px-3 py-1.5 border border-border rounded text-sm text-text hover:border-accent hover:text-accent transition-colors"
            >
              Retry
            </button>
            <button
              onClick={() => { try { localStorage.clear() } catch { /* noop */ }; location.reload() }}
              className="px-3 py-1.5 border border-danger/40 text-danger rounded text-sm hover:bg-danger/10 transition-colors"
            >
              Reset UI state &amp; reload
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Sidebar tab panels ───────────────────────────────────────────────────────
// Clicking a sidebar nav item opens the corresponding page as a half-width
// panel down the right side of the main area — the Topology Canvas stays
// visible (shrunk to the other half) rather than being fully replaced. The
// Properties + Bottom panels hide while a tab panel is active. A slim
// breadcrumb header carries a Close affordance; Esc also closes (handled in
// App's global key handler).
const PANEL_META: Record<SlidePanel, { eyebrow: string; title: string }> = {
  overview:   { eyebrow: 'PROJECT',    title: 'Project info' },
  snapshots:  { eyebrow: 'PROJECT',    title: 'Network snapshots' },
  scenarios:  { eyebrow: 'PROJECT',    title: 'Scenarios' },
  compare:    { eyebrow: 'PROJECT',    title: 'Compare scenarios' },
  timeseries: { eyebrow: 'DATA',       title: 'Time series' },
  simparams:  { eyebrow: 'SIMULATION', title: 'Solver settings' },
  horizon:    { eyebrow: 'SIMULATION', title: 'Model horizon' },
  capacityBounds: { eyebrow: 'SIMULATION', title: 'Capacity bounds' },
  issues:     { eyebrow: 'SIMULATION', title: 'Issues' },
  results:    { eyebrow: 'SIMULATION', title: 'Results' },
}

// Tabs that take the whole main area (canvas hidden) rather than opening as a
// half-width panel beside the canvas — their charts, tables, and two-column
// layouts need the full width.
const FULL_SCREEN_TABS = new Set<SlidePanel>(['results', 'timeseries', 'capacityBounds'])

function fullPageContent(panel: SlidePanel): React.ReactNode {
  switch (panel) {
    case 'timeseries': return <TimeSeriesManager />
    case 'simparams':  return <SolverSettings />
    case 'results':    return <Results />
    case 'horizon':    return <ModelHorizon />
    case 'snapshots':  return <SnapshotsPanel />
    case 'issues':     return <IssuesPanel />
    case 'overview':   return <OverviewPanel />
    case 'scenarios':  return <ScenariosPanel />
    // Superseded by the docked comparison rail inside Results (the Compare
    // triggers now open Results + the rail). Kept so the SlidePanel union /
    // PANEL_META stay exhaustive; don't re-point the triggers back here.
    case 'compare':    return <CompareView />
    case 'capacityBounds': return <CapacityBoundsEditor />
    default:           return null
  }
}

function FullPageTab({ panel, onClose }: { panel: SlidePanel; onClose: () => void }) {
  const meta = PANEL_META[panel]
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-bg">
      {/* Slim breadcrumb header — eyebrow / title + Close. */}
      <div className="flex items-center gap-2 px-5 h-9 border-b border-border bg-bg-2 shrink-0">
        <span className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-accent">
          {meta.eyebrow}
        </span>
        <span className="text-border-3">/</span>
        <span className="text-[12px] font-semibold text-text tracking-[-0.005em]">{meta.title}</span>
        <span className="flex-1" />
        <button
          onClick={onClose}
          title="Close (Esc)"
          className="flex items-center gap-1.5 text-[11px] text-muted hover:text-text px-2 py-1 rounded hover:bg-panel transition-colors"
        >
          Close
          <span className="text-[15px] leading-none">×</span>
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">
        {fullPageContent(panel)}
      </div>
    </div>
  )
}

export default function App() {
  const {
    activeSlidePanel, setSlidePanel, currentProject, canvasView,
    lastSavedByProject, markProjectSaved, pruneRecents, recents,
    theme, density, compareRailOpen, setCompareRailOpen,
  } = useUIStore()

  // Results + Time Series take the whole main area (see FULL_SCREEN_TABS);
  // every other sidebar tab opens as a half-width panel beside the canvas.
  const fullScreenTab = activeSlidePanel != null && FULL_SCREEN_TABS.has(activeSlidePanel)

  // Apply theme + density to <html> so CSS-var overrides in index.css kick in.
  // Done on <html> (not <body>) because the @theme block lives at :root scope —
  // routing the data attrs to document.documentElement keeps the selectors
  // consistent (`:root[data-theme="dark"]` not `body[data-theme="dark"]`).
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  useEffect(() => {
    document.documentElement.dataset.density = density
  }, [density])
  const qc = useQueryClient()
  const [recoveryAttempted, setRecoveryAttempted] = useState(false)

  // Backend project list — used in two places: (1) prune recents of names that
  // no longer exist on disk (after a delete from another tab), (2) seed
  // lastSavedByProject from metadata.created_at when the in-memory map has no
  // entry for a known project (typical on a fresh browser session).
  const { data: backendProjects } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
    refetchInterval: 30_000,
  })

  useEffect(() => {
    if (!backendProjects) return
    // Skip prune on an empty response. Empty can mean either (a) genuinely
    // no projects on disk — in which case wiping recents is correct — or (b)
    // backend was briefly unavailable and returned [] from a fallback layer.
    // The cost of (b) is silently wiping the user's recents, which is hard to
    // recover. Conservative choice: only prune when we see at least one
    // project AND keep the current project untouchable regardless.
    if (backendProjects.length > 0) {
      const valid = backendProjects.map(p => p.name)
      // Always preserve the active project — losing it from recents because
      // of a transient list-mismatch is jarring (the row would disappear from
      // the sidebar without the user doing anything).
      if (currentProject && !valid.includes(currentProject)) valid.push(currentProject)
      pruneRecents(valid)
    }
    // Seed timestamps for any recents we don't yet have an in-memory time for.
    // Backend stamps metadata.created_at on every save, so it doubles as
    // "last modified" for the project bundle.
    for (const p of backendProjects) {
      if (!recents.includes(p.name)) continue
      if (lastSavedByProject[p.name]) continue
      if (p.created_at) markProjectSaved(p.name, p.created_at)
    }
  // recents intentionally NOT in deps — we only want to seed once per backend refresh, not loop.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendProjects])

  // Handle ?mode=new: reset network, clear diagram state, strip query param
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('mode') === 'new') {
      networkApi.resetNetwork().then(() => {
        qc.invalidateQueries()
      }).catch(() => {/* noop — page is still usable */})
      try { localStorage.removeItem('network-diagram:default:state') } catch { /* noop */ }
      window.history.replaceState({}, '', window.location.pathname)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Simulation reconnect: on mount, ask the backend whether a solve is in
  // flight. If yes, restore the in-memory store to `status='running'` and
  // open an SSE stream — the backend replays the ring-buffer of historical
  // log lines on connect, then transitions to live. Without this, a page
  // reload (or HMR) strands the user with an empty log + idle status while
  // the backend is still solving. Runs exactly once per mount.
  const simReconnectRef = useRef(false)
  useEffect(() => {
    if (simReconnectRef.current) return
    // Don't claim the ref UNTIL the probe succeeds — if the backend is
    // unreachable on initial mount (server still booting, transient
    // network blip), the old code set the ref to true before the await,
    // and the catch silently returned. The effect's `[]` deps then
    // prevented any future attempt, so the user had to reload the page
    // to recover. Now: probe with a retry-on-failure schedule that uses
    // exponential backoff (capped at 30 s) so a slow-booting backend
    // self-heals without user intervention. Each attempt only sets the
    // ref-permanently when it succeeds AND the worker was running; a
    // healthy idle backend is also a success (nothing to reconnect to,
    // ref stays false so a future page event could retry — though the
    // 'not running' branch returns early which is fine).
    let cleanup: (() => void) | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false
    let attempt = 0
    const RETRY_BASE_MS = 1000
    const RETRY_MAX_MS = 30_000
    const probe = async () => {
      if (cancelled || simReconnectRef.current) return
      try {
        const st = await simulationApi.getStatus()
        // Probe succeeded — we have a definitive answer (running or not).
        // Pin the ref so future re-renders don't re-probe; the standard
        // /api/simulation/status polling takes over from here.
        simReconnectRef.current = true
        if (!st.running) return
        const sim = useSimulationStore.getState()
        sim.setStatus('running')
        sim.clearLog()
        appLog('INFO', 'Reconnected to in-flight simulation')
        cleanup = createLogStream(
          (line) => useSimulationStore.getState().appendLog(line),
          (data) => {
            const d = data as { status?: string; objective?: number | null; solve_time?: number | null }
            const finalStatus = d.status === 'completed' ? 'completed'
              : d.status === 'aborted' ? 'aborted' : 'failed'
            const s = useSimulationStore.getState()
            s.setStatus(finalStatus)
            s.setResult(d.objective ?? null, d.solve_time ?? null)
            qc.invalidateQueries({ queryKey: ['results'] })
            qc.invalidateQueries({ queryKey: ['simulationStatus'] })
          },
          (reason) => {
            useSimulationStore.getState().setStatus('failed')
            appLog('ERROR', `Reconnect: ${reason}`)
          },
        )
      } catch {
        // Status fetch failed — backend likely still booting or unreachable.
        // Schedule a retry with exponential backoff (1s, 2s, 4s, 8s, …, 30s
        // cap). Loop until the backend answers OR the component unmounts.
        if (cancelled) return
        attempt += 1
        const delay = Math.min(RETRY_BASE_MS * 2 ** (attempt - 1), RETRY_MAX_MS)
        retryTimer = setTimeout(() => { retryTimer = null; void probe() }, delay)
      }
    }
    void probe()
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      cleanup?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Cross-project bleed: wipe live solve state (status / KPI / log lines)
  // whenever `currentProject` changes value. Without this, switching from
  // project A (just-solved) to project B leaves StatusBar showing A's
  // "Optimal · 1.23 B€" and BottomPanel showing A's solver log until B
  // runs its own solve. The solver_config is intentionally preserved —
  // it's reloaded from each project's solver_config.json by the load
  // endpoint (and seeded into the store by App's reconnect effect).
  // Tracks the previous value via ref so the very first mount (no
  // previous project) doesn't fire — though resetForProjectSwitch is
  // idempotent on a fresh store anyway.
  const prevProjectForResetRef = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    const prev = prevProjectForResetRef.current
    prevProjectForResetRef.current = currentProject
    if (prev !== undefined && prev !== currentProject) {
      useSimulationStore.getState().resetForProjectSwitch()
    }
  }, [currentProject])

  // Auto-recovery: when the frontend remembers a `currentProject` (restored
  // from localStorage on reload) but the backend's in-memory PyPSA network
  // is empty (uvicorn --reload, server restart, or fresh `pypsa.Network()`),
  // re-load the project from disk so the canvas + tables aren't blank.
  useEffect(() => {
    if (recoveryAttempted) return
    if (!currentProject) return
    let cancelled = false
    ;(async () => {
      try {
        const meta = await networkApi.getMeta()
        if (cancelled) return
        // Heuristic: empty in-memory network. The bus_count check is the
        // strong signal — a freshly-reset PyPSA instance has zero buses.
        if ((meta?.bus_count ?? 0) > 0) return
        // Confirm the project actually exists on disk before attempting to
        // load — avoids 404s for projects that were never persisted.
        const projects = await projectsApi.list().catch(() => [])
        if (cancelled) return
        const exists = projects.some(p => p.name === currentProject)
        if (!exists) {
          appLog('WARN', `currentProject '${currentProject}' has no backend folder; clearing stale tab state`)
          return
        }
        appLog('INFO', `Auto-recovering project '${currentProject}' (backend was empty)`)
        await projectsApi.load(currentProject)
        if (cancelled) return
        invalidateNetworkQueries(qc)
      } catch (e) {
        appLog('WARN', `Auto-recovery for '${currentProject}' failed: ${String((e as Error)?.message ?? e)}`)
      } finally {
        if (!cancelled) setRecoveryAttempted(true)
      }
    })()
    return () => { cancelled = true }
  }, [currentProject, recoveryAttempted, qc])

  // Global keyboard handler:
  //   Esc                → close active slide-panel (existing behaviour)
  //   Ctrl/Cmd + K       → open command palette (full surface)
  //   Ctrl/Cmd + P       → open command palette pre-filtered to projects
  // The palette has its own intra-modal handler for arrow keys / Enter /
  // Esc-while-open — this effect only controls the *trigger*.
  //
  // Critical ordering: `e.preventDefault()` for Ctrl+P MUST fire before any
  // early-return on input focus. Otherwise, when the user is typing in
  // *any* input (including the palette's own search field), the browser
  // print dialog fires uncovered. We therefore check the key first, call
  // preventDefault, and only then decide whether to mutate palette state
  // based on focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Esc closes the docked comparison rail FIRST (if open), so users can
      // dismiss the comparison without also closing the Results view; a
      // second Esc then closes the panel as before.
      if (e.key === 'Escape') {
        if (compareRailOpen) { setCompareRailOpen(false); return }
        if (activeSlidePanel) setSlidePanel(null)
      }
      const modifier = e.ctrlKey || e.metaKey
      if (!modifier) return
      const isPaletteKey =
        e.key === 'k' || e.key === 'K' || e.key === 'p' || e.key === 'P'
      if (!isPaletteKey) return
      // Always suppress the browser shortcut (Cmd+K=focus address bar in
      // some browsers, Cmd+P=print). This call must precede the input check
      // — otherwise typing in any field (including the palette's own
      // search input) bypasses suppression and the print dialog fires.
      e.preventDefault()
      // If the user is typing in an editable element, don't change palette
      // state — they're either inside the palette itself (already open, no
      // need to reopen) or in some other field where stealing focus would
      // be jarring. The browser shortcut is still suppressed above.
      const tag = (e.target as HTMLElement | null)?.tagName
      const isEditable = (e.target as HTMLElement | null)?.isContentEditable
      if (tag === 'INPUT' || tag === 'TEXTAREA' || isEditable) return
      useUIStore
        .getState()
        .setPaletteMode(e.key === 'k' || e.key === 'K' ? 'all' : 'projects')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeSlidePanel, setSlidePanel, compareRailOpen, setCompareRailOpen])

  // Click-outside-to-close for slide panels. Mounts a document `mousedown`
  // listener only while a panel is open. Closes the panel unless the click
  // landed inside:
  //   • the panel itself (panelRef)
  //   • the Sidebar (so users can switch tabs without re-opening)
  //   • elements explicitly marked `data-no-panel-close` (AppHeader, modals,
  //     dropdowns rendered via portals)
  //   • detached / portaled DOM not contained by document.body (defensive)
  // CAPTURE phase is essential — TopologyCanvas / ReactFlow call
  // `e.stopPropagation()` on mousedown in several places (node drag, zone
  // selection, pane interactions). A bubble-phase listener never fires for
  // those clicks. Capture runs BEFORE the stopPropagation point, so we
  // still get the chance to close.
  const panelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!activeSlidePanel) return
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null
      if (!target || !document.body.contains(target)) return
      if (panelRef.current?.contains(target)) return
      if (target.closest('aside')) return
      if (target.closest('[data-no-panel-close]')) return
      setSlidePanel(null)
    }
    document.addEventListener('mousedown', onPointerDown, true)
    return () => document.removeEventListener('mousedown', onPointerDown, true)
  }, [activeSlidePanel, setSlidePanel])

  return (
    <AppErrorBoundary>
    <div className="flex flex-col h-screen overflow-hidden bg-bg">
      {/* ── Crash recovery banner ──────────────────────────────────── */}
      {/* Renders only when a project has an orphaned `.tmp` sibling — a
          tell-tale of a save killed before the atomic rename. Hidden
          (returns null) the rest of the time so it doesn't take vertical
          space. */}
      <CrashRecoveryBanner />

      {/* ── App header ─────────────────────────────────────────────── */}
      <AppHeader />

      {/* ── Project tab strip ──────────────────────────────────────── */}
      <ProjectTabs />

      {/* ── Three-column body ──────────────────────────────────────── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Zone 0 — Sectioned nav sidebar */}
        <Sidebar />

        {/* Zone 2 — Centre: the canvas (+ bottom panel). Most sidebar tabs
            open alongside it as a HALF-WIDTH panel (the canvas stays visible,
            shrunk to the other half); Results takes the whole area. */}
        <div className="flex flex-1 min-w-0 min-h-0 overflow-hidden">

          {/* Canvas column — full width normally, half beside a tab panel,
              hidden while a full-screen tab (Results) occupies the area. Kept
              mounted (display:none, not unmounted) so its zoom/pan + React
              Flow state survive a tab open/close. */}
          <div
            className={`flex flex-col min-h-0 overflow-hidden ${
              fullScreenTab ? 'hidden'
                : activeSlidePanel ? 'w-1/2 min-w-0'
                : 'flex-1 min-w-0'
            }`}
          >
            <div className="relative flex-1 min-h-0 overflow-hidden">
              <ErrorBoundary label="Canvas crashed">
                {canvasView === 'blank'
                  ? <TopologyCanvas />
                  : <MapCanvas mode={canvasView} />}
              </ErrorBoundary>
              {/* Mode switcher floats over whichever canvas is active */}
              <MapModeSwitcher />
              {/* Snapshot picker / playback bar for the results overlay —
                  shown on both the blank schematic and the satellite/hybrid map */}
              <SnapshotPicker />
            </div>
            {/* Zone 4 — Bottom tabbed panel (canvas only) */}
            <BottomPanel />
          </div>

          {/* Tab panel — half-width beside the canvas, full-width for Results. */}
          {activeSlidePanel && (
            <div
              ref={panelRef}
              className={`min-w-0 flex flex-col min-h-0 overflow-hidden ${
                fullScreenTab ? 'flex-1' : 'w-1/2 border-l border-border'
              }`}
            >
              {/* Boundary keyed on panel + project so a crash in any full-page
                  tab (Results/Compare/Economics/…) shows an inline fallback
                  instead of whitescreening the app, and navigating to another
                  tab or switching project remounts it (clearing the error). */}
              <ErrorBoundary key={`${activeSlidePanel}-${currentProject ?? ''}`} label="This panel crashed">
                <FullPageTab panel={activeSlidePanel} onClose={() => setSlidePanel(null)} />
              </ErrorBoundary>
            </div>
          )}
        </div>

        {/* Zone 3 — Right properties panel. Only renders alongside the
            Topology Canvas — hidden while a tab panel occupies the right half. */}
        {!activeSlidePanel && <PropertiesPanel />}
      </div>

      <StatusBar />
      <div data-no-panel-close>
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              fontSize: 13,
              background: 'var(--color-panel)',
              color: 'var(--color-text)',
              border: '1px solid var(--color-border)',
            },
          }}
        />
      </div>

      <CommandPalette />
    </div>
    </AppErrorBoundary>
  )
}
