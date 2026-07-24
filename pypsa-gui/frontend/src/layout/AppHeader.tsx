import { Play, Zap, Search, Loader, PanelRightClose, PanelRightOpen, Undo2, Save, Check, Command } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSimulationStore, appLog } from '../store/simulationStore'
import { networkApi } from '../api/network'
import { simulationApi, createLogStream } from '../api/simulation'
import { projectsApi } from '../api/projects'
import { downloadProjectBundle, saveProjectQuietly, type BundleSaveResult } from '../utils/projectActions'
import { useSolveQueue, useEnqueueSolve, useAbortJob, activeJobForProject } from '../hooks/useSolveQueue'
import { nk } from '../utils/queryKeys'
import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useUIStore } from '../store/uiStore'
import type { Bus, FailureInfo, Generator, Line, Link, Load, StorageUnit } from '../api/types'
import toast from 'react-hot-toast'

// ── Status indicators ──────────────────────────────────────────────────────────
const STATUS_DOT: Record<string, string> = {
  idle: 'bg-[#9ca3af]', running: 'bg-[#d97706] animate-pulse',
  completed: 'bg-[#16a34a]', aborted: 'bg-[#9ca3af]', failed: 'bg-[#f85149]',
}
const STATUS_LABEL: Record<string, string> = {
  idle: 'Idle', running: 'Running…', completed: 'Optimal', aborted: 'Aborted', failed: 'Failed',
}

// ── Search types ───────────────────────────────────────────────────────────────
type ResultType = 'Bus' | 'Line' | 'Generator' | 'StorageUnit' | 'Load' | 'Link'
interface SearchResult { type: ResultType; name: string; busName?: string }

const TAB_FOR_TYPE: Record<ResultType, string> = {
  Bus: 'Buses', Line: 'Lines', Generator: 'Generators',
  StorageUnit: 'Storage', Load: 'Loads', Link: 'Links',
}
const TYPE_LABEL: Record<ResultType, string> = {
  Bus: 'Bus', Line: 'Line', Generator: 'Generator',
  StorageUnit: 'Storage', Load: 'Load', Link: 'Link',
}

