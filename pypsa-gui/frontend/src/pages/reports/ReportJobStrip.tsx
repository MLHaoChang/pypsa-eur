// The generation job's strip (WP7b): what the last `GET …/generate/status`
// record says, in one line per outcome.
//
//   running → progress `done/total`, the current section's title, elapsed
//             time, and Abort ("stopping…" once requested, until the record
//             leaves `running` — the backend stops after the current section)
//   failed  → a banner with the backend's `error`
//   done    → who wrote it (`profile_id` / `model`) and how many JSON repairs
//             the prose needed
//   aborted → the same line, marked partial
//
// Whatever the outcome, every section the model could not write
// (`prose_failures`) is listed by title with its reason: the document states
// those sections as `not_established`, and the strip says why.
//
// Shared by the panel (a whole-report `generate`) and the viewer (a
// single-section `regenerate`). Section titles come from the caller — the
// open document's headings when there is one, else the fixed catalogue.
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, Square } from 'lucide-react'
import type { ReportJobRecord } from '../../api/reports'
import { Btn } from '../../components/PageKit'
import { DEFAULT_SECTION_CHOICES } from './GenerateReportDialog'

const CATALOGUE_TITLES: Record<string, string> = Object.fromEntries(
  DEFAULT_SECTION_CHOICES.map(c => [c.id, c.title]),
)

export function sectionTitle(id: string | null | undefined, titles?: Record<string, string>): string {
  if (!id) return ''
  return titles?.[id] ?? CATALOGUE_TITLES[id] ?? id
}

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [active])
  return now
}

export function ReportJobStrip({
  record, titles, aborting, onAbort, progressPct,
}: {
  record: ReportJobRecord
  /** Section id → heading, for the current section and the failures list. */
  titles?: Record<string, string>
  aborting?: boolean
  onAbort?: () => void
  progressPct: number | null
}) {
  const running = record.status === 'running'
  const now = useNow(running)
  const end = record.finished_at != null ? record.finished_at * 1000 : now
  const elapsed = formatElapsed((end - record.started_at * 1000) / 1000)
  const { done, total, current } = record.progress ?? { done: 0, total: 0, current: null }
  const what = record.mode === 'regenerate'
    ? `Rewriting "${sectionTitle(record.section, titles)}"`
    : record.mode === 'mapping'
      ? 'Proposing the mapping plan'
      : 'Writing the report'
  const writer = [record.profile_id, record.model].filter(Boolean).join(' · ')

  return (
    <div
      className="rounded-[10px] border border-border bg-bg-2 px-4 py-2.5 text-[12px] flex flex-col gap-2"
      data-testid="report-job-strip"
      data-status={record.status}
    >
      {running && (
        <div className="flex items-center gap-3">
          <Loader2 size={13} className="animate-spin text-accent shrink-0" aria-hidden="true" />
          <span className="text-text font-medium">{what}</span>
          <span className="font-mono text-muted">{done}/{total}</span>
          {current && (
            <span className="text-muted truncate" data-testid="report-job-current">
              {sectionTitle(current, titles)}
            </span>
          )}
          <span className="font-mono text-muted ml-auto" data-testid="report-job-elapsed">{elapsed} elapsed</span>
          {onAbort && (
            <Btn
              onClick={onAbort}
              disabled={aborting}
              title="Stop after the current section; what was written so far is kept"
              data-testid="report-job-abort"
            >
              <Square size={11} /> {aborting ? 'stopping…' : 'Abort'}
            </Btn>
          )}
        </div>
      )}
      {running && (
        <div
          className="h-1.5 rounded bg-border overflow-hidden"
          role="progressbar"
          aria-label="Report generation progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPct ?? 0}
        >
          <div className="h-full bg-accent transition-[width]" style={{ width: `${progressPct ?? 0}%` }} />
        </div>
      )}

      {record.status === 'failed' && (
        <div className="flex items-start gap-2 text-danger" data-testid="report-job-failed" role="alert">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" aria-hidden="true" />
          <span>
            <span className="font-semibold">Generation failed</span>
            {record.error ? <> — {record.error}</> : null}
            <span className="text-muted"> ({elapsed})</span>
          </span>
        </div>
      )}

      {(record.status === 'done' || record.status === 'aborted') && (
        <div className="flex items-start gap-2 text-text" data-testid="report-job-done">
          <CheckCircle2 size={13} className={`shrink-0 mt-0.5 ${record.status === 'done' ? 'text-success' : 'text-warn'}`} aria-hidden="true" />
          <span>
            <span className="font-semibold">
              {record.status === 'aborted'
                ? 'Stopped'
                : record.mode === 'regenerate'
                  ? 'Section rewritten'
                  : record.mode === 'mapping' ? 'Mapping plan proposed' : 'Report written'}
            </span>
            {record.version != null && <span className="font-mono"> v{record.version}</span>}
            {record.status === 'aborted' && <span className="text-muted"> (partial — the sections not reached are stated as not established)</span>}
            {writer && <> by <span className="font-mono">{writer}</span></>}
            <span className="text-muted">
              {' · '}{done}/{total} section{total === 1 ? '' : 's'}
              {' · '}{record.repairs} repair{record.repairs === 1 ? '' : 's'}
              {' · '}{elapsed}
            </span>
          </span>
        </div>
      )}

      {record.prose_failures.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[11.5px] text-muted pl-5" aria-label="Sections the model could not write">
          {record.prose_failures.map((f, i) => (
            <li key={`${f.section_id}-${i}`} data-testid="report-job-prose-failure" className="list-disc">
              <span className="text-text">{sectionTitle(f.section_id, titles)}</span>
              {' — '}<span className="font-mono">{f.reason}</span>
              {f.repairs > 0 && <span> after {f.repairs} repair{f.repairs === 1 ? '' : 's'}</span>}
              {f.raw_head && <span className="font-mono"> · {f.raw_head.slice(0, 60)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
