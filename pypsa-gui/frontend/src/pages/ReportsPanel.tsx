// The Reports panel (WP7a, plan §WP7; assessment §3.4 level L0).
//
// Thin like GridspinePanel: everything shown is READ from the backend's own
// list route (`GET /api/projects/{name}/reports`), and every action is one
// request — create an evidence-only report (POST, phase 1), delete behind a
// ConfirmDialog, open a report in the viewer. Refusals carry the backend's
// `error_kind` + message and surface as toasts, like the other panels.
//
// Generation (WP7b): "Generate report…" opens `GenerateReportDialog`, whose
// submit is `useReportJob().start`; the hook polls `GET …/generate/status`
// every 1.5 s while the job runs and the strip (`ReportJobStrip`) shows
// progress, Abort, the failure or the done line with who wrote it and which
// sections the model could not write. When the job leaves `running` the hook
// invalidates this list, so the new report (or version) appears without a
// reload; a whole-report job that finishes while nothing is open opens its
// report. "Generation pre-armed" (`uiStore.reportGenerateRequest`, set by the
// Adequacy tab's Reports button) opens the dialog on arrival.
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePlus, FileText, RefreshCw, Sparkles, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  createEvidenceOnlyReport,
  deleteReport,
  getReport,
  listReports,
  reportErrorMessage,
  reportJobErrorMessage,
  type ReportMeta,
} from '../api/reports'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Btn, PageBody, PageSection, Tag } from '../components/PageKit'
import { useUIStore } from '../store/uiStore'
import { GenerateReportDialog } from './reports/GenerateReportDialog'
import { ReportJobStrip } from './reports/ReportJobStrip'
import { REPORT_DOC_KEY, ReportViewer } from './reports/ReportViewer'
import { useReportJob } from './reports/useReportJob'

export const REPORTS_KEY = (project: string) => ['reports', 'list', project] as const

const MODE_LABEL: Record<ReportMeta['mode'], string> = {
  evidence_only: 'evidence only',
  generated: 'generated',
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export default function ReportsPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  if (!currentProject) {
    return (
      <PageBody>
        <PageSection
          title="Reports"
          right={
            <div className="flex items-center gap-2">
              <Btn disabled title="No Project is open" data-testid="reports-create">
                <FilePlus size={12} /> Create evidence report
              </Btn>
              <Btn variant="primary" disabled title="No Project is open" data-testid="reports-generate">
                <Sparkles size={12} /> Generate report…
              </Btn>
            </div>
          }
        >
          <p className="text-[12px] text-muted">
            Open a Project to list its reports. A report is written from the studies
            the Project's session has run — the Energy Hub reference design and the
            adequacy study — and exports to Word.
          </p>
        </PageSection>
      </PageBody>
    )
  }
  return <ReportsList project={currentProject} />
}

/** The newest evidence-only report: the skeleton a generated report is
 *  written into, and the closest thing to "the session's current evidence"
 *  until a dedicated evidence-hash route exists (see ReportViewer). */
export function newestEvidenceOnly(reports: ReportMeta[] | undefined): ReportMeta | null {
  if (!reports) return null
  return reports
    .filter(m => m.mode === 'evidence_only')
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))[0] ?? null
}