function TypeIcon({ type }: { type: ResultType }) {
  const s = 13
  if (type === 'Bus') return (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none">
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="2" />
      <circle cx="10" cy="10" r="2.5" fill="currentColor" />
    </svg>
  )
  if (type === 'Line') return (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="2" y1="10" x2="18" y2="10" />
      <line x1="6" y1="7" x2="6" y2="13" />
      <line x1="10" y1="7" x2="10" y2="13" />
      <line x1="14" y1="7" x2="14" y2="13" />
    </svg>
  )
  if (type === 'Generator') return (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="currentColor">
      <path d="M10 2L12 8H18L13.5 11.5L15.5 18L10 14.5L4.5 18L6.5 11.5L2 8H8Z" />
    </svg>
  )
  if (type === 'StorageUnit') return (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="6" width="14" height="10" rx="2" />
      <path d="M7 6V4M13 6V4" strokeLinecap="round" />
      <path d="M7 12L9 10L11 12L13 10" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
  if (type === 'Load') return (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M10 3L17 14H3Z" strokeLinejoin="round" />
      <line x1="10" y1="14" x2="10" y2="17" strokeLinecap="round" />
    </svg>
  )
  return (
    <svg width={s} height={s} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="4" cy="10" r="2.5" />
      <circle cx="16" cy="10" r="2.5" />
      <line x1="6.5" y1="10" x2="13.5" y2="10" strokeDasharray="2 2" />
    </svg>
  )
}

// ── AppHeader ──────────────────────────────────────────────────────────────────
export default function AppHeader() {
  const { status, setStatus, clearLog, appendLog } = useSimulationStore()
  const {
    projectName, setProjectName,
    rightPanelOpen, toggleRightPanel, openRightPanel,
    setSelectedComponent, setHighlightedComponent,
    requestBottomTab,
    currentProject, setCurrentProject, addTab, markProjectSaved,
    renameProject: renameProjectInStore,
    setPaletteMode,
  } = useUIStore()
  const queryClient = useQueryClient()

  // Platform-correct modifier label for the command-palette hint chip.
  // App.tsx binds both Ctrl+K and Cmd+K; we just display the right one.
  const cmdKey = useMemo(
    () => (typeof navigator !== 'undefined'
      && /Mac|iPhone|iPad|iPod/.test(navigator.platform) ? '⌘' : 'Ctrl'),
    [],
  )

  // ── Project name editing ───────────────────────────────────────────────────
  const [editingName, setEditingName] = useState(false)
  const [nameInput, setNameInput] = useState(projectName)
  const nameRef = useRef<HTMLInputElement>(null)

  useEffect(() => { document.title = projectName + ' — PyPSA Studio' }, [projectName])

  // commitName has two paths:
  //   • Active project loaded (currentProject is set) → call the rename
  //     endpoint so the on-disk directory is moved, metadata + child
  //     scenarios are reparented, and the in-memory n.name stays in sync.
  //     A failed rename rolls the input back to projectName so the user
  //     doesn't lose context.
  //   • No active project (e.g. fresh workspace, the user is just typing a
  //     draft name to use on the next save) → keep the legacy behaviour and
  //     only update local state. The Save flow at line ~336 will use this
  //     value as the suggested directory name.
  const commitName = useCallback(() => {
    const trimmed = nameInput.trim().slice(0, 64)  // match backend's 64-char limit
    setEditingName(false)
    if (!trimmed || trimmed === projectName) return
    if (currentProject && currentProject === projectName) {
      // Active project — call backend. Cache invalidation + store sync are
      // handled in the .then/.catch so the UI stays consistent across
      // sidebar / OverviewPanel / Compare scenarios picker.
      projectsApi.rename(currentProject, trimmed)
        .then(() => {
          renameProjectInStore(currentProject, trimmed)
          queryClient.invalidateQueries({ queryKey: ['projects'] })
          queryClient.invalidateQueries({ queryKey: ['compare-state', currentProject] })
          queryClient.invalidateQueries({ queryKey: ['results-summary', currentProject] })
          queryClient.invalidateQueries({ queryKey: nk(currentProject, 'meta') })
          queryClient.invalidateQueries({ queryKey: ['changelog'] })
          toast.success(`Renamed to '${trimmed}'`)
        })
        .catch((e: { response?: { data?: { detail?: unknown } }; message?: string }) => {
          const detail = e.response?.data?.detail
          toast.error(typeof detail === 'string' ? detail : (e.message || 'Rename failed'))
          // Roll the displayed name back so the user can see what was kept.
          setProjectName(projectName)
        })
      // Optimistic UI: render the new name immediately. Rolled back on error.
      setProjectName(trimmed)
    } else {
      // Draft / unsaved — legacy behaviour.
      setProjectName(trimmed)
    }
  }, [nameInput, projectName, currentProject, setProjectName, renameProjectInStore, queryClient])

  const startEditName = useCallback(() => {
    setNameInput(projectName)
    setEditingName(true)
    // The button is about to unmount and the <input> ref is on a not-yet-
    // mounted element, so deferring is necessary. `.select()` alone doesn't
    // put focus on the input — chain `.focus()` first so the rename UX works
    // when triggered from outside (e.g. sidebar's project header card calling
    // .focus() on the button, which then unmounts).
    setTimeout(() => {
      nameRef.current?.focus()
      nameRef.current?.select()
    }, 0)
  }, [projectName])

  // ── Search ─────────────────────────────────────────────────────────────────
  const [searchVal, setSearchVal] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(-1)
  const searchRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchVal), 200)
    return () => clearTimeout(t)
  }, [searchVal])

  useEffect(() => { setSelectedIdx(-1) }, [debouncedSearch])

  const searchActive = debouncedSearch.trim().length >= 2

  const { data: buses = [] }      = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses })
  const { data: lines = [] }      = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: generators = [] } = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: sus = [] }        = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: loads = [] }      = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: links = [] }      = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })
  const { data: meta }            = useQuery({ queryKey: nk(currentProject, 'meta'),          queryFn: networkApi.getMeta, refetchInterval: 30000 })

  const searchResults = useMemo((): SearchResult[] => {
    if (!searchActive) return []
    const q = debouncedSearch.toLowerCase()
    const results: SearchResult[] = []
    ;(buses as Bus[]).filter(b => b.name.toLowerCase().includes(q))
      .forEach(b => results.push({ type: 'Bus', name: b.name }))
    ;(lines as Line[]).filter(l => l.name.toLowerCase().includes(q) || l.bus0?.toLowerCase().includes(q) || l.bus1?.toLowerCase().includes(q))
      .forEach(l => results.push({ type: 'Line', name: l.name, busName: l.bus0 }))
    ;(generators as Generator[]).filter(g => g.name.toLowerCase().includes(q) || g.bus?.toLowerCase().includes(q))
      .forEach(g => results.push({ type: 'Generator', name: g.name, busName: g.bus }))
    ;(sus as StorageUnit[]).filter(s => s.name.toLowerCase().includes(q) || s.bus?.toLowerCase().includes(q))
      .forEach(s => results.push({ type: 'StorageUnit', name: s.name, busName: s.bus }))
    ;(loads as Load[]).filter(l => l.name.toLowerCase().includes(q) || l.bus?.toLowerCase().includes(q))
      .forEach(l => results.push({ type: 'Load', name: l.name, busName: l.bus }))
    ;(links as Link[]).filter(l => l.name.toLowerCase().includes(q) || l.bus0?.toLowerCase().includes(q))
      .forEach(l => results.push({ type: 'Link', name: l.name, busName: l.bus0 }))
    return results.slice(0, 10)
  }, [searchActive, debouncedSearch, buses, lines, generators, sus, loads, links])

  const selectResult = useCallback((result: SearchResult) => {
    setSelectedComponent({ type: result.type, name: result.name })
    setHighlightedComponent({ type: result.type, name: result.name, busName: result.busName })
    requestBottomTab(TAB_FOR_TYPE[result.type])
    openRightPanel()
    setSearchVal('')
    setDebouncedSearch('')
  }, [setSelectedComponent, setHighlightedComponent, requestBottomTab, openRightPanel])

  const handleSearchKey = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelectedIdx(i => Math.min(i + 1, searchResults.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSelectedIdx(i => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter' && selectedIdx >= 0) selectResult(searchResults[selectedIdx])
    else if (e.key === 'Escape') { setSearchVal(''); setSelectedIdx(-1) }
  }, [searchResults, selectedIdx, selectResult])

  useEffect(() => {
    const closeSearch = () => {
      setSearchVal('')
      setDebouncedSearch('')
      setSelectedIdx(-1)
    }
    const onDown = (e: MouseEvent) => {
      // Bail if the click is inside the search input itself — user is still
      // interacting with it.
      if (searchRef.current?.contains(e.target as Node)) return
      // Bail if inside the dropdown — user is about to click a result.
      // The result's onClick clears the search on success.
      // When the dropdown isn't rendered yet (no results), `dropdownRef.current`
      // is null and we fall through to the clear.
      if (dropdownRef.current?.contains(e.target as Node)) return
      // Outside click — clear synchronously so the dropdown unmounts on the
      // same frame as the click (skips the 200 ms debounce window).
      closeSearch()
    }
    // CAPTURE PHASE (`true` as the third arg). The bubble-phase pattern fails
    // when descendant handlers (React Flow's pane, the ProjectTabs strip,
    // Leaflet's panes, etc.) call `e.stopPropagation()` on mousedown — the
    // document-level listener never sees the event. Capture-phase listeners
    // fire FIRST, before any descendant has a chance to stop propagation, so
    // this guarantees the close-on-outside behaviour regardless of who
    // captures the event downstream.
    const onKey = (e: KeyboardEvent) => {
      // Global Escape — works even when focus has moved off the input
      // (e.g. user tabbed away but the dropdown is still on screen because
      // the debounced search hasn't cleared yet, or the focus was stolen
      // by a click on another element that doesn't process Escape).
      if (e.key === 'Escape') closeSearch()
    }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [])

  // ── Undo ───────────────────────────────────────────────────────────────────
  // queryClient is declared at the top of the component (next to the
  // useUIStore destructure) so commitName can use it for cache invalidation.
  const { data: undoInfoData } = useQuery({
    queryKey: nk(currentProject, 'undoInfo'),
    queryFn: networkApi.undoInfo,
    refetchInterval: 3000,
    staleTime: 0,
  })
  const undoDepth = undoInfoData?.depth ?? 0

  const ALL_NETWORK_KEYS = ['buses', 'lines', 'links', 'generators', 'loads', 'storage_units',
    'stores', 'transformers', 'meta', 'load_profiles', 'generator_profiles', 'link_profiles',
    'timeseries', 'carriers', 'snapshots', 'undoInfo']

  const undoMut = useMutation({
    mutationFn: networkApi.undo,
    onSuccess: (data) => {
      const proj = useUIStore.getState().currentProject
      ALL_NETWORK_KEYS.forEach(k => queryClient.invalidateQueries({ queryKey: nk(proj, k) }))
      toast.success(`Undone · ${data.remaining} step${data.remaining !== 1 ? 's' : ''} left`)
    },
    onError: () => toast.error('Nothing to undo'),
  })

  const handleUndo = useCallback(() => {
    if (undoDepth === 0 || undoMut.isPending) return
    undoMut.mutate()
  }, [undoDepth, undoMut])

  // ── Save shortcut ──────────────────────────────────────────────────────────
  // Save = persist current network to the backend project folder AND download
  // the bundle (.pypsaproj.zip) via the OS save-file picker. The backend save
  // clears the undo stack on success (clear_undo defaults to true), making the
  // saved state the new baseline. Mirrors the Sidebar's Save behaviour so
  // Ctrl+S, the header button, and the menu item are all equivalent.
  const [savedFlash, setSavedFlash] = useState(false)
  // Track the savedFlash timer so we can clear it on unmount — otherwise
  // a save that completes just before navigation triggers a 1.5s state-update
  // on an unmounted AppHeader and React 18 logs a warning. The ref persists
  // across re-renders; the cleanup effect catches both unmount and re-saves
  // that override an in-flight flash.
  const savedFlashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (savedFlashTimerRef.current) clearTimeout(savedFlashTimerRef.current)
  }, [])
  const saveMut = useMutation({
    mutationFn: async (name: string) => {
      // Assert identity when re-saving the ACTIVE project so the backend
      // refuses (409) if its in-memory network was swapped to a different
      // project. A "Save As" (name !== currentProject) omits expect so the
      // write under the new name is allowed. rebind=true because onSuccess
      // promotes the saved name to the active project (first-save / Save-As) —
      // the backend must follow so subsequent saves don't 409 on a stale bind.
      const expect = name === currentProject ? name : undefined
      const result = await projectsApi.save(name, false, true, expect, true)
      // Flush the diagram layout (node positions + edge waypoints) to
      // layout.json AFTER the network save. Order matters: the project
      // directory must exist on disk before PUT /projects/{name}/layout
      // will accept the write (it 404s on missing projects). With this
      // flush the next load reseeds saved positions instead of running
      // the layout algorithm from scratch.
      // Lazy import sidesteps the cycle (TopologyCanvas → projectsApi).
      let layoutHint = ''
      try {
        const { flushPendingLayoutToServer } = await import('../pages/TopologyCanvas')
        const r = await flushPendingLayoutToServer(result.saved)
        if (r.status === 'server') {
          appLog('INFO', `Layout saved · ${r.nodes} node(s) + ${r.edges} edge(s) → layout.json`)
          layoutHint = ` · layout: ${r.nodes}n/${r.edges}e`
        } else if (r.status === 'local') {
          appLog('WARN', `Layout server write failed — kept ${r.nodes} node(s) + ${r.edges} edge(s) in localStorage`)
          layoutHint = ` · ⚠ layout to localStorage only`
        }
      } catch (e) {
        appLog('WARN', `Layout flush failed for '${result.saved}': ${String((e as Error)?.message ?? e)}`)
        layoutHint = ` · ⚠ layout flush failed`
      }
      // Bundle download happens immediately after the backend write so a
      // hardware/network failure during download still leaves a valid project
      // on the server — the user can re-export from Project → Export later.
      // No options → use cache-first behaviour: prompt only on the first save
      // for this project, write directly to the chosen file thereafter.
      let downloadMode: BundleSaveResult = 'cancelled'
      try {
        downloadMode = await downloadProjectBundle(result.saved)
      } catch (e) {
        appLog('WARN', `Bundle download failed (server save still succeeded): ${String((e as Error)?.message ?? e)}`)
      }
      return { ...result, downloadMode, layoutHint }
    },
    onSuccess: (result) => {
      const tsCols = result.ts_columns_saved ?? 0
      appLog('INFO', `Save '${result.saved}': ${result.bus_count} buses, ${tsCols} ts columns`)
      const suffix = result.downloadMode === 'cancelled'
        ? ' (server only)'
        : result.downloadMode === 'download' ? ' — downloaded'
        : result.downloadMode === 'reused' ? ' — updated saved file'
        : '' // 'picker' — first save / location chosen
      toast.success(`Saved · ${result.saved}${suffix}${result.layoutHint}`)
      setSavedFlash(true)
      // Cancel any in-flight flash timer before scheduling a new one so
      // back-to-back saves don't queue multiple deferred resets.
      if (savedFlashTimerRef.current) clearTimeout(savedFlashTimerRef.current)
      savedFlashTimerRef.current = setTimeout(() => {
        setSavedFlash(false)
        savedFlashTimerRef.current = null
      }, 1500)
      // Promote the just-saved project to the active project / open tab when
      // this was the first save (no currentProject yet). Without this, the
      // next Ctrl+S would prompt for a name again instead of overwriting.
      if (currentProject !== result.saved) {
        setCurrentProject(result.saved)
        setProjectName(result.saved)
        addTab(result.saved)
      }
      // Stamp the save time so StatusBar / ProjectSection can render
      // "Saved Xm ago" without polling the backend.
      markProjectSaved(result.saved)
      // Depth counter polls every 3s; force-refresh so the badge clears now.
      // Scope to the just-saved project (which becomes the active project on a
      // first save) so a background project's depth badge isn't disturbed.
      queryClient.invalidateQueries({ queryKey: nk(result.saved, 'undoInfo') })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      // The compare view caches per-project state keyed ['compare-state', name].
      // A save changes the saved-state the compare endpoint reads from disk —
      // invalidate so an open Compare panel reflects the new numbers instead
      // of waiting out its staleTime.
      queryClient.invalidateQueries({ queryKey: ['compare-state', result.saved] })
    },
    onError: (e: unknown) => {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 409) {
        toast.error('Cannot save: network is empty. Load the project first.')
        return
      }
      toast.error('Save failed')
      appLog('ERROR', `Save failed: ${String((e as Error)?.message ?? e)}`)
    },
  })

  const handleQuickSave = useCallback(() => {
    // No active project yet ⇒ prompt for a name and save under that. Avoids
    // dead-ending the user with a "create a project first" instruction —
    // they already have a network in memory and just want to save it.
    let name = currentProject
    if (!name) {
      // Default name comes from the header's editable project name (which
      // the user may have already changed) — slugify-light so the path is
      // safe. Empty input cancels the save.
      const suggestion = (projectName || 'untitled').trim().replace(/\s+/g, '_')
      const answer = window.prompt('Name this project to save it:', suggestion)
      if (answer === null) return  // user clicked Cancel
      name = answer.trim()
      if (!name) {
        toast.error('Save cancelled — name is required.')
        return
      }
    }
    saveMut.mutate(name)
  }, [currentProject, projectName, saveMut])

  // Global Ctrl+S / Cmd+S shortcut for save
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && !e.shiftKey) {
        const tag = (e.target as HTMLElement)?.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) return
        e.preventDefault()
        handleQuickSave()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleQuickSave])

  // Global Ctrl+Z / Cmd+Z shortcut
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        // Don't intercept when user is typing in an input/textarea
        const tag = (e.target as HTMLElement)?.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) return
        e.preventDefault()
        handleUndo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleUndo])

  // ── Simulation actions ─────────────────────────────────────────────────────
  const isRunning = status === 'running'

  // The solver_config.mode tells us what "Run" actually does — LOPF (full
  // optimisation) or PF (AC Newton-Raphson power flow). The button
  // label reflects this so the user always knows what the next click triggers.
  // Polled at 4 s so a change made in Solver Settings shows up promptly without
  // hammering the backend.
  const { data: solverCfg } = useQuery({
    queryKey: nk(currentProject, 'solverConfig'),
    queryFn: simulationApi.getSolverConfig,
    refetchInterval: 4000,
  })
  const mode = (solverCfg?.mode ?? 'lopf').toLowerCase()
  const MODE_LABEL: Record<string, string> = {
    lopf: 'LOPF',         // linear optimal power flow (capacity exp + UC + dispatch)
    lpf:  'Load Flow',    // DC linear power flow
    pf:   'AC Power Flow',// non-linear Newton-Raphson
  }
  // Plain-language description of what the current Run mode actually does, so a
  // non-expert hovering the button understands LOPF vs power-flow without
  // opening Solver Settings.
  const MODE_DESC: Record<string, string> = {
    lopf: 'optimise capacity expansion, unit commitment & dispatch to minimise cost',
    lpf:  'solve a linear (DC) power flow on the current dispatch',
    pf:   'solve a non-linear (AC) Newton-Raphson power flow on the current dispatch',
  }
  const runLabel = `Run ${MODE_LABEL[mode] ?? mode.toUpperCase()}`

  // SSE cleanup ref — close the stream on Abort / unmount so a long-lived
  // EventSource doesn't leak across re-renders.
  const esCleanupRef = useRef<(() => void) | null>(null)
  // Bounded retry for the rare "auto-attached the SSE a hair before the
  // dispatcher set the active ctx's log_queue" race (job.status flips to
  // 'running' in the queue a few statements before ctx_state_update sets
  // log_queue, so a 1.5s poll can land in that microsecond gap). On an early
  // stream error we reset the attach guard so the next poll re-attaches —
  // bounded so a genuinely dead stream still surfaces as 'failed'.
  const attachRetryRef = useRef<{ id: number; tries: number }>({ id: -1, tries: 0 })
  // Latch covering the idle-click enqueue path (pre-save await + enqueue), so a
  // fast double-click can't stack two jobs before enqueue.isPending flips.
  const enqueuingRef = useRef(false)
  const qcRef = useQueryClient()

  // ── Solve queue wiring ───────────────────────────────────────────────────
  // Run now ENQUEUES the foreground project: the FIFO dispatcher solves it
  // immediately if the slot is free, or queues it behind a running solve.
  const { data: solveQueue } = useSolveQueue()
  const enqueue = useEnqueueSolve()
  const abort = useAbortJob()
  // The current project's active (queued|running) queue job, if any. Drives
  // the smart Run/Queued/Abort button + the status pill.
  const myJob = currentProject ? activeJobForProject(solveQueue, currentProject) : undefined
  // Tracks the job id we've already opened the SSE for, so the auto-attach
  // effect opens the stream EXACTLY once per run (not on every 1.5s poll).
  const attachedJobRef = useRef<number | null>(null)

  // The single SSE-open closure, shared by the auto-attach effect (and kept
  // identical to the historical direct-/run done/err logic). `runLabel` is
  // read via the ref-captured closure value at attach time. On `done` we also
  // invalidate ['solveQueue'] so the queue UI reflects the finished job.
  const openLogStream = useCallback((label: string, jobId?: number) => {
    esCleanupRef.current?.()
    esCleanupRef.current = createLogStream(
      (line) => {
        // First real line confirms the stream is live — reset the retry budget
        // for this job so a LATE blip doesn't consume a re-attach.
        if (jobId != null && attachRetryRef.current.id === jobId) {
          attachRetryRef.current.tries = 0
        }
        appendLog(line)
      },
      (data) => {
        const d = data as {
          status?: string; objective?: number | null; solve_time?: number | null
          condition?: string | null; failure?: FailureInfo | null
        }
        const finalStatus = d.status === 'completed' ? 'completed'
          : d.status === 'aborted' ? 'aborted'
          : 'failed'
        setStatus(finalStatus)
        if (finalStatus === 'completed') {
          appLog('INFO',
            `${label} completed: objective=${d.objective ?? 'n/a'}, solve_time=${d.solve_time ?? 'n/a'} s`,
          )
          // Invalidate every result query so the Results panel and any open
          // result charts re-fetch the freshly populated DataFrames. Read the
          // live active project via getState() — this done handler fires
          // asynchronously, long after the closure was created.
          const proj = useUIStore.getState().currentProject
          qcRef.invalidateQueries({ queryKey: nk(proj, 'results') })
          qcRef.invalidateQueries({ queryKey: nk(proj, 'simulationStatus') })
        } else if (finalStatus === 'failed') {
          // Surface the actionable failure card from the backend taxonomy:
          // store it (drives the IssuesPanel banner), auto-open the Issues
          // panel, and toast the headline. getState() — same async-closure
          // reasoning as the completed branch above.
          const fail = d.failure ?? null
          appLog('ERROR', fail ? `${label} failed — ${fail.title}: ${fail.hint}` : `${label} failed`)
          useSimulationStore.getState().setLastFailure(fail)
          if (fail) {
            // Don't yank the user out of a panel they deliberately opened mid-
            // solve; only auto-open Issues when nothing (or Issues) is showing.
            const ui = useUIStore.getState()
            if (ui.activeSlidePanel == null || ui.activeSlidePanel === 'issues') {
              ui.setSlidePanel('issues')
            }
            toast.error(`Solve failed — ${fail.title}. See the Issues panel.`, { duration: 6000 })
          }
        }
        qcRef.invalidateQueries({ queryKey: ['solveQueue'] })
        esCleanupRef.current = null
      },
      // SSE error recovery: if the stream dies before `done` (browser sleep,
      // backend crash, network blip past the auto-retry window), surface
      // the failure and unlock the UI so the user can Run again instead of
      // being stuck in the "Abort" state forever.
      (reason) => {
        esCleanupRef.current = null
        // Self-heal the attach-before-log_queue race: if this stream died early
        // (no `done`) while THIS job is still the running one, reset the attach
        // guard so the next poll re-opens it — by then log_queue is set. Bounded
        // to 3 tries; past that, a genuinely dead stream surfaces as 'failed'.
        if (jobId != null && attachRetryRef.current.id === jobId
            && attachRetryRef.current.tries < 3) {
          attachRetryRef.current.tries += 1
          attachedJobRef.current = null  // allow the effect to re-attach
          appLog('WARN', `${label}: log stream not ready yet — retrying (${attachRetryRef.current.tries}/3)…`)
          return
        }
        setStatus('failed')
        appLog('ERROR', `${label}: ${reason}`)
        qcRef.invalidateQueries({ queryKey: ['solveQueue'] })
      },
    )
  }, [appendLog, setStatus, appLog, qcRef])

  // Auto-attach the SSE log the moment the CURRENT project's queue job starts
  // running. `/log_stream` captures _state["log_queue"] at open, so we MUST
  // gate on status 'running' (the dispatcher sets log_queue on the active ctx
  // only then) — opening while 'queued' would capture a stale/None queue. The
  // history-replay buffer backfills any lines emitted in the ≤1.5s before the
  // poll catches the transition. The ref guard makes this fire once per run.
  useEffect(() => {
    if (!myJob) {
      // No active job for this project — clear the guard so the NEXT run of
      // this project (a new job id) attaches afresh.
      attachedJobRef.current = null
      return
    }
    if (myJob.status === 'running' && attachedJobRef.current !== myJob.id) {
      attachedJobRef.current = myJob.id
      // Seed the retry budget for this job on the first attach (a re-attach
      // after an early-error keeps the same id and decrements the budget).
      if (attachRetryRef.current.id !== myJob.id) {
        attachRetryRef.current = { id: myJob.id, tries: 0 }
      }
      if (status !== 'running') {
        clearLog()
        setStatus('running')
      }
      requestBottomTab('Log')
      openLogStream(runLabel, myJob.id)
    }
  }, [myJob, status, runLabel, openLogStream, clearLog, setStatus, requestBottomTab])

  // The current project is actively solving (its job runs, or a legacy direct
  // /run set status='running'). Drives the Abort affordance.
  const jobRunning = myJob?.status === 'running'
  const jobQueued = myJob?.status === 'queued'
  const busy = jobRunning || jobQueued || isRunning

  const handleRunButton = async () => {
    // 1. RUNNING (queue job or legacy /run) → abort.
    if (jobRunning || isRunning) {
      if (myJob) {
        await abort.mutateAsync(myJob.id)
      } else {
        // Legacy path: a solve started via direct /run before this change.
        await simulationApi.abort()
      }
      esCleanupRef.current?.()
      esCleanupRef.current = null
      attachedJobRef.current = null
      setStatus('aborted')
      appLog('WARN', 'Simulation aborted by user')
      return
    }
    // 2. QUEUED (waiting behind another solve) → cancel the queued job.
    if (jobQueued && myJob) {
      await abort.mutateAsync(myJob.id)
      appLog('WARN', `Cancelled queued solve of '${currentProject}'`)
      return
    }
    // 3. IDLE / slot may be free → ENQUEUE the current project.
    if (!currentProject) {
      toast.error('Save the project before running it.')
      return
    }
    // Guard the pre-save→enqueue window against a fast double-click (the await
    // on saveProjectQuietly precedes enqueue.isPending flipping to true). The
    // latch is released in the finally so every exit path clears it.
    if (enqueuingRef.current) return
    enqueuingRef.current = true
    try {
      clearLog()
      // Pre-run snapshot: enqueue REQUIRES a saved network.nc on disk (the
      // dispatcher solves the resident foreground ctx in place, but the enqueue
      // endpoint validates the file exists). This also serialises the CURRENT
      // (clean) network as the "last known good" state before the LP mutates it.
      appLog('INFO', `Pre-run snapshot: saving '${currentProject}' before queuing the solve…`)
      const ok = await saveProjectQuietly(currentProject)
      if (!ok) {
        appLog('ERROR', `Pre-run snapshot of '${currentProject}' failed — cannot queue the solve.`)
        toast.error('Save failed — cannot queue the solve.')
        return
      }
      // Pop the bottom panel open on the Log tab so the stream is visible the
      // instant the auto-attach effect opens it (when the job goes running).
      requestBottomTab('Log')
      const job = await enqueue.mutateAsync(currentProject)
      appLog('INFO', job.status === 'running'
        ? `${runLabel} started`
        : `${runLabel} queued (#${job.position ?? '?'})`)
      // DO NOT open the SSE here — the auto-attach effect opens it once the
      // job transitions to 'running' (immediately if the slot was free, or
      // after the preceding solve finishes). Gating on 'running' guarantees
      // the backend's log_queue is set before /log_stream captures it.
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      appLog('ERROR', `${runLabel} failed to queue: ${typeof detail === 'string' ? detail : 'unknown error'}`)
      toast.error(typeof detail === 'string' ? detail : 'Failed to queue the solve.')
    } finally {
      enqueuingRef.current = false
    }
  }

  // Close the SSE stream if the component unmounts mid-run (e.g. user
  // navigates away). Without this, the EventSource keeps reconnecting.
  useEffect(() => () => { esCleanupRef.current?.() }, [])

  return (
    <header data-no-panel-close className="h-11 flex items-center gap-2 px-3 bg-bg border-b border-border shrink-0 z-10">

      {/* Project name — click to edit inline */}
      <div className="flex items-center gap-1.5 shrink-0">
        {editingName ? (
          <input
            ref={nameRef}
            type="text"
            value={nameInput}
            maxLength={80}
            onChange={e => setNameInput(e.target.value)}
            onBlur={commitName}
            onKeyDown={e => {
              if (e.key === 'Enter') commitName()
              else if (e.key === 'Escape') { setEditingName(false); setNameInput(projectName) }
            }}
            className="text-[11px] font-semibold text-text bg-bg border border-accent rounded px-1.5 py-0.5 w-44 focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
        ) : (
          <button
            data-project-name-editor
            onClick={startEditName}
            onFocus={startEditName}
            title="Click to rename project"
            className="text-[11px] font-semibold text-text truncate max-w-[180px] hover:text-accent transition-colors cursor-text"
          >
            {projectName}
          </button>
        )}
      </div>

      {/* Mini stat chips — buses · lines · assets (design system). Rounded
          bg-2 container with 1px dividers; coloured LED per metric. */}
      <div className="inline-flex items-center bg-bg-2 border border-border rounded-lg p-0.5 shrink-0">
        {([
          ['buses',  buses.length,                                      'var(--color-v-red)'],
          ['lines',  lines.length,                                      'var(--color-v-blue)'],
          ['assets', generators.length + sus.length + loads.length,     'var(--color-cat-renew)'],
        ] as const).map(([label, n, dot], i) => (
          <span key={label} className="inline-flex items-center">
            {i > 0 && <span className="w-px h-3.5 bg-border" />}
            <span className="inline-flex items-center gap-1.5 h-6 px-2 rounded-md text-[10.5px] font-mono text-ink-600">
              <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot }} />
              <b className="text-text font-semibold">{n}</b> {label}
            </span>
          </span>
        ))}
      </div>

      {/* Search — multi-type, debounced, keyboard navigable */}
      <div className="relative mx-2" style={{ width: 220 }}>
        <Search size={11} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
        <input
          ref={searchRef}
          type="text"
          value={searchVal}
          onChange={e => setSearchVal(e.target.value)}
          onKeyDown={handleSearchKey}
          placeholder="Search components…"
          className="w-full pl-7 pr-2.5 py-1 text-[11px] bg-panel border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
        />
        {searchResults.length > 0 && (
          <div
            ref={dropdownRef}
            className="absolute top-full left-0 mt-1 w-full bg-bg border border-border rounded shadow-lg z-50 py-0.5"
          >
            {searchResults.map((r, idx) => (
              <button
                key={`${r.type}-${r.name}`}
                className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-left transition-colors
                  ${idx === selectedIdx ? 'bg-accent/10' : 'hover:bg-accent/5'}`}
                onMouseEnter={() => setSelectedIdx(idx)}
                onClick={() => selectResult(r)}
              >
                <span className="text-muted shrink-0"><TypeIcon type={r.type} /></span>
                <span className="flex-1 min-w-0">
                  <span className="text-[12px] font-semibold text-text block truncate">{r.name}</span>
                  <span className="text-[10px] text-muted">
                    {TYPE_LABEL[r.type]}{r.busName ? ` · → ${r.busName}` : ''}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Command palette launcher — the palette (project switch, jump to any
          panel/asset, theme/density) was previously keyboard-only (Ctrl/Cmd+K),
          so non-power-users never found it. This visible affordance teaches the
          shortcut via the kbd chip. */}
      <button
        onClick={() => setPaletteMode('all')}
        title={`Command palette — switch projects, open panels, jump to assets (${cmdKey}+K)`}
        className="flex items-center gap-1.5 px-2 py-1.5 rounded text-[11px] font-medium border border-border text-muted hover:text-text hover:bg-border/30 transition-colors shrink-0"
      >
        <Command size={12} />
        <kbd className="text-[9px] font-mono bg-bg-2 border border-border rounded px-1 py-px leading-none">{cmdKey} K</kbd>
      </button>

      {/* Undo */}
      <button
        onClick={handleUndo}
        disabled={undoDepth === 0 || undoMut.isPending || busy}
        title={undoDepth > 0 ? `Undo last action (Ctrl+Z) · ${undoDepth} step${undoDepth !== 1 ? 's' : ''} available` : 'Nothing to undo'}
        className="flex items-center gap-1 px-2 py-1.5 rounded text-[11px] font-medium border border-border text-text hover:bg-border/30 transition-colors disabled:opacity-35 disabled:cursor-not-allowed"
      >
        {undoMut.isPending
          ? <Loader size={12} className="animate-spin" />
          : <Undo2 size={12} />}
        {undoDepth > 0 && (
          <span className="text-[9px] font-bold text-accent bg-accent/10 rounded-full px-1 min-w-[14px] text-center leading-none py-0.5">
            {undoDepth}
          </span>
        )}
      </button>

      {/* Save shortcut — checkpoints the project and clears the undo stack.
          When no project exists yet (currentProject is null), the button
          prompts for a name on click instead of being disabled — same path
          Ctrl+S already takes. Only disable for in-flight save / running solve. */}
      <button
        onClick={handleQuickSave}
        disabled={saveMut.isPending || busy}
        title={currentProject
          ? `Save '${currentProject}' (Ctrl+S) — overwrites the saved project & clears revert history`
          : 'Save (Ctrl+S) — you will be prompted to name the project'}
        className="flex items-center gap-1 px-2 py-1.5 rounded text-[11px] font-medium border border-border text-text hover:bg-border/30 transition-colors disabled:opacity-35 disabled:cursor-not-allowed"
      >
        {saveMut.isPending
          ? <Loader size={12} className="animate-spin" />
          : savedFlash
            ? <Check size={12} className="text-success" />
            : <Save size={12} />}
      </button>

      <div className="flex-1" />

      {/* Run simulation — smart Run/Queue/Cancel/Abort button. Clicking Run
          ENQUEUES the foreground project; the FIFO dispatcher solves it now if
          the slot is free, or queues it behind a running solve. Three states:
            • idle           → green "Run LOPF/…", click enqueues
            • queued behind  → amber "Queued #N", click cancels the queued job
            • running        → amber "Abort", click aborts the solve
          Label's run mode tracks solver_config.mode (Solver Settings). */}
      {(() => {
        // amber for both queued + running; green only at idle.
        const amber = jobQueued || jobRunning || isRunning
        const queuedLabel = `Queued${myJob?.position != null ? ` #${myJob.position}` : ''}`
        return (
          <button
            onClick={handleRunButton}
            disabled={abort.isPending || enqueue.isPending}
            title={jobRunning || isRunning
              ? 'Abort the running simulation'
              : jobQueued
                ? 'Cancel this queued solve'
                : `${runLabel}: ${MODE_DESC[mode] ?? ''} — queues the solve; runs now if the queue is free`}
            className="flex items-center gap-1.5 px-3.5 h-8 rounded-lg text-[11px] font-semibold text-white transition-all select-none hover:brightness-110 active:translate-y-px disabled:opacity-60 disabled:cursor-wait"
            style={{
              // Design system: 3-stop green gradient with layered + inset shadows;
              // amber gradient while queued / running.
              background: amber
                ? 'linear-gradient(180deg, #f59e0b 0%, #d97706 50%, #b45309 100%)'
                : 'linear-gradient(180deg, #22c55e 0%, #16a34a 50%, #15803d 100%)',
              boxShadow: amber
                ? '0 1px 0 rgba(180,80,0,0.35), 0 1px 3px rgba(180,80,0,0.2), inset 0 1px 0 rgba(255,255,255,0.22)'
                : '0 1px 0 rgba(20,100,50,0.4), 0 1px 3px rgba(20,100,50,0.25), inset 0 1px 0 rgba(255,255,255,0.22), inset 0 -1px 0 rgba(0,0,0,0.08)',
            }}
          >
            {jobRunning || isRunning
              ? <><Loader size={11} className="animate-spin" /> Abort</>
              : jobQueued
                ? <><Loader size={11} className="animate-spin" /> {queuedLabel}</>
                : <>
                    {mode === 'lopf' ? <Zap size={11} /> : <Play size={11} />}
                    {runLabel}
                  </>}
          </button>
        )
      })()}

      {/* Status indicator — bordered pill with a glowing LED (design system).
          A queued (not-yet-running) job shows "Queued #N" instead of "Idle"
          without disturbing the simulationStore `status` semantics. */}
      {(() => {
        const queued = jobQueued
        const dot = queued ? STATUS_DOT.running : (STATUS_DOT[status] ?? STATUS_DOT.idle)
        const label = queued
          ? `Queued${myJob?.position != null ? ` #${myJob.position}` : ''}`
          : (STATUS_LABEL[status] ?? 'Idle')
        return (
          <div className="flex items-center gap-1.5 h-7 px-2.5 rounded-md border border-border bg-bg-2">
            <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
            <span className="text-[10px] text-muted font-mono">{label}</span>
          </div>
        )
      })()}

      {/* Right panel toggle */}
      <button
        onClick={toggleRightPanel}
        className="ml-1 p-1.5 text-muted hover:text-text border border-transparent hover:border-border rounded transition-colors"
        title={rightPanelOpen ? 'Hide properties panel' : 'Show properties panel'}
      >
        {rightPanelOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
      </button>
    </header>
  )
}
