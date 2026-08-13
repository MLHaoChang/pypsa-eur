import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Play, X, Trash2, Loader, ChevronRight, ChevronDown,
  CheckCircle2, AlertCircle, Clock, CircleSlash, Plus, PlugZap,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { projectsApi } from '../api/projects'
import { solveQueueApi, isActive, isTerminal, type SolveJob, type SolveJobStatus, type ResultsBundle } from '../api/solveQueue'
import { useSolveQueue, useEnqueueSolve, useAbortJob, useClearFinished } from '../hooks/useSolveQueue'
import { useAuth } from '../auth/AuthProvider'

const STATUS_META: Record<SolveJobStatus, { label: string; cls: string; Icon: typeof Clock }> = {
  queued:      { label: 'Queued',      cls: 'text-muted bg-panel border-border',                Icon: Clock },
  running:     { label: 'Running',     cls: 'text-accent bg-accent/10 border-accent/30',        Icon: Loader },
  completed:   { label: 'Completed',   cls: 'text-emerald-600 bg-emerald-500/10 border-emerald-500/30', Icon: CheckCircle2 },
  failed:      { label: 'Failed',      cls: 'text-danger bg-danger/10 border-danger/30',        Icon: AlertCircle },
  aborted:     { label: 'Aborted',     cls: 'text-amber-600 bg-amber-500/10 border-amber-500/30', Icon: CircleSlash },
  // Visually separate from `aborted` on purpose: the user did NOT stop this
  // one. Slate rather than amber, and a plug icon rather than a "no entry".
  interrupted: { label: 'Interrupted', cls: 'text-slate-500 bg-slate-500/10 border-slate-500/30', Icon: PlugZap },
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

/**
 * Whether this row has a log worth opening.
 *
 * A `queued` job has produced nothing yet — that is the only status this
 * excludes. Deliberately NOT `isTerminal(job)`: that set (`TERMINAL_STATUSES`
 * in `api/solveQueue.ts`) is narrower in the OTHER direction — it excludes
 * `running`, whose in-progress log is exactly what R9's live tail exists to
 * show. Using `isTerminal` here would disable the expand control on the one
 * status where the log is most useful mid-solve. `job.status !== 'queued'`
 * covers `running` and every terminal status — `completed` / `failed` /
 * `aborted` / `interrupted` (increment 3, R27) — with no per-status branch.
 *
 * NOTE: as of increment 3, `interrupted` is a real `SolveJobStatus` and this
 * function correctly returns `true` for it. Until Task 16a this was
 * dormant-correct: `services/solve_queue.list_jobs` served the in-memory
 * queue only, and boot reconciliation (`services/solve_job_store.
 * reconcile_on_boot`) deliberately never re-admits a `running → interrupted`
 * row into that in-memory store (the crash-loop guard), so no interrupted job
 * could ever reach this component. Task 16a made `GET /api/simulation/queue`
 * merge persisted rows back into the listing at the READ boundary (never by
 * re-admitting anything to the in-memory store, so the crash-loop guard is
 * untouched) — an interrupted job now reaches this component for real, and
 * this line was already correct for it, unchanged.
 *
 * A redacted row (`project_id: null`) is one the caller may not see at all,
 * so its endpoints would 404 — disabling it here means the UI and the
 * authorization agree instead of rendering a control that always fails.
 */
export function canExpandJob(job: SolveJob): boolean {
  if (job.project_id == null) return false
  return job.status !== 'queued'
}

function JobLogPanel({ jobId, live }: { jobId: string; live: boolean }) {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)

    if (!live) {
      // Terminal row: the retained log is a fixed snapshot. One REST read,
      // nothing to keep open or clean up.
      setLines([])
      solveQueueApi.jobLogHistory(jobId)
        .then(r => { if (!cancelled) setLines(r.lines) })
        .catch(() => { if (!cancelled) setError('Could not read this job’s log.') })
      return () => { cancelled = true }
    }

    // Live rows follow the job's own stream, not `/api/simulation/log_stream`
    // — which binds to the ACTIVE context and would serve a different project's
    // log (or none) whenever the user is not viewing the solving project.
    //
    // The stream (Task 9) already replays this job's full history before
    // going live, in the SAME connection — it is deliberately the ONLY
    // history source while live. A second, separate `jobLogHistory` fetch
    // here would race that replay: whichever resolves second would either
    // clobber live lines the other had already appended, or double-render
    // the whole backlog, depending on network timing on any given render.
    // One channel avoids that double-rendering by construction. The stream
    // itself can still repeat a single line at ITS OWN history/live seam
    // (subscribed-before-snapshotted, per Task 9's review) — harmless here
    // since lines are plain strings joined into a `<pre>`, not React-keyed.
    setLines([])
    const es = new EventSource(solveQueueApi.jobLogStreamUrl(jobId))
    let doneReceived = false
    let lastEventAt = Date.now()
    // Mirrors `createLogStream` (api/simulation.ts:567-621) — EventSource
    // auto-reconnects on a transient error (browser sleep, a network blip, a
    // server hiccup) unless the app closes it. Closing on the FIRST error, as
    // this branch originally did, silently freezes the log at whatever had
    // arrived so far: no more lines, no indication anything is wrong, and the
    // row keeps reading as "live". That is the exact anti-pattern this file's
    // sibling documents fixing once already — only declare the stream dead
    // once no event has arrived for STALE_MS, and even then verify with the
    // job's own status before giving up: a long native-solver phase can be
    // silent for a while and looks identical to a lost connection.
    const STALE_MS = 30_000
    es.onmessage = (e) => {
      if (cancelled) return
      lastEventAt = Date.now()
      // A line arriving is the recovery signal for a prior stale/error banner
      // — without clearing it here, a connection that heals on its own leaves
      // a stale "connection lost" message sitting on top of live data that is
      // actually accumulating fine behind it.
      setError(null)
      setLines(prev => [...prev, e.data])
    }
    es.addEventListener('done', () => { doneReceived = true; es.close() })
    es.onerror = () => {
      if (cancelled || doneReceived) return
      if (es.readyState === EventSource.CLOSED) {
        // The browser's own reconnect budget is already exhausted — nothing
        // further will arrive on this connection, unlike a transient error.
        setError('Log stream lost before the job finished.')
        return
      }
      if (Date.now() - lastEventAt > STALE_MS) {
        solveQueueApi.jobLogHistory(jobId)
          .then(r => {
            if (cancelled || doneReceived) return
            if (r.status === 'running') {
              // Still running — the quiet spell was a real solver phase, not
              // a dead connection. Let the browser keep retrying.
              lastEventAt = Date.now()
            } else {
              // The job finished while the stream was stuck. `live` will flip
              // to false on the next queue poll and re-fetch the authoritative
              // retained log via the branch above; surface the gap in the
              // meantime rather than leaving a frozen "live" view.
              es.close()
              setError('Log stream lost — the job has since finished.')
            }
          })
          .catch(() => {
            if (cancelled || doneReceived) return
            // The verification request itself is unreachable — plausibly the
            // SAME outage that broke the stream (backend down, network gone).
            // MUST close: leaving `es` open lets the browser's own ~3s
            // auto-reconnect keep re-firing `onerror`, and since no message
            // ever arrives to advance `lastEventAt`, every retry re-enters
            // this stale branch and fires ANOTHER jobLogHistory request —
            // unbounded, for as long as the row stays expanded, hammering a
            // backend that just said it was unreachable.
            es.close()
            setError('Log stream silent and unreachable — connection lost.')
          })
      }
    }
    return () => { cancelled = true; es.close() }
  }, [jobId, live])

  if (error) return <div className="px-3 py-2 text-[11px] text-danger">{error}</div>
  if (lines.length === 0) {
    return <div className="px-3 py-2 text-[11px] text-muted">No log lines for this job.</div>
  }
  return (
    <pre className="px-3 py-2 max-h-56 overflow-auto text-[10px] leading-snug font-mono text-muted bg-bg-2/40 border-t border-border whitespace-pre-wrap">
      {lines.join('\n')}
    </pre>
  )
}

function JobRow({ job, onAbort }: { job: SolveJob; onAbort: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const canAbort = job.status === 'queued' || job.status === 'running'
  // A redacted row names no project, so there is nothing to preview and no name
  // to put in the URL — `/projects/null/results_bundle` is what the unguarded
  // version would have requested.
  const name = job.project_id
  const canExpand = canExpandJob(job)
  const canPreview = job.status === 'completed' && name != null
  return (
    <div className="rounded-lg border border-border bg-bg overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => canExpand && setExpanded(v => !v)}
          disabled={!canExpand}
          className={`p-0.5 rounded ${canExpand ? 'text-muted hover:text-text' : 'opacity-0 pointer-events-none'}`}
          title={canExpand ? 'Show this job’s log' : 'Not available for this job'}
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
            {job.status === 'interrupted' && (
              <span>Did not finish — stopped by a restart, not by you</span>
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
      {expanded && canExpand && (
        <>
          <JobLogPanel jobId={job.id} live={job.status === 'running'} />
          {canPreview && name != null && <JobResultsPreview name={name} />}
        </>
      )}
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

  const onAbort = (id: string) => {
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
