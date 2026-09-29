// The generation job, as the frontend sees it (WP7b, plan §WP7).
//
// One hook watches WP3's job for a Project: `GET …/reports/generate/status`
// is polled every 1.5 s while the record says `running` and not otherwise —
// the same `refetchInterval` shape GridspinePanel uses for its study — and
// `start` / `abort` / `regenerate` are mutations over the job routes that
// re-read the status on success so polling begins on the next tick.
//
// The transition is what matters, not the state: on the poll where the record
// LEAVES `running` (→ `done` / `failed` / `aborted`) the reports list and the
// finished report's document queries are invalidated once, so the panel and
// the viewer refresh without a reload (plan acceptance "Generate → running →
// done transitions drive the list and the viewer"). A record that is already
// terminal at first read (the last job of an earlier visit) does not
// invalidate anything — nothing changed since the caches were filled.
//
// The query is keyed by project only, so the panel and the viewer share one
// poll; `onFinished` lets a caller react to the transition (the viewer
// switches to the new latest version).
import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  abortGenerate,
  generateReport,
  getGenerateStatus,
  proposeMappingPlan,
  regenerateSection,
  type GenerateReportOptions,
  type GenerateReportResponse,
  type ProposeMappingOptions,
  type ProposeMappingResponse,
  type RegenerateSectionOptions,
  type RegenerateSectionResponse,
  type ReportJobRecord,
} from '../../api/reports'

export const REPORT_JOB_KEY = (project: string) => ['reports', 'job', project] as const
/** Prefix of every version of one report's document query (`REPORT_DOC_KEY`);
 *  exported for the viewer's round trip (WP14), which writes a version without a job. */
export const REPORT_DOC_PREFIX = (project: string, reportId: string) => ['reports', 'doc', project, reportId] as const
export const REPORTS_LIST_KEY = (project: string) => ['reports', 'list', project] as const
/** One report's template binding + stored plan (`GET …/{id}/template`, WP11). */
export const REPORT_TEMPLATE_KEY = (project: string, reportId: string) =>
  ['reports', 'template', project, reportId] as const

export const REPORT_JOB_POLL_MS = 1500

export interface UseReportJobOptions {
  /** Called once per job, on the poll where the record leaves `running`. */
  onFinished?: (record: ReportJobRecord) => void
}

export interface ReportJobApi {
  record: ReportJobRecord | null
  isRunning: boolean
  /** `done / total` in percent (0–100), or `null` without a record or total. */
  progressPct: number | null
  /** An abort was requested and the record has not left `running` yet. */
  aborting: boolean
  isStarting: boolean
  start: (opts?: GenerateReportOptions) => Promise<GenerateReportResponse>
  abort: () => Promise<void>
  regenerate: (
    reportId: string,
    sectionId: string,
    opts?: RegenerateSectionOptions,
  ) => Promise<RegenerateSectionResponse>
  /** WP11: ask the model for a mapping plan (a `mode: "mapping"` job). */
  proposeMapping: (reportId: string, opts?: ProposeMappingOptions) => Promise<ProposeMappingResponse>
  statusError: unknown
}

export function progressPercent(record: ReportJobRecord | null): number | null {
  if (!record) return null
  const { done, total } = record.progress ?? { done: 0, total: 0 }
  if (!total || total <= 0) return record.status === 'done' ? 100 : null
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)))
}

export function useReportJob(project: string | null, options: UseReportJobOptions = {}): ReportJobApi {
  const qc = useQueryClient()
  const [aborting, setAborting] = useState(false)

  const status = useQuery({
    queryKey: REPORT_JOB_KEY(project ?? ''),
    queryFn: () => getGenerateStatus(project as string),
    enabled: project != null,
    retry: false,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? REPORT_JOB_POLL_MS : false),
  })

  const record = status.data ?? null
  const isRunning = record?.status === 'running'

  // The transition effect. `prevStatus` starts undefined so a record that is
  // terminal at first read is a baseline, not a transition.
  const prevStatus = useRef<ReportJobRecord['status'] | null | undefined>(undefined)
  const onFinishedRef = useRef(options.onFinished)
  onFinishedRef.current = options.onFinished
  useEffect(() => {
    const now = record?.status ?? null
    const before = prevStatus.current
    prevStatus.current = now
    if (before !== 'running' || now === 'running' || !record || !project) return
    setAborting(false)
    qc.invalidateQueries({ queryKey: REPORTS_LIST_KEY(project) })
    qc.invalidateQueries({ queryKey: REPORT_DOC_PREFIX(project, record.report_id) })
    // A mapping job writes the stored plan, not a document version.
    if (record.mode === 'mapping') {
      qc.invalidateQueries({ queryKey: REPORT_TEMPLATE_KEY(project, record.report_id) })
    }
    onFinishedRef.current?.(record)
  }, [record, project, qc])

  const refreshStatus = useCallback(async () => {
    if (!project) return
    await qc.invalidateQueries({ queryKey: REPORT_JOB_KEY(project) })
  }, [project, qc])

  const startMutation = useMutation({
    mutationFn: (opts: GenerateReportOptions = {}) => generateReport(project as string, opts),
    onSuccess: () => { setAborting(false); void refreshStatus() },
  })

  const abortMutation = useMutation({
    mutationFn: () => abortGenerate(project as string),
    onSuccess: (out) => { if (out.aborting) setAborting(true); void refreshStatus() },
  })

  const regenerateMutation = useMutation({
    mutationFn: (args: { reportId: string; sectionId: string; opts?: RegenerateSectionOptions }) =>
      regenerateSection(project as string, args.reportId, args.sectionId, args.opts),
    onSuccess: () => { setAborting(false); void refreshStatus() },
  })

  const proposeMutation = useMutation({
    mutationFn: (args: { reportId: string; opts?: ProposeMappingOptions }) =>
      proposeMappingPlan(project as string, args.reportId, args.opts),
    onSuccess: () => { setAborting(false); void refreshStatus() },
  })

  const start = useCallback(
    (opts: GenerateReportOptions = {}) => startMutation.mutateAsync(opts),
    [startMutation],
  )
  const abort = useCallback(async () => { await abortMutation.mutateAsync() }, [abortMutation])
  const regenerate = useCallback(
    (reportId: string, sectionId: string, opts?: RegenerateSectionOptions) =>
      regenerateMutation.mutateAsync({ reportId, sectionId, opts }),
    [regenerateMutation],
  )
  const proposeMapping = useCallback(
    (reportId: string, opts?: ProposeMappingOptions) => proposeMutation.mutateAsync({ reportId, opts }),
    [proposeMutation],
  )

  return {
    record,
    isRunning,
    progressPct: progressPercent(record),
    aborting: aborting && isRunning,
    isStarting: startMutation.isPending || regenerateMutation.isPending || proposeMutation.isPending,
    start,
    abort,
    regenerate,
    proposeMapping,
    statusError: status.error,
  }
}
