import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Play, X, Trash2, Loader, ChevronRight, ChevronDown,
  CheckCircle2, AlertCircle, Clock, CircleSlash, Plus,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { projectsApi } from '../api/projects'
import { solveQueueApi, isActive, isTerminal, type SolveJob, type SolveJobStatus, type ResultsBundle } from '../api/solveQueue'
import { useSolveQueue, useEnqueueSolve, useAbortJob, useClearFinished } from '../hooks/useSolveQueue'
import { useAuth } from '../auth/AuthProvider'

const STATUS_META: Record<SolveJobStatus, { label: string; cls: string; Icon: typeof Clock }> = {
  queued:    { label: 'Queued',    cls: 'text-muted bg-panel border-border',                Icon: Clock },
  running:   { label: 'Running',   cls: 'text-accent bg-accent/10 border-accent/30',        Icon: Loader },
  completed: { label: 'Completed', cls: 'text-emerald-600 bg-emerald-500/10 border-emerald-500/30', Icon: CheckCircle2 },
  failed:    { label: 'Failed',    cls: 'text-danger bg-danger/10 border-danger/30',        Icon: AlertCircle },
  aborted:   { label: 'Aborted',   cls: 'text-amber-600 bg-amber-500/10 border-amber-500/30', Icon: CircleSlash },
}

function fmtObjective(v: number | null): string {
  if (v == null || !isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e9) return `€${(v / 1e9).toFixed(2)} B`
  if (abs >= 1e6) return `€${(v / 1e6).toFixed(2)} M`
  if (abs >= 1e3) return `€${(v / 1e3).toFixed(1)} k`
  return `€${v.toFixed(0)}`
}

