import { useState, useRef, useEffect, useCallback, useId } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  FilePlus, FolderOpen, BookOpen, Upload, Copy as CopyIcon, X, AlertTriangle,
  Sparkles,
} from 'lucide-react'
import toast from 'react-hot-toast'
import type { ProjectInfo } from '../api/types'
import { projectsApi } from '../api/projects'
import { ioApi } from '../api/io'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { invalidateNetworkQueries, formatRelativeTime } from '../utils/projectActions'
import { nk } from '../utils/queryKeys'
import { appLog } from '../store/simulationStore'
import { Dialog } from '../components/Dialog'

// NewProjectWizard — replaces the single-input NewProjectModal with a 4-tab
// flow per the design spec. Tabs:
//
//   • Blank       — name + create empty project (legacy behaviour)
//   • Template    — pick a curated example network
//   • From file   — drop a .pypsaproj.zip (full project + results); .nc wraps as project
//   • Clone       — fork an existing project under a new name
//
// Tabs share a single header + the same `onClose`. Submission paths vary per
// tab: Blank calls the legacy `onConfirm(name)` to mirror the existing
// create-empty path; the other three drive their own mutations directly so
// the wizard can manage its own loading/error UX.

export interface NewProjectWizardProps {
  existingProjects: ProjectInfo[]
  onConfirm: (name: string) => void  // legacy "create blank" handler
  onClose: () => void
  isPending: boolean
  // Which tab to land on. The projects home surfaces the four tabs as four
  // separate action cards, so it needs to open the wizard already on the one
  // the user clicked; the workbench omits it and gets 'blank'.
  initialTab?: NewProjectTab
}

export type NewProjectTab = 'blank' | 'template' | 'file' | 'clone'
type Tab = NewProjectTab

// Curated example networks. `available: true` templates are backed by a real
// network.nc under backend/project_templates/<id> and import via
// projectsApi.createFromTemplate. See project_templates/_build.py for how each
// is generated (3bus/ieee14 are synthetic; belgium is PyPSA-Eur's own test
// network — the OSM-derived Belgian grid clustered to 5 nodes).
const TEMPLATES = [
  { id: '3bus',    name: '3-Bus Tutorial', description: 'Minimal AC network — 3 buses, 3 lines, 1 generator. Best starting point for learning PyPSA.', buses: 3,  lines: 3,  badge: 'simple',    available: true },
  { id: 'ieee14',  name: 'IEEE 14-Bus',    description: 'Classic test case. 14 buses, 20 lines, 5 generators. Stable baseline for benchmarking.',     buses: 14, lines: 20, badge: 'reference', available: true },
  { id: 'belgium', name: 'Belgium Grid',   description: "PyPSA-Eur's Belgian network — OSM-derived HV grid clustered to 5 nodes, with wind, solar, nuclear, gas, batteries and H2. Solves out of the box.", buses: 10, lines: 6, badge: 'PyPSA-Eur', available: true },
] as const

export default function NewProjectWizard({
  existingProjects, onConfirm, onClose, isPending, initialTab = 'blank',
}: NewProjectWizardProps) {
  const [tab, setTab] = useState<Tab>(initialTab)
  const titleId = useId()

  return (
    <Dialog
      open
      onClose={onClose}
      aria-labelledby={titleId}
      panelClassName="bg-bg rounded-xl shadow-2xl w-[640px] max-w-[95vw] max-h-[88vh] overflow-hidden flex flex-col"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <FilePlus size={15} className="text-accent" />
          <span id={titleId} className="text-sm font-semibold text-text">New project</span>
        </div>
        <button onClick={onClose} className="p-1 text-muted hover:text-text transition-colors">
          <X size={15} />
        </button>
      </div>

      {/* Tab strip */}
      <div className="flex items-center border-b border-border shrink-0 px-2">
        <TabBtn id="blank"    active={tab === 'blank'}    onClick={() => setTab('blank')}    icon={<FilePlus size={12} />}   label="Blank" />
        <TabBtn id="template" active={tab === 'template'} onClick={() => setTab('template')} icon={<BookOpen size={12} />}    label="From template" />
        <TabBtn id="file"     active={tab === 'file'}     onClick={() => setTab('file')}     icon={<Upload size={12} />}     label="From file" />
        <TabBtn id="clone"    active={tab === 'clone'}    onClick={() => setTab('clone')}    icon={<CopyIcon size={12} />}   label="Clone" />
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === 'blank'    && <BlankTab    existingProjects={existingProjects} onConfirm={onConfirm} onClose={onClose} isPending={isPending} />}
        {tab === 'template' && <TemplateTab onClose={onClose} />}
        {tab === 'file'     && <FromFileTab onClose={onClose} />}
        {tab === 'clone'    && <CloneTab    existingProjects={existingProjects} onClose={onClose} />}
      </div>
    </Dialog>
  )
}

