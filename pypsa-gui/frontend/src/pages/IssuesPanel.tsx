import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, AlertCircle, CheckCircle2, RefreshCw, ArrowRight } from 'lucide-react'
import toast from 'react-hot-toast'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import type { ValidationIssue } from '../api/types'
import { PageBody, PageSection, RowGrid, StatCard, Btn } from '../components/PageKit'

// IssuesPanel — surfaces the backend's preflight validation findings as a
// browsable list. Reuses /api/simulation/preflight, which is the same gate
// /run uses; if this panel is empty the solver run will not fail on
// pre-checks (it can still hit numerical/feasibility errors during solve).
//
// Why a panel and not a banner: validation findings vary in severity and
// have detailed messages — a banner is too small. The panel lets users
// scan, jump to the offending component, and rerun the check.
export default function IssuesPanel() {
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const setHighlightedComponent = useUIStore(s => s.setHighlightedComponent)
  const openRightPanel = useUIStore(s => s.openRightPanel)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)

  // Refetch on mount + every 15 s. The model may change between solves
  // (user edits buses, costs, etc.), so polling keeps the panel honest
  // without the user having to remember to refresh.
  const { data, isFetching, refetch } = useQuery({
    queryKey: ['preflight'],
    queryFn: simulationApi.preflight,
    refetchOnMount: 'always',
    refetchInterval: 15_000,
    staleTime: 5_000,
  })

  // Manual revalidate. Useful right after a bulk edit, before clicking Run.
  // Single refetch — previous implementation used `useMutation` that called
  // `preflight` then `refetch()` on success, sending two requests for one
  // user click. `refetch()` is sufficient: it re-runs the query function,
  // updates the cached data, and triggers re-render.
  const [isRevalidating, setIsRevalidating] = useState(false)
  const revalidate = async () => {
    setIsRevalidating(true)
    try {
      await refetch()
      toast.success('Validation rerun')
    } catch {
      toast.error('Validation failed')
    } finally {
      setIsRevalidating(false)
    }
  }

  // Split errors first, warnings second. Within each, preserve backend
  // order so the user sees the same sequence the solver would emit.
  const grouped = useMemo(() => {
    const issues = data?.issues ?? []
    return {
      errors:   issues.filter(i => i.severity === 'error'),
      warnings: issues.filter(i => i.severity === 'warning'),
    }
  }, [data?.issues])

  const allOk = data && data.errors === 0 && data.warnings === 0
  const total = (data?.issues?.length ?? 0)

  // Jump straight to the offending asset's edit form. The Issues view is
  // itself a slide panel, and PropertiesPanel only mounts when no slide panel
  // is active (App.tsx) — so `setSlidePanel(null)` is what actually reveals
  // the editor. Without it the component is "selected" but stays invisible
  // behind the still-open Issues panel.
  const jumpTo = (type: string, name: string) => {
    setSelectedComponent({ type, name })
    setHighlightedComponent({ type, name })
    openRightPanel()
    setSlidePanel(null)
  }

  return (
    <div className="flex flex-col h-full">
      <PageBody>
        {/* StatCard strip — Errors leads as the accent metric. */}
        <RowGrid cols={2}>
          <StatCard
            accent
            eyebrow="Errors"
            value={data ? data.errors : '—'}
            sub="Blocking — solver will not start"
            deltaState={data && data.errors > 0 ? 'err' : undefined}
            delta={data && data.errors > 0 ? 'Fix to run' : data ? 'Clear' : undefined}
          />
          <StatCard
            eyebrow="Warnings"
            value={data ? data.warnings : '—'}
            sub="Advisable to fix"
            deltaState={data && data.warnings > 0 ? 'warn' : undefined}
            delta={data && data.warnings > 0 ? 'Review' : data ? 'Clear' : undefined}
          />
        </RowGrid>

        <PageSection
          title="Findings"
          count={total}
          hint="Live preflight — the same gate Run LOPF uses"
          right={
            <Btn onClick={revalidate} disabled={isRevalidating || isFetching}>
              <RefreshCw size={11} className={isRevalidating || isFetching ? 'animate-spin' : ''} />
              Revalidate
            </Btn>
          }
          bodyClassName=""
        >
          {data?.deferred ? (
            // Solver worker still active — backend declined to run the
            // validator on the transient LP state. Two flavours:
            //   * regular: just wait (animated spinner)
            //   * stuck: user already aborted but HiGHS won't yield — only
            //            a backend restart fixes it (danger banner + steps)
            data.deferred_stuck ? (
              <div className="flex flex-col items-start gap-3 px-6 py-6 border-l-4 border-danger bg-danger/5">
                <div className="flex items-center gap-2">
                  <AlertCircle className="text-danger" size={20} />
                  <span className="text-sm font-semibold text-text">Solver stuck after Abort</span>
                </div>
                <span className="text-xs text-muted max-w-xl">
                  {data.deferred_reason ?? 'Solver was aborted but is still running in native solver code. The PyPSA lock will not release on its own.'}
                </span>
                <div className="text-[11px] text-text">
                  <span className="font-semibold">To recover, the backend server needs a restart:</span>
                  <ol className="list-decimal pl-5 mt-1 space-y-0.5 text-muted">
                    <li>Your work is safe — a snapshot was saved to disk just before the run started.</li>
                    <li>Restart the backend, then reload your project from the file picker to pick that snapshot back up.</li>
                    <li>
                      If someone set this up for you, ask them to restart it. If you started it yourself, stop the
                      server window and run:
                      <code className="block mt-1 px-1.5 py-1 bg-bg-2 rounded break-all">.pixi/envs/default/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --app-dir pypsa-gui/backend</code>
                    </li>
                  </ol>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center px-8 py-12 gap-2 text-center">
                <RefreshCw className="text-accent animate-spin" size={24} />
                <span className="text-sm font-medium text-text">Validation deferred</span>
                <span className="text-xs text-muted max-w-md">
                  {data.deferred_reason ?? 'Solver worker still active — validation will resume once the LP transforms are reverted.'}
                </span>
              </div>
            )
          ) : allOk ? (
            <div className="flex flex-col items-center justify-center px-8 py-12 gap-2 text-center">
              <CheckCircle2 className="text-success" size={28} />
              <span className="text-sm font-medium text-text">All checks passed</span>
              <span className="text-xs text-muted max-w-sm">
                The model is ready to run. The solver may still hit numerical issues during the actual solve.
              </span>
            </div>
          ) : !data ? (
            <div className="px-4 py-10 text-center text-[11.5px] text-muted">Loading validation…</div>
          ) : (
            <div className="flex flex-col">
              {grouped.errors.length > 0 && (
                <>
                  <GroupHeader label="Errors" count={grouped.errors.length} severity="error" />
                  {grouped.errors.map((issue, i) => (
                    <IssueRow key={`err-${i}-${issue.code}-${issue.name}`} issue={issue} onJumpTo={jumpTo} />
                  ))}
                </>
              )}
              {grouped.warnings.length > 0 && (
                <>
                  <GroupHeader label="Warnings" count={grouped.warnings.length} severity="warning" />
                  {grouped.warnings.map((issue, i) => (
                    <IssueRow key={`warn-${i}-${issue.code}-${issue.name}`} issue={issue} onJumpTo={jumpTo} />
                  ))}
                </>
              )}
            </div>
          )}
        </PageSection>
      </PageBody>
    </div>
  )
}