function StatusBadge({ status }: { status: SolveJobStatus }) {
  const m = STATUS_META[status]
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold border ${m.cls}`}>
      <m.Icon size={11} className={status === 'running' ? 'animate-spin' : ''} />
      {m.label}
    </span>
  )
}

// Compact "Σ dispatch per carrier over the modelled snapshots" mix, computed
// client-side from the bundle's generators payload + carrier map. Deliberately
// an UNWEIGHTED snapshot sum (a headline preview, not horizon-scaled energy).
function generationMix(b: ResultsBundle): Array<{ carrier: string; total: number }> {
  const g = b.generators
  if (!g) return []
  const per: Record<string, number> = {}
  g.columns.forEach((col, ci) => {
    const carrier = b.carriers[col] ?? 'other'
    let sum = 0
    for (const row of g.data) {
      const v = row[ci]
      if (typeof v === 'number' && isFinite(v)) sum += v
    }
    per[carrier] = (per[carrier] ?? 0) + sum
  })
  return Object.entries(per)
    .map(([carrier, total]) => ({ carrier, total }))
    .filter(e => Math.abs(e.total) > 1e-6)
    .sort((a, b2) => b2.total - a.total)
}

function JobResultsPreview({ name }: { name: string }) {
  const { data, isLoading, isError } = useQuery({
    // Keyed by the QUEUED job's project name (not the active project) — each
    // queue row previews its OWN on-disk results. `nk(name, …)` yields
    // `['resultsBundle', name, 'lopf']`, the same shape this used before.
    queryKey: nk(name, 'resultsBundle', 'lopf'),
    queryFn: () => solveQueueApi.resultsBundle(name, 'lopf'),
    staleTime: 30_000,
  })

  if (isLoading) {
    return <div className="px-3 py-2 text-[11px] text-muted flex items-center gap-1.5"><Loader size={12} className="animate-spin" /> Loading results…</div>
  }
  if (isError) return <div className="px-3 py-2 text-[11px] text-danger">Couldn't read results.</div>
  if (!data || !data.available) return <div className="px-3 py-2 text-[11px] text-muted">No dispatch on disk for this project.</div>

  const mix = generationMix(data)
  const max = mix.reduce((m, e) => Math.max(m, Math.abs(e.total)), 0) || 1
  return (
    <div className="px-3 py-2 space-y-2 bg-bg-2/40 border-t border-border">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        <span className="text-muted">Objective <span className="text-text font-semibold">{fmtObjective(data.objective)}</span></span>
        {data.solve_time != null && <span className="text-muted">Solve time <span className="text-text font-semibold">{data.solve_time}s</span></span>}
        {data.condition && <span className="text-muted">Status <span className="text-text font-semibold">{data.condition}</span></span>}
      </div>
      {mix.length > 0 ? (
        <div className="space-y-1">
          <div className="text-[9px] font-mono uppercase tracking-wide text-muted">Σ generation over modelled snapshots</div>
          {mix.slice(0, 8).map(e => (
            <div key={e.carrier} className="flex items-center gap-2">
              <span className="w-20 truncate text-[10px] text-text">{e.carrier}</span>
              <div className="flex-1 h-2.5 rounded bg-panel overflow-hidden">
                <div className="h-full bg-accent/70 rounded" style={{ width: `${(Math.abs(e.total) / max) * 100}%` }} />
              </div>
              <span className="w-16 text-right text-[10px] tabular-nums text-muted">{e.total.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-[11px] text-muted">No generator dispatch to summarise.</div>
      )}
    </div>
  )
}

// The row is a job the caller may not see: the backend nulled its identifying
// fields. Say so plainly rather than rendering an empty element — the row's id,
// status, position and timings are legitimately visible and the queue depth is
// the thing the caller actually needs from it.
export const REDACTED_PROJECT_LABEL = 'Hidden — another organisation’s project'

function JobRow({ job, onAbort }: { job: SolveJob; onAbort: (id: number) => void }) {
  const [expanded, setExpanded] = useState(false)
  const canAbort = job.status === 'queued' || job.status === 'running'
  // A redacted row names no project, so there is nothing to preview and no name
  // to put in the URL — `/projects/null/results_bundle` is what the unguarded
  // version would have requested.
  const name = job.project_id
  const canPreview = job.status === 'completed' && name != null
  return (
    <div className="rounded-lg border border-border bg-bg overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => canPreview && setExpanded(v => !v)}
          disabled={!canPreview}
          className={`p-0.5 rounded ${canPreview ? 'text-muted hover:text-text' : 'opacity-0 pointer-events-none'}`}
          title={canPreview ? 'Preview results' : 'Not available for this job'}
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {name != null ? (
              <span className="truncate text-[12px] font-medium text-text" title={name}>{name}</span>
            ) : (
              <span className="truncate text-[12px] font-medium text-muted italic" title={REDACTED_PROJECT_LABEL}>
                {REDACTED_PROJECT_LABEL}
              </span>
            )}
            {job.status === 'queued' && job.position != null && (
              <span className="text-[10px] text-muted">#{job.position} in line</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-muted">
            {job.status === 'completed' && <span>{fmtObjective(job.objective)}{job.solve_time != null ? ` · ${job.solve_time}s` : ''}</span>}
            {job.status === 'failed' && <span className="text-danger truncate" title={job.error ?? job.condition ?? ''}>{job.error ?? job.condition ?? 'Failed'}</span>}
            {job.status === 'aborted' && (
              <span>{job.condition === 'superseded' ? 'Superseded by a newer run' : 'Aborted by user'}</span>
            )}
          </div>
        </div>
        <StatusBadge status={job.status} />
        {canAbort && (
          <button
            onClick={() => onAbort(job.id)}
            className="p-1 rounded text-muted hover:text-danger hover:bg-danger/10 transition-colors"
            title={job.status === 'running' ? 'Abort this solve' : 'Remove from queue'}
          >
            <X size={14} />
          </button>
        )}
      </div>
      {expanded && canPreview && name != null && <JobResultsPreview name={name} />}
    </div>
  )
}

export default function SolveQueuePanel() {
  const { currentProject, openTabs, markProjectSaved } = useUIStore()
  const { data, isLoading, isError } = useSolveQueue()
  const enqueue = useEnqueueSolve()
  const abortJob = useAbortJob()
  const clearFinished = useClearFinished()
  const { user } = useAuth()
  const [adding, setAdding] = useState<string | null>(null)

  // The queue is process-global and shared across organisations, so clearing it
  // is gated server-side on `is_super_admin` (routers/solve_queue.py). NOT on
  // `useAuth().isAdmin` — that is `hasAdminConsoleAccess`, which is also true for
  // an ORG admin (`role === 'admin'`), who would see an enabled button and still
  // get a 403. Read the raw flag so the control matches the route exactly.
  const canClearFinished = Boolean(user?.is_super_admin)

  const jobs = data?.jobs ?? []
  const activeCount = jobs.filter(isActive).length
  const finishedCount = jobs.filter(isTerminal).length
  // Project names that already have a queued/running job — don't offer to re-add.
  // A redacted row names no project and can match nothing, so drop it rather
  // than letting `null` sit in the set.
  const activeProjects = new Set(
    jobs.filter(isActive).map(j => j.project_id).filter((n): n is string => n != null),
  )

  // Save (only when it's the active project — it may carry unsaved edits) then
  // enqueue. The dispatcher solves the SAVED version, so persistence first is
  // required for `name` === currentProject; other open tabs were already saved
  // on switch-away, so they enqueue directly.
  const addToQueue = async (name: string) => {
    if (adding) return
    setAdding(name)
    try {
      if (name === currentProject) {
        await projectsApi.save(name, false, false, name)
        markProjectSaved(name)
      }
      await enqueue.mutateAsync(name)
      toast.success(`Queued '${name}' to solve`)
    } catch (e) {
      const resp = (e as { response?: { status?: number; data?: { detail?: string } } })?.response
      const detail = resp?.data?.detail
      if (resp?.status === 409) {
        toast.error(detail ?? 'Cannot queue while a solve is in progress — wait for the queue to finish.')
      } else if (resp?.status === 404) {
        toast.error(`'${name}' has no saved network yet — save it first.`)
      } else {
        toast.error(`Could not queue '${name}': ${(e as Error)?.message ?? e}`)
      }
    } finally {
      setAdding(null)
    }
  }

  const onAbort = (id: number) => {
    abortJob.mutate(id, {
      onError: (e) => toast.error(`Abort failed: ${(e as Error)?.message ?? e}`),
    })
  }

  // Open tabs that aren't already queued/running — quick-add targets.
  // openTabs is Array<{name, lastInteractedAt}> (B8); this list is just names.
  const addableTabs = openTabs.map(t => t.name).filter(n => !activeProjects.has(n))

  return (
    <div className="flex flex-col h-full min-h-0 bg-bg">
      {/* Header / actions */}
      <div className="px-4 py-3 border-b border-border space-y-2 shrink-0">
        <p className="text-[11px] text-muted leading-snug">
          Queue saved projects to solve one after another, unattended. Results persist to
          disk — view a finished solve below without loading the project.
          {activeCount > 0 && (
            <span className="text-accent">
              {' '}A project solving in the queue is read-only until it finishes; other projects stay editable.
            </span>
          )}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => currentProject && addToQueue(currentProject)}
            disabled={!currentProject || !!adding || (currentProject != null && activeProjects.has(currentProject))}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-semibold bg-accent text-white hover:bg-accent/90 disabled:opacity-40 transition-colors"
            title={currentProject ? `Save '${currentProject}' and add it to the solve queue` : 'No active project'}
          >
            {adding === currentProject ? <Loader size={12} className="animate-spin" /> : <Play size={12} />}
            Queue current project
          </button>
          <button
            onClick={() => clearFinished.mutate()}
            disabled={!canClearFinished || finishedCount === 0 || clearFinished.isPending}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium border border-border text-muted hover:text-text hover:bg-panel disabled:opacity-40 transition-colors"
            title={canClearFinished
              ? 'Remove completed / failed / aborted jobs from the list'
              : 'Only super-admins can clear finished jobs — the solve queue is shared across organisations'}
          >
            <Trash2 size={12} /> Clear finished
          </button>
        </div>
        {addableTabs.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            <span className="text-[10px] text-muted">Add open project:</span>
            {addableTabs.map(t => (
              <button
                key={t}
                onClick={() => addToQueue(t)}
                disabled={!!adding}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border border-border text-muted hover:text-accent hover:border-accent/40 disabled:opacity-40 transition-colors"
                title={`Add '${t}' to the queue`}
              >
                {adding === t ? <Loader size={10} className="animate-spin" /> : <Plus size={10} />}
                {t}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Job list */}
      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-2">
        {isLoading && (
          <div className="text-[12px] text-muted flex items-center gap-2"><Loader size={14} className="animate-spin" /> Loading queue…</div>
        )}
        {isError && <div className="text-[12px] text-danger">Couldn't reach the solve queue.</div>}
        {!isLoading && !isError && jobs.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center text-muted gap-2 py-10">
            <Clock size={28} className="opacity-40" />
            <p className="text-[12px]">The solve queue is empty.</p>
            <p className="text-[11px]">Add a saved project above; it'll solve in the background and its results will appear here.</p>
          </div>
        )}
        {jobs.map(job => <JobRow key={job.id} job={job} onAbort={onAbort} />)}
      </div>
    </div>
  )
}
