import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, X, FolderOpen, Loader } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { appLog } from '../store/simulationStore'
import ProjectPicker from '../components/ProjectPicker'
import {
  invalidateNetworkQueries, saveProjectQuietly, nextUntitledName,
  resetBackendNetwork, slugify, abortRunningSim, uniqueProjectName,
  switchToProject,
} from '../utils/projectActions'
import { useSolveQueue, activeJobForProject } from '../hooks/useSolveQueue'
import { useJobTerminalInvalidation } from '../hooks/useJobTerminalInvalidation'
import { Dialog } from '../components/Dialog'

// The "+" dialog. Two ways to add a tab, because a workspace that can only
// CREATE forces you to the sidebar to open a project you already have —
// which is the whole point of a tab strip.
//
//   Open existing  — pick from the projects saved on the backend. Opening one
//                    ADDS a tab beside the current project rather than
//                    replacing it (setCurrentProject auto-adds; see uiStore).
//   New project    — the original name-input flow, unchanged.
//
// Defaults to "Open existing" whenever there is something to open, and falls
// back to "New project" on an empty backend so a first-run user is not
// staring at an empty list.
function AddProjectModal({
  initial, taken, currentProject, onCreate, onOpen, onClose,
}: {
  initial: string
  taken: readonly string[]
  currentProject: string | null
  onCreate: (name: string) => void
  onOpen: (name: string) => void
  onClose: () => void
}) {
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    refetchOnMount: 'always',
  })
  // Resolve the default mode from the list, but only once — re-deriving it on
  // every render would yank the user out of a mode they picked as soon as the
  // query settled or refetched.
  const [mode, setMode] = useState<'open' | 'new' | null>(null)
  const resolvedMode = mode ?? (projects.length > 0 ? 'open' : 'new')

  const [name, setName] = useState(initial)
  const inputRef = useRef<HTMLInputElement>(null)
  const titleId = useId()
  useEffect(() => {
    if (resolvedMode === 'new') { inputRef.current?.focus(); inputRef.current?.select() }
  }, [resolvedMode])
  const trimmed = name.trim()
  const conflict = trimmed && taken.includes(trimmed)
  const commit = () => {
    if (!trimmed || conflict) return
    onCreate(trimmed)
  }

  const tab = (id: 'open' | 'new', label: string) => (
    <button
      key={id}
      type="button"
      role="tab"
      aria-selected={resolvedMode === id}
      onClick={() => setMode(id)}
      className={`px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors ${
        resolvedMode === id
          ? 'border-accent text-accent'
          : 'border-transparent text-muted hover:text-text'
      }`}
    >{label}</button>
  )

  return (
    <Dialog
      open
      onClose={onClose}
      aria-labelledby={titleId}
      panelClassName="bg-bg rounded-xl shadow-2xl w-[460px] max-w-[95vw] max-h-[80vh] flex flex-col overflow-hidden"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <span id={titleId} className="text-sm font-semibold text-text">Add a project tab</span>
        <button onClick={onClose} aria-label="Close add project dialog"
          className="p-1 text-muted hover:text-text"><X size={15} /></button>
      </div>

      <div role="tablist" className="flex items-center gap-1 px-3 border-b border-border shrink-0">
        {tab('open', 'Open existing')}
        {tab('new', 'New project')}
      </div>

      {resolvedMode === 'open' ? (
        <ProjectPicker
          currentProject={currentProject}
          alreadyOpen={taken}
          onPick={onOpen}
          autoFocusFilter
          emptyHint={<>No projects saved yet — use <b>New project</b> to make one.</>}
        />
      ) : (
        <div className="p-4 space-y-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text">Project name</span>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commit() }}
              className={`px-2.5 py-1.5 text-xs border rounded focus:outline-none focus:ring-1 ${
                conflict
                  ? 'border-danger focus:border-danger focus:ring-danger/20'
                  : 'border-border focus:border-accent focus:ring-accent/20'
              }`}
              placeholder="my_project"
            />
            {conflict
              ? <span className="text-[10px] text-danger">A tab named '{trimmed}' is already open</span>
              : <span className="text-[10px] text-muted">A new empty project will be created on the backend</span>}
          </label>
          <div className="flex gap-2 justify-end">
            <button onClick={onClose} className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors">Cancel</button>
            <button
              onClick={commit}
              disabled={!trimmed || !!conflict}
              className="px-4 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 disabled:opacity-40 transition-colors"
            >Create</button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

export default function ProjectTabs() {
  const qc = useQueryClient()
  const {
    openTabs, currentProject, addTab, closeTab,
    setCurrentProject, setProjectName, setProjectSwitchInProgress,
  } = useUIStore()
  // openTabs is now Array<{name, lastInteractedAt}> (B8). Most of this
  // component cares only about the names + their order, so derive a flat name
  // list once and use it for the modal `taken` / nextUntitled / render map.
  const tabNames = openTabs.map(t => t.name)
  const [busy, setBusy] = useState<string | null>(null)
  const [showNameModal, setShowNameModal] = useState(false)
  // Per-tab solve-queue indicator (shared ['solveQueue'] query — polls only
  // while jobs are active). Marks tabs whose project is queued or solving.
  const { data: solveQueue } = useSolveQueue()

  // Per-finished-job cache invalidation, scoped to THAT job's project. This
  // replaces the `>0 → 0` resync that used to live here: it reloaded
  // `currentProject` from disk (reset_network + import_from_netcdf, with no
  // save in front of it) whenever the GLOBAL active-job count fell to zero, so
  // a project that was never queued lost its unsaved edits and another
  // organisation's redacted rows could trigger it. Its own comment justified it
  // by "the swap-based queue solves through the SHARED active slot", a design
  // B4.3 replaced — and increment 1's one-context-per-project invariant means
  // the freshly solved network IS the one this session holds, so there is
  // nothing to reload.
  useJobTerminalInvalidation()

  const switchTo = useCallback(async (target: string) => {
    if (target === currentProject || busy) return
    setBusy(target)
    // Fence autosave across the swap (see ScenariosPanel for the rationale).
    setProjectSwitchInProgress(true)
    try {
      // B8 instant switch: abort foreground solve → save outgoing → activate
      // (pointer swap) → re-key reactive queries to the target's RETAINED
      // cache (no component-table refetch). switchToProject owns that core;
      // we own the busy-state, fence, and toasts.
      const r = await switchToProject(target, qc)
      switch (r.status) {
        case 'switched':
          appLog('INFO', `Switched to project '${target}'`)
          toast.success(`Switched to '${target}'`)
          break
        case 'noop':
          break
        case 'abort-failed':
          toast.error('Could not abort the running simulation. Try again in a moment.')
          break
        case 'busy-solve':
          toast.error(`Finish or abort the running solve on '${currentProject}' before switching.`)
          break
        case 'not-found':
          toast.error(`'${target}' no longer exists`)
          break
        case 'error':
          appLog('ERROR', `Switch to '${target}' failed: ${r.message}`)
          toast.error(`Could not load '${target}'`)
          break
      }
    } finally {
      setProjectSwitchInProgress(false)
      setBusy(null)
    }
  }, [currentProject, busy, qc, setProjectSwitchInProgress])

  // The actual create — invoked from the modal once the user has picked a name.
  const createProject = useCallback(async (name: string) => {
    if (busy) return
    setBusy('__new__')
    try {
      // Resolve a collision-free name BEFORE any destructive step. This flow
      // seeds an empty network under `name` with force=true, which bypasses
      // the backend's empty-network-overwrite guard — so a slug matching an
      // existing-but-unopened project would silently wipe its network.nc. The
      // "+" quick-create has no overwrite-consent UI (unlike the sidebar
      // wizard's Blank tab), so we uniquify rather than clobber. If the project
      // list can't be read we can't prove the name is free → abort the create.
      let target: string
      try {
        target = await uniqueProjectName(name)
      } catch (e) {
        appLog('ERROR', `Could not verify project-name availability for '${name}': ${String((e as Error)?.message ?? e)}`)
        toast.error('Could not verify project-name availability. Try again in a moment.')
        return
      }
      if (target !== name) toast(`'${name}' already exists — created '${target}' instead`, { icon: 'ℹ️' })

      const stopped = await abortRunningSim()
      if (!stopped) {
        toast.error('Could not abort the running simulation. Try again in a moment.')
        return
      }
      if (currentProject) await saveProjectQuietly(currentProject)
      await resetBackendNetwork()
      try { await projectsApi.save(target, true) }
      catch (e) { appLog('WARN', `Could not seed empty project '${target}': ${String((e as Error)?.message ?? e)}`) }
      invalidateNetworkQueries(qc, target)
      addTab(target)
      setCurrentProject(target)
      setProjectName(target)
      appLog('INFO', `New project tab created: ${target}`)
      toast.success(`Created '${target}'`)
    } catch (e) {
      toast.error(`Could not create new project: ${(e as Error)?.message ?? e}`)
    } finally {
      setBusy(null)
    }
  }, [busy, currentProject, qc, addTab, setCurrentProject, setProjectName])

  // Open an EXISTING project as an additional tab. `switchToProject` does the
  // whole dance — abort any foreground solve, save the outgoing project,
  // activate the target backend-side, re-key the reactive queries — and its
  // `setCurrentProject` call auto-adds the tab when the project is not already
  // open (uiStore). So the current project stays open beside the new one;
  // this is an ADD, not a replace.
  const openExisting = useCallback(async (target: string) => {
    if (busy) return
    setShowNameModal(false)
    if (target === currentProject) return
    setBusy(target)
    setProjectSwitchInProgress(true)
    try {
      const r = await switchToProject(target, qc)
      switch (r.status) {
        case 'switched':
          appLog('INFO', `Opened project '${target}' in a tab`)
          toast.success(`Opened '${target}'`)
          break
        case 'noop':
          break
        case 'abort-failed':
          toast.error('Could not abort the running simulation. Try again in a moment.')
          break
        case 'busy-solve':
          toast.error(`Finish or abort the running solve on '${currentProject}' before opening another project.`)
          break
        case 'not-found':
          toast.error(`'${target}' no longer exists`)
          // The list we picked from is stale — refresh it so the row goes away.
          qc.invalidateQueries({ queryKey: ['projects'] })
          break
        case 'error':
          appLog('ERROR', `Opening '${target}' failed: ${r.message}`)
          toast.error(`Could not open '${target}'`)
          break
      }
    } finally {
      setProjectSwitchInProgress(false)
      setBusy(null)
    }
  }, [busy, currentProject, qc, setProjectSwitchInProgress])

  // The "+" tab opens the add-a-tab dialog: open an existing project, or
  // create a new one. nextUntitledName is the suggested default on the create
  // side so the user can still hit Enter for the legacy auto-named flow.
  const handleNewClick = useCallback(() => {
    if (busy) return
    setShowNameModal(true)
  }, [busy])

  const handleNameConfirm = useCallback((name: string) => {
    setShowNameModal(false)
    // Slugify so users typing "My Project!" still produce a valid backend
    // project folder name. The slugified value is the source of truth for
    // both the tab and the backend project id.
    const safe = slugify(name) || name
    createProject(safe)
  }, [createProject])

  const handleClose = useCallback(async (e: React.MouseEvent, name: string) => {
    e.stopPropagation()
    if (busy) return
    if (openTabs.length <= 1) {
      toast.error('At least one project tab must remain open')
      return
    }
    if (name === currentProject) {
      // Auto-save the active project before closing it, then move to a neighbour.
      await saveProjectQuietly(name)
      const idx = tabNames.indexOf(name)
      const neighbour = tabNames[idx + 1] ?? tabNames[idx - 1]
      closeTab(name)
      if (neighbour) await switchTo(neighbour)
    } else {
      closeTab(name)
    }
  }, [tabNames, openTabs, currentProject, busy, closeTab, switchTo])

  if (openTabs.length === 0) {
    // No tabs yet — show only the "+" so the user can start a first project.
    return (
      <>
        <div className="flex items-center gap-1 px-2 h-9 bg-bg border-b border-border shrink-0">
          <span className="text-[11px] text-muted">No project open</span>
          <button
            onClick={handleNewClick}
            disabled={!!busy}
            className="ml-2 flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-accent hover:bg-accent/10 transition-colors disabled:opacity-50"
          >
            <Plus size={12} /> Open or create a project
          </button>
        </div>
        {showNameModal && (
          <AddProjectModal
            initial={nextUntitledName(tabNames)}
            taken={tabNames}
            currentProject={currentProject}
            onCreate={handleNameConfirm}
            onOpen={openExisting}
            onClose={() => setShowNameModal(false)}
          />
        )}
      </>
    )
  }

  return (
    <div data-no-panel-close className="flex items-center gap-0.5 px-2 h-9 bg-bg border-b border-border shrink-0 overflow-x-auto">
      {showNameModal && (
        <AddProjectModal
          initial={nextUntitledName(tabNames)}
          taken={tabNames}
          currentProject={currentProject}
          onCreate={handleNameConfirm}
          onOpen={openExisting}
          onClose={() => setShowNameModal(false)}
        />
      )}
      {tabNames.map(name => {
        const active = name === currentProject
        const loading = busy === name
        const qJob = activeJobForProject(solveQueue, name)
        return (
          <div
            key={name}
            role="tab"
            tabIndex={0}
            aria-selected={active}
            onClick={() => switchTo(name)}
            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') switchTo(name) }}
            className={`group relative flex items-center gap-1.5 pl-2.5 pr-1 h-7 rounded-t text-[12px] font-medium border-x border-t cursor-pointer transition-colors ${
              active
                ? 'bg-bg border-border text-text'
                : 'bg-panel border-transparent text-muted hover:bg-bg hover:text-text'
            }`}
            style={{ marginBottom: -1 }}
            title={`Switch to '${name}' (auto-saves current first)`}
          >
            {/* 2px industrial-red underline on the active tab — design system */}
            {active && <span aria-hidden className="absolute inset-x-0 -bottom-px h-0.5 bg-accent" />}
            {loading
              ? <Loader size={11} className="animate-spin text-accent" />
              : <FolderOpen size={11} className={active ? 'text-accent' : 'text-muted'} />}
            <span className="truncate max-w-[160px]">{name}</span>
            {qJob && (
              <span
                className="shrink-0"
                title={qJob.status === 'running' ? 'Solving in the queue' : `Queued to solve (#${qJob.position ?? '?'})`}
              >
                {qJob.status === 'running'
                  ? <Loader size={9} className="animate-spin text-amber-500" />
                  : <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500" aria-hidden />}
              </span>
            )}
            <button
              type="button"
              onClick={e => handleClose(e, name)}
              className={`ml-1 p-0.5 rounded transition-opacity ${active ? 'opacity-60 hover:opacity-100' : 'opacity-0 group-hover:opacity-100'} hover:bg-border/50`}
              title="Close tab (auto-saves before closing)"
              aria-label={`Close ${name}`}
            >
              <X size={11} />
            </button>
          </div>
        )
      })}
      <button
        onClick={handleNewClick}
        disabled={!!busy}
        className="ml-1 flex items-center justify-center w-6 h-6 rounded text-muted hover:text-accent hover:bg-accent/5 transition-colors disabled:opacity-50"
        title="Open an existing project or create a new one (auto-saves current first)"
        aria-label="Add a project tab"
      >
        {busy === '__new__' ? <Loader size={12} className="animate-spin" /> : <Plus size={14} />}
      </button>
    </div>
  )
}