function GroupHeader({ label, count, severity }: {
  label: string; count: number; severity: 'error' | 'warning'
}) {
  const color = severity === 'error' ? 'text-danger' : 'text-warn'
  return (
    <div className={`flex items-center gap-1.5 px-4 pt-3 pb-1.5 text-[9px] font-bold uppercase tracking-[0.14em] ${color}`}>
      {label} <span className="text-muted font-mono">({count})</span>
    </div>
  )
}

// Maps the backend's `component_class` (PyPSA's internal Python class name)
// to the UI's component-type string. Both happen to coincide for most
// classes; this is the place to add aliases if they drift.
const COMPONENT_TYPE_MAP: Record<string, string> = {
  Bus: 'Bus',
  Generator: 'Generator',
  Load: 'Load',
  Line: 'Line',
  Link: 'Link',
  StorageUnit: 'StorageUnit',
  Store: 'Store',
  Transformer: 'Transformer',
}

function IssueRow({ issue, onJumpTo }: {
  issue: ValidationIssue
  onJumpTo: (type: string, name: string) => void
}) {
  const isError = issue.severity === 'error'
  const Icon = isError ? AlertCircle : AlertTriangle
  const hasTarget = !!issue.component_class && !!issue.name &&
                    !!COMPONENT_TYPE_MAP[issue.component_class]

  return (
    <div className="flex items-start gap-3 px-4 py-3 border-t border-border first:border-t-0 hover:bg-bg-2 transition-colors">
      {/* Severity tile — tinted square, mirrors the design's .iss-sev */}
      <span
        className={`mt-0.5 shrink-0 w-6 h-6 grid place-items-center rounded-md border
          ${isError
            ? 'bg-danger/10 text-danger border-danger/20'
            : 'bg-warn/10 text-warn border-warn/20'}`}
      >
        <Icon size={13} />
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-0.5">
          <span className="text-[11px] font-mono font-semibold text-accent">{issue.code}</span>
          {issue.component_class && issue.name && (
            <span className="text-[10px] text-muted truncate">
              {issue.component_class} <span className="font-mono text-ink-700">{issue.name}</span>
            </span>
          )}
        </div>
        <div className="text-[11.5px] text-ink-700 leading-relaxed whitespace-pre-wrap">
          {issue.message}
        </div>
      </div>
      {hasTarget && (
        <button
          onClick={() => onJumpTo(COMPONENT_TYPE_MAP[issue.component_class], issue.name)}
          title={`Open ${issue.component_class} "${issue.name}" in the editor`}
          className="shrink-0 flex items-center gap-1 self-center rounded-md border border-accent/30 bg-accent/5 px-2 py-1 text-[10.5px] font-medium text-accent hover:bg-accent/10 hover:border-accent/50 transition-colors"
        >
          View <ArrowRight size={11} />
        </button>
      )}
    </div>
  )
}
