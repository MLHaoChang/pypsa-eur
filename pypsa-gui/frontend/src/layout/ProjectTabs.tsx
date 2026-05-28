import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Plus, X, FolderOpen, Loader } from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import { appLog } from '../store/simulationStore'
import {
  invalidateNetworkQueries, saveProjectQuietly, nextUntitledName,
  resetBackendNetwork, slugify, abortRunningSim,
} from '../utils/projectActions'

// Small name-input modal for the "+ new tab" flow. Lives here rather than in
// a shared module because it's only used by ProjectTabs; the Sidebar has its
// own (heavier) NewProjectModal with folder picker support.
function NewTabNameModal({
  initial, taken, onCreate, onClose,
}: {
  initial: string
  taken: readonly string[]
  onCreate: (name: string) => void
  onClose: () => void
}) {
  const [name, setName] = useState(initial)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select() }, [])
  const trimmed = name.trim()
  const conflict = trimmed && taken.includes(trimmed)
  const commit = () => {
    if (!trimmed || conflict) return
    onCreate(trimmed)
  }
  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-bg rounded-xl shadow-2xl w-80 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="text-sm font-semibold text-text">New project</span>
          <button onClick={onClose} className="p-1 text-muted hover:text-text"><X size={15} /></button>
        </div>
        <div className="p-4 space-y-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-text">Project name</span>
            <input
              ref={inputRef}
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') onClose() }}
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
      </div>
    </div>
  )
}

export default function ProjectTabs() {
  const qc = useQueryClient()
  const {
    openTabs, currentProject, addTab, closeTab,
    setCurrentProject, setProjectName,
  } = useUIStore()
  const [busy, setBusy] = useState<string | null>(null)
  const [showNameModal, setShowNameModal] = useState(false)

  const switchTo = useCallback(async (target: string) => {
    if (target === currentProject || busy) return
    setBusy(target)
    try {
      // Mid-solve, the PyPSA lock is held by the worker thread — every
      // mutating endpoint (save_project, load_project, reset_network)
      // blocks until it's released. abortRunningSim() requests a stop and
      // polls status until the worker actually exits. Without this, the
      // following save+load both hit axios' 30s timeout and the user sees
      // "Could not save 'X': timeout of 30000ms exceeded".
      const stopped = await abortRunningSim()
      if (!stopped) {
        toast.error('Could not abort the running simulation. Try again in a moment.')
        return
      }
      if (currentProject) await saveProjectQuietly(currentProject)
      await projectsApi.load(target)
      invalidateNetworkQueries(qc)
      setCurrentProject(target)
      setProjectName(target)
      appLog('INFO', `Switched to project '${target}'`)
      toast.success(`Switched to '${target}'`)
    } catch (e) {
      const msg = (e as Error)?.message ?? String(e)
      appLog('ERROR', `Switch to '${target}' failed: ${msg}`)
      toast.error(`Could not load '${target}'`)
    } finally {
      setBusy(null)
    }
  }, [currentProject, busy, qc, setCurrentProject, setProjectName])

  // The actual create — invoked from the modal once the user has picked a name.
  const createProject = useCallback(async (name: string) => {
    if (busy) return
    setBusy('__new__')
    try {
      const stopped = await abortRunningSim()
      if (!stopped) {
        toast.error('Could not abort the running simulation. Try again in a moment.')
        return
      }
      if (currentProject) await saveProjectQuietly(currentProject)
      await resetBackendNetwork()
      try { await projectsApi.save(name, true) }
      catch (e) { appLog('WARN', `Could not seed empty project '${name}': ${String((e as Error)?.message ?? e)}`) }
      invalidateNetworkQueries(qc)
      addTab(name)
      setCurrentProject(name)
      setProjectName(name)
      appLog('INFO', `New project tab created: ${name}`)
      toast.success(`Created '${name}'`)
    } catch (e) {
      toast.error(`Could not create new project: ${(e as Error)?.message ?? e}`)
    } finally {
      setBusy(null)
    }
  }, [busy, currentProject, qc, addTab, setCurrentProject, setProjectName])

  // The "+" tab now opens a name-input modal first; creation happens on Enter
  // / Create. nextUntitledName is the suggested default so the user can hit
  // Enter for the legacy auto-named flow.
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
      const idx = openTabs.indexOf(name)
      const neighbour = openTabs[idx + 1] ?? openTabs[idx - 1]
      closeTab(name)
      if (neighbour) await switchTo(neighbour)
    } else {
      closeTab(name)
    }
  }, [openTabs, currentProject, busy, closeTab, switchTo])

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
            <Plus size={12} /> New project
          </button>
        </div>
        {showNameModal && (
          <NewTabNameModal
            initial={nextUntitledName(openTabs)}
            taken={openTabs}
            onCreate={handleNameConfirm}
            onClose={() => setShowNameModal(false)}
          />
        )}
      </>
    )
  }

  return (
    <div data-no-panel-close className="flex items-center gap-0.5 px-2 h-9 bg-bg border-b border-border shrink-0 overflow-x-auto">
      {showNameModal && (
        <NewTabNameModal
          initial={nextUntitledName(openTabs)}
          taken={openTabs}
          onCreate={handleNameConfirm}
          onClose={() => setShowNameModal(false)}
        />
      )}
      {openTabs.map(name => {
        const active = name === currentProject
        const loading = busy === name
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
        title="New project (auto-saves current first)"
      >
        {busy === '__new__' ? <Loader size={12} className="animate-spin" /> : <Plus size={14} />}
      </button>
    </div>
  )
}