function ReportsList({ project }: { project: string }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<ReportMeta | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ReportMeta | null>(null)
  const [genOpen, setGenOpen] = useState(false)
  const reportGenerateRequest = useUIStore(s => s.reportGenerateRequest)
  const clearReportGenerateRequest = useUIStore(s => s.clearReportGenerateRequest)

  // "Generation pre-armed": consumed once, then cleared (same shape as the
  // settings deep link — the panel remounts on every switch).
  useEffect(() => {
    if (!reportGenerateRequest) return
    setGenOpen(true)
    clearReportGenerateRequest()
  }, [reportGenerateRequest, clearReportGenerateRequest])

  const list = useQuery({
    queryKey: REPORTS_KEY(project),
    queryFn: () => listReports(project),
    retry: false,
  })

  const job = useReportJob(project, {
    onFinished: (record) => {
      if (record.mode !== 'generate') return
      if (record.status === 'done') {
        toast.success(`Report written — v${record.version ?? 1}`)
        // Open the new report when nothing else is open; the refreshed list
        // replaces this placeholder meta (see `selectedMeta`).
        setSelected(prev => prev ?? {
          report_id: record.report_id, title: 'Report', created_at: '', updated_at: '',
          latest_version: record.version ?? 1, mode: 'generated', evidence_hash: '',
          profile_id: record.profile_id, model: record.model,
        })
      } else if (record.status === 'aborted') {
        toast(`Generation stopped — v${record.version ?? 1} keeps what was written`)
      } else {
        toast.error(`Generation failed: ${record.error ?? 'unknown error'}`)
      }
    },
  })

  const evidenceMeta = newestEvidenceOnly(list.data)
  // The section choices for the dialog: the newest evidence-only document's
  // sections. Fetched lazily — only while the dialog is open.
  const evidenceDoc = useQuery({
    queryKey: REPORT_DOC_KEY(project, evidenceMeta?.report_id ?? '', null),
    queryFn: () => getReport(project, evidenceMeta!.report_id),
    enabled: genOpen && evidenceMeta != null,
    retry: false,
  })
  const sectionChoices = useMemo(
    () => evidenceDoc.data?.sections.map(s => ({ id: s.section_id, title: s.heading })) ?? null,
    [evidenceDoc.data],
  )
  const sectionTitles = useMemo(
    () => (sectionChoices ? Object.fromEntries(sectionChoices.map(c => [c.id, c.title])) : undefined),
    [sectionChoices],
  )

  const create = useMutation({
    mutationFn: () => createEvidenceOnlyReport(project),
    onSuccess: (created) => {
      const { document: _doc, ...meta } = created
      qc.invalidateQueries({ queryKey: REPORTS_KEY(project) })
      toast.success(`Report created: ${meta.title}`)
      setSelected(meta)
    },
    onError: (e) => toast.error(`Could not create the report: ${reportErrorMessage(e)}`),
  })

  const remove = useMutation({
    mutationFn: (meta: ReportMeta) => deleteReport(project, meta.report_id),
    onSuccess: (_out, meta) => {
      qc.invalidateQueries({ queryKey: REPORTS_KEY(project) })
      setPendingDelete(null)
      if (selected?.report_id === meta.report_id) setSelected(null)
      toast.success(`Deleted ${meta.title}`)
    },
    onError: (e) => {
      setPendingDelete(null)
      toast.error(`Could not delete the report: ${reportErrorMessage(e)}`)
    },
  })

  // When the list refreshes (a new version, a rename), keep the open viewer
  // on the fresh meta so its version switcher sees `latest_version`.
  const selectedMeta = selected
    ? (list.data?.find(m => m.report_id === selected.report_id) ?? selected)
    : null

  if (selectedMeta) {
    return (
      <PageBody>
        <ReportViewer
          project={project}
          meta={selectedMeta}
          onBack={() => setSelected(null)}
          currentEvidenceHash={evidenceMeta?.evidence_hash ?? null}
        />
      </PageBody>
    )
  }

  const reports = list.data ?? []
  const generateBlocked = job.isRunning
    ? 'A report is already being written — wait for it or abort it'
    : job.isStarting ? 'Starting…' : null

  return (
    <PageBody>
      <PageSection
        title={<span className="inline-flex items-center gap-2"><FileText size={13} /> Reports</span>}
        count={list.data ? reports.length : undefined}
        hint={`Study reports of ${project}`}
        right={
          <div className="flex items-center gap-2">
            <Btn onClick={() => list.refetch()} title="Refresh" aria-label="Refresh reports">
              <RefreshCw size={12} />
            </Btn>
            <Btn
              onClick={() => create.mutate()}
              disabled={create.isPending}
              title="Tables, figures and disclosures from the studies this session has run — no model prose"
              data-testid="reports-create"
            >
              <FilePlus size={12} /> {create.isPending ? 'Creating…' : 'Create evidence report'}
            </Btn>
            <Btn
              variant="primary"
              onClick={() => setGenOpen(true)}
              disabled={generateBlocked != null}
              title={generateBlocked ?? 'Write a report with the active LLM profile from the studies this session has run'}
              data-testid="reports-generate"
            >
              <Sparkles size={12} /> Generate report…
            </Btn>
          </div>
        }
      >
        {job.record && (
          <div className="mb-3">
            <ReportJobStrip
              record={job.record}
              titles={sectionTitles}
              progressPct={job.progressPct}
              aborting={job.aborting}
              onAbort={() => { job.abort().catch(e => toast.error(reportJobErrorMessage(e))) }}
            />
          </div>
        )}
        {list.isError ? (
          <p className="text-[12px] text-danger" data-testid="reports-error">
            {reportErrorMessage(list.error, 'Could not list the reports')}
          </p>
        ) : list.isPending ? (
          <p className="text-[12px] text-muted">Loading…</p>
        ) : reports.length === 0 ? (
          <p className="text-[12px] text-muted" data-testid="reports-empty">
            No reports yet. Create an evidence report from the studies this Project's
            session has run; sections the studies did not establish are stated as such.
          </p>
        ) : (
          <ul className="flex flex-col divide-y divide-border" aria-label="Reports">
            {reports.map(meta => (
              <li
                key={meta.report_id}
                className="flex items-center gap-3 py-2 text-[12px]"
                data-testid="report-row"
              >
                <button
                  type="button"
                  className="flex-1 min-w-0 text-left hover:text-accent"
                  onClick={() => setSelected(meta)}
                  title="Open this report"
                  data-testid="report-open"
                >
                  <span className="font-medium text-text block truncate">{meta.title}</span>
                  <span className="text-[10.5px] text-muted inline-flex items-center gap-2 mt-0.5">
                    <span>{formatDate(meta.created_at)}</span>
                    {meta.profile_id && <span className="font-mono">{meta.profile_id}</span>}
                  </span>
                </button>
                <span className="font-mono text-[11px] text-muted">v{meta.latest_version}</span>
                <Tag tone={meta.mode === 'generated' ? 'accent' : 'neutral'}>{MODE_LABEL[meta.mode] ?? meta.mode}</Tag>
                {meta.model && (
                  <span
                    className="inline-flex items-center px-1.5 py-0.5 rounded border border-border bg-panel text-[10px] text-muted font-mono whitespace-nowrap"
                    data-testid="report-written-by"
                    title={meta.profile_id ? `Profile ${meta.profile_id}` : undefined}
                  >
                    written by {meta.model}
                  </span>
                )}
                <Btn
                  onClick={() => setPendingDelete(meta)}
                  disabled={remove.isPending}
                  title="Delete this report and every version of it"
                  aria-label={`Delete ${meta.title}`}
                  data-testid="report-delete"
                >
                  <Trash2 size={12} />
                </Btn>
              </li>
            ))}
          </ul>
        )}
      </PageSection>

      <GenerateReportDialog
        open={genOpen}
        onClose={() => setGenOpen(false)}
        onGenerate={(opts) => job.start(opts)}
        sectionChoices={sectionChoices}
      />

      <ConfirmDialog
        open={pendingDelete != null}
        title="Delete report?"
        message={
          pendingDelete
            ? <>Delete <b>{pendingDelete.title}</b> and its {pendingDelete.latest_version} version{pendingDelete.latest_version === 1 ? '' : 's'}? Exported Word files stay in the Project's files.</>
            : null
        }
        confirmLabel="Delete report"
        danger
        pending={remove.isPending}
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete)}
        onCancel={() => setPendingDelete(null)}
      />
    </PageBody>
  )
}