function TabBtn({ active, onClick, icon, label }: {
  id: Tab
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-2 text-[12px] flex items-center gap-1.5 border-b-2 -mb-px transition-colors
        ${active ? 'border-accent text-accent font-medium' : 'border-transparent text-muted hover:text-text'}`}
    >
      {icon}
      {label}
    </button>
  )
}

// ── Tab 1: Blank — direct port of the legacy NewProjectModal body ─────────

function BlankTab({ existingProjects, onConfirm, onClose, isPending }: NewProjectWizardProps) {
  const [name, setName] = useState('new_project')
  const inputRef = useRef<HTMLInputElement>(null)
  // Dialog's own initial-focus effect now guards against stealing focus
  // already claimed inside the panel (see Dialog.tsx), so this plain
  // synchronous focus+select is enough — no microtask deferral needed.
  useEffect(() => { inputRef.current?.focus(); inputRef.current?.select() }, [])

  const trimmed = name.trim()
  const existing = existingProjects.find(p => p.name === trimmed)
  const willOverwrite = existing !== undefined && existing.bus_count > 0
  const commit = () => { if (trimmed) onConfirm(trimmed) }

  const handleBrowse = async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = window as any
    if (typeof w.showDirectoryPicker !== 'function') {
      toast.error('Folder picker requires a Chromium-based browser. Type the project name instead.')
      return
    }
    try {
      const handle = await w.showDirectoryPicker({ mode: 'read' })
      if (handle?.name) {
        setName(handle.name)
        setTimeout(() => inputRef.current?.select(), 0)
      }
    } catch (e) {
      if ((e as { name?: string })?.name !== 'AbortError') {
        toast.error(`Could not pick folder: ${(e as Error)?.message ?? e}`)
      }
    }
  }

  return (
    <div className="p-5 space-y-4">
      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-text">Project name</span>
        <div className="flex items-stretch gap-1.5">
          <input
            ref={inputRef}
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') onClose() }}
            className="flex-1 px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 font-mono"
            placeholder="my_project"
          />
          <button
            type="button"
            onClick={handleBrowse}
            title="Pick a folder — its name will be used as the project name"
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs border border-border rounded hover:border-accent hover:text-accent transition-colors text-muted"
          >
            <FolderOpen size={13} /> Browse…
          </button>
        </div>
        <span className="text-[10px] text-muted">
          Saved to <span className="font-mono">pypsa-gui/backend/projects/{trimmed || '…'}/</span>
        </span>
      </label>

      {willOverwrite && (
        <div className="flex items-start gap-2 px-3 py-2 bg-warn/10 border border-warn/30 rounded text-xs text-warn">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          <span>
            A project named <strong>{trimmed}</strong> already exists with {existing.bus_count} buses.
            Creating a new project here will <strong>overwrite it</strong>.
          </span>
        </div>
      )}

      <p className="text-[11px] text-muted leading-relaxed">
        Creates a blank project — the canvas starts empty. Add components from the sidebar palette
        or switch to the <strong>From file</strong> tab to import an existing network.
      </p>

      <div className="flex gap-2 pt-2 border-t border-border justify-end -mx-5 px-5">
        <button
          onClick={onClose}
          className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={commit}
          disabled={!trimmed || isPending}
          className={`px-4 py-1.5 rounded text-xs font-semibold transition-colors disabled:opacity-40
            ${willOverwrite
              ? 'bg-warn text-white hover:bg-warn/90'
              : 'bg-accent text-white hover:bg-accent/90'}`}
        >
          {isPending ? 'Creating…' : willOverwrite ? 'Overwrite & Create' : 'Create blank project'}
        </button>
      </div>
    </div>
  )
}

// ── Tab 2: Template — curated example networks ────────────────────────────

function TemplateTab({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const setCurrentProject = useUIStore(s => s.setCurrentProject)
  const setProjectName    = useUIStore(s => s.setProjectName)

  // Available templates import via the backend's /projects/from_template/<id>
  // endpoint, which copies the bundled network.nc into a fresh project dir and
  // loads it — same success path as FromFileTab's bundle import.
  const importMut = useMutation({
    mutationFn: (templateId: string) => projectsApi.createFromTemplate(templateId),
    onSuccess: (res) => {
      invalidateNetworkQueries(qc, res.imported)
      qc.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(res.imported)
      setProjectName(res.imported)
      appLog('INFO', `Created '${res.imported}' from template (${res.summary.buses} buses)`)
      toast.success(`Created '${res.imported}' from template`)
      onClose()
    },
    onError: (e: Error) => toast.error(`Template import failed: ${e.message}`),
  })

  const handlePick = useCallback((id: string, available: boolean) => {
    if (!available) {
      toast('This template isn\'t bundled yet. Use the From file tab to import a .nc network instead.', {
        icon: '📂',
        duration: 5000,
      })
      return
    }
    importMut.mutate(id)
  }, [importMut])

  // Which card (if any) is mid-import — used to show a per-card spinner label.
  const pendingId = importMut.isPending ? importMut.variables : null

  return (
    <div className="p-5">
      <div className="flex items-start gap-2 mb-3 px-2.5 py-2 bg-bg/60 border border-border/60 rounded text-[11px] text-muted">
        <Sparkles size={11} className="text-accent shrink-0 mt-0.5" />
        <span>
          Pre-built example networks. Click one to create a new editable project —
          the network is copied in and loaded immediately. Templates marked
          <strong> soon</strong> aren't bundled yet.
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        {TEMPLATES.map(t => {
          const loading = pendingId === t.id
          const disabled = importMut.isPending || !t.available
          return (
            <button
              key={t.id}
              onClick={() => handlePick(t.id, t.available)}
              disabled={disabled}
              title={t.available
                ? `Create a new project from the ${t.name} template`
                : 'Not bundled yet — use the From file tab instead'}
              className={`text-left border rounded-lg p-3.5 flex flex-col gap-2 transition-all
                ${t.available
                  ? 'border-border hover:border-accent/60 hover:bg-accent/5 cursor-pointer'
                  : 'border-dashed border-border/60 opacity-60 hover:opacity-90 cursor-not-allowed'}
                ${importMut.isPending && !loading ? 'opacity-40' : ''}`}
            >
              <div className="flex items-baseline gap-2">
                <span className="text-sm font-semibold text-text">{t.name}</span>
                {!t.available && (
                  <span className="text-[9px] uppercase tracking-wide bg-muted/15 text-muted px-1.5 py-px rounded">
                    soon
                  </span>
                )}
                {loading && (
                  <span className="text-[9px] uppercase tracking-wide bg-accent/15 text-accent px-1.5 py-px rounded animate-pulse">
                    creating…
                  </span>
                )}
              </div>
              <div className="text-[11px] text-muted leading-relaxed flex-1">{t.description}</div>
              <div className="text-[10px] text-muted font-mono">{t.buses} buses · {t.lines} lines · {t.badge}</div>
            </button>
          )
        })}
      </div>
      <div className="mt-4 text-[10px] text-muted/70 text-center">
        Template networks are bundled in <span className="font-mono">backend/project_templates/</span>.
      </div>
    </div>
  )
}

// ── Tab 3: From file — prefer .pypsaproj.zip (full project + results) ─────

function _uniqueProjectName(base: string, existing: string[]): string {
  const taken = new Set(existing.map(n => n.toLowerCase()))
  let name = base.trim() || 'imported_project'
  if (!taken.has(name.toLowerCase())) return name
  let i = 2
  while (taken.has(`${name}_${i}`.toLowerCase())) i += 1
  return `${name}_${i}`
}

function FromFileTab({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const setCurrentProject = useUIStore(s => s.setCurrentProject)
  const setProjectName    = useUIStore(s => s.setProjectName)
  const [dragOver, setDragOver] = useState(false)
  const { data: existingProjects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
  })

  const importMut = useMutation({
    mutationFn: async (file: File) => {
      const lower = file.name.toLowerCase()
      // Full project bundle — network + results + config + layout.
      if (lower.endsWith('.pypsaproj.zip') || lower.endsWith('.zip')) {
        return projectsApi.importBundle(file)
      }
      // Raw PyPSA netcdf: load into memory, then Save as a new project.
      // Network-only (no results_state.pkl / solver sidecars).
      if (lower.endsWith('.nc')) {
        const summary = await ioApi.importNetcdf(file)
        const base = file.name.replace(/\.nc$/i, '').trim() || 'imported_project'
        const names = (existingProjects as { name: string }[]).map(p => p.name)
        const name = _uniqueProjectName(base, names)
        await projectsApi.save(name, false, true, undefined, true)
        return { imported: name, summary }
      }
      throw new Error(
        'Unsupported file. Use a .pypsaproj.zip project bundle (network + results), or a .nc network file.',
      )
    },
    onSuccess: (res) => {
      invalidateNetworkQueries(qc, res.imported)
      qc.invalidateQueries({ queryKey: nk(res.imported, 'results') })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setCurrentProject(res.imported)
      setProjectName(res.imported)
      appLog('INFO', `Imported '${res.imported}' via wizard (${res.summary.buses} buses)`)
      toast.success(`Opened project '${res.imported}'`)
      onClose()
    },
    onError: (e: Error) => toast.error(`Import failed: ${e.message}`),
  })

  const onFile = (file: File) => {
    if (!file) return
    importMut.mutate(file)
  }

  return (
    <div className="p-5">
      <label
        className={`flex flex-col items-center justify-center gap-2 px-6 py-12 border-2 border-dashed rounded-lg cursor-pointer transition-colors
          ${dragOver ? 'border-accent bg-accent/5' : 'border-border hover:border-accent/40 hover:bg-bg/40'}`}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => {
          e.preventDefault()
          setDragOver(false)
          const f = e.dataTransfer.files?.[0]
          if (f) onFile(f)
        }}
      >
        <Upload size={28} className="text-muted" />
        <span className="text-sm font-medium text-text">Drop a .pypsaproj.zip project file here</span>
        <span className="text-[11px] text-muted">or click to browse · .nc also accepted (network only)</span>
        <input
          type="file"
          accept=".pypsaproj.zip,.zip,.nc"
          className="hidden"
          onChange={e => {
            const f = e.target.files?.[0]
            if (f) onFile(f)
          }}
        />
      </label>
      {importMut.isPending && (
        <div className="mt-3 text-xs text-muted text-center animate-pulse">
          Importing… (can take 30 s+ for large time-series bundles)
        </div>
      )}
      <p className="text-[11px] text-muted leading-relaxed mt-4">
        <strong>.pypsaproj.zip</strong> — recommended. Full project: network + solve results + solver config + layout.
        Restores Results tabs when the file was saved after a solve.
        <br />
        <strong>.nc</strong> — PyPSA network only; wrapped as a new project (no GUI side-results).
      </p>
    </div>
  )
}

// ── Tab 4: Clone — fork an existing project ───────────────────────────────

function CloneTab({ existingProjects, onClose }: {
  existingProjects: ProjectInfo[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const setCurrentProject = useUIStore(s => s.setCurrentProject)
  const setProjectName    = useUIStore(s => s.setProjectName)
  const currentProject    = useUIStore(s => s.currentProject)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [newName, setNewName]   = useState('')

  // Auto-suggest "<source>_copy" the first time a source is picked.
  useEffect(() => {
    if (sourceId && !newName) setNewName(`${sourceId}_copy`)
  }, [sourceId, newName])

  // Refresh the list when modal opens — the parent may have a stale cache.
  const { data: freshList = existingProjects } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
  })

  // Dirty-state probe. Any pending undo means there are unsaved edits in the
  // CURRENT project that the clone's `projectsApi.load(source)` step will
  // discard (load REPLACES the in-memory network with the source's contents).
  // We warn the user before they hit Clone — the established pattern in this
  // codebase per CLAUDE.md ("POST /api/projects/{name} is a **destructive
  // save**, not a load"). Polling at 3 s matches the AppHeader's badge cadence.
  const { data: undoInfo } = useQuery({
    queryKey: nk(currentProject, 'undoInfo'),
    queryFn: networkApi.undoInfo,
    refetchInterval: 3000,
    staleTime: 0,
  })
  const dirty = (undoInfo?.depth ?? 0) > 0

  const cloneMut = useMutation({
    mutationFn: async ({ src, dest }: { src: string; dest: string }) => {
      // Load source into memory then save under destination. Two-step is
      // simpler than a backend clone endpoint and uses existing primitives.
      // After load(src) the backend is bound to `src`; save(dest, rebind=true)
      // writes the in-memory (src) network under `dest` AND rebinds the backend
      // to `dest` so the post-clone setCurrentProject(dest) below stays in sync
      // — otherwise saving the clone would 409 against the stale `src` binding.
      // expect is omitted: this is a deliberate save-under-a-new-name.
      await projectsApi.load(src)
      const res = await projectsApi.save(dest, false, true, undefined, true)
      return res
    },
    onSuccess: (res) => {
      invalidateNetworkQueries(qc, res.saved)
      setCurrentProject(res.saved)
      setProjectName(res.saved)
      qc.invalidateQueries({ queryKey: ['projects'] })
      appLog('INFO', `Cloned '${sourceId}' → '${res.saved}' via wizard`)
      toast.success(`Cloned to '${res.saved}'`)
      onClose()
    },
    onError: (e: Error) => {
      // Partial-failure note: if load() succeeded but save() didn't, the
      // in-memory network is now the SOURCE, not the original project. The
      // user must open their original to recover. We don't auto-recover here
      // because we can't reliably distinguish "save failed" from "saved
      // partially". Tell the user explicitly.
      toast.error(
        `Clone failed: ${e.message}. The in-memory network may now be '${sourceId}' — reopen your original project to recover.`,
        { duration: 8000 },
      )
      appLog('ERROR', `Clone failed: ${e.message}`)
    },
  })

  const trimmed = newName.trim()
  // Overwrite detection. Mirrors BlankTab's `willOverwrite` logic so users
  // see the same yellow banner pattern across tabs. `bus_count > 0` filters
  // out empty projects (which are safe to overwrite without warning).
  const destExisting = freshList.find(p => p.name === trimmed)
  const willOverwrite = destExisting !== undefined && destExisting.bus_count > 0
  // Will the load step lose unsaved edits to the current project? Only when
  // there's an active project AND it's different from both src/dest AND there
  // are pending undo steps. If src === currentProject, no edits to lose
  // (load(src) just reloads the project the user already has open).
  const willDropDirty = dirty && currentProject != null && currentProject !== sourceId
  const canClone = sourceId && trimmed && trimmed !== sourceId

  return (
    <div className="p-5 flex flex-col gap-4">
      <div>
        <div className="text-[11px] font-bold uppercase tracking-wide text-muted/70 mb-1.5">
          Source project
        </div>
        <div className="max-h-40 overflow-y-auto border border-border rounded">
          {freshList.length === 0 ? (
            <div className="px-3 py-4 text-xs text-muted text-center">No projects to clone from.</div>
          ) : (
            freshList.map(p => (
              <button
                key={p.name}
                onClick={() => setSourceId(p.name)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors
                  ${sourceId === p.name ? 'bg-accent/10 text-accent' : 'hover:bg-bg/40 text-text'}`}
              >
                <FolderOpen size={11} className="shrink-0" />
                <span className="flex-1 truncate font-mono">{p.name}</span>
                <span className="text-[10px] text-muted shrink-0">
                  {p.bus_count} buses · {formatRelativeTime(p.created_at) ?? '—'}
                </span>
              </button>
            ))
          )}
        </div>
      </div>

      <label className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-text">New name</span>
        <input
          type="text"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          disabled={!sourceId}
          placeholder={sourceId ? `${sourceId}_copy` : 'pick a source first'}
          className="px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 font-mono disabled:opacity-40"
        />
      </label>

      {/* Warnings (yellow). Two cases worth flagging up-front so the user
          doesn't lose work:
            1. Destination overwrites an existing non-empty project.
            2. The current project has unsaved edits that the load step will
               discard (load replaces in-memory state).
          Both surface as the same banner style as BlankTab.willOverwrite. */}
      {willOverwrite && (
        <div className="flex items-start gap-2 px-3 py-2 bg-warn/10 border border-warn/30 rounded text-xs text-warn">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          <span>
            A project named <strong>{trimmed}</strong> already exists with {destExisting!.bus_count} buses.
            Cloning will <strong>overwrite it</strong>.
          </span>
        </div>
      )}
      {willDropDirty && (
        <div className="flex items-start gap-2 px-3 py-2 bg-warn/10 border border-warn/30 rounded text-xs text-warn">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          <span>
            <strong>{currentProject}</strong> has unsaved edits ({undoInfo?.depth ?? 0} undo step{(undoInfo?.depth ?? 0) === 1 ? '' : 's'})
            that will be <strong>discarded</strong> when the source project is loaded.
            Cancel and save first if you want to keep them.
          </span>
        </div>
      )}

      <div className="flex gap-2 pt-2 border-t border-border justify-end -mx-5 px-5">
        <button
          onClick={onClose}
          className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={() => sourceId && canClone && cloneMut.mutate({ src: sourceId, dest: trimmed })}
          disabled={!canClone || cloneMut.isPending}
          className={`px-4 py-1.5 rounded text-xs font-semibold transition-colors disabled:opacity-40 text-white ${
            willOverwrite || willDropDirty
              ? 'bg-warn hover:bg-warn/90'
              : 'bg-accent hover:bg-accent/90'
          }`}
        >
          {cloneMut.isPending
            ? 'Cloning…'
            : willOverwrite
            ? 'Overwrite & Clone'
            : willDropDirty
            ? 'Discard edits & Clone'
            : 'Clone project'}
        </button>
      </div>
    </div>
  )
}
