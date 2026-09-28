// The Reports panel (WP7a, plan §WP7; assessment §3.4 level L0).
//
// Thin like GridspinePanel: everything shown is READ from the backend's own
// list route (`GET /api/projects/{name}/reports`), and every action is one
// request — create an evidence-only report (POST, phase 1), delete behind a
// ConfirmDialog, open a report in the viewer. Refusals carry the backend's
// `error_kind` + message and surface as toasts, like the other panels.
//
// Generation (a "Generate" button with sections + language, progress and
// abort) is WP7b and lands after WP3's job exists on the backend.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePlus, FileText, RefreshCw, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  createEvidenceOnlyReport,
  deleteReport,
  listReports,
  reportErrorMessage,
  type ReportMeta,
} from '../api/reports'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Btn, PageBody, PageSection, Tag } from '../components/PageKit'
import { useUIStore } from '../store/uiStore'
import { ReportViewer } from './reports/ReportViewer'

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
            <Btn variant="primary" disabled title="No Project is open" data-testid="reports-create">
              <FilePlus size={12} /> Create evidence report
            </Btn>
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

function ReportsList({ project }: { project: string }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<ReportMeta | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ReportMeta | null>(null)

  const list = useQuery({
    queryKey: REPORTS_KEY(project),
    queryFn: () => listReports(project),
    retry: false,
  })

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
        <ReportViewer project={project} meta={selectedMeta} onBack={() => setSelected(null)} />
      </PageBody>
    )
  }

  const reports = list.data ?? []

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
              variant="primary"
              onClick={() => create.mutate()}
              disabled={create.isPending}
              title="Tables, figures and disclosures from the studies this session has run — no model prose"
              data-testid="reports-create"
            >
              <FilePlus size={12} /> {create.isPending ? 'Creating…' : 'Create evidence report'}
            </Btn>
          </div>
        }
      >
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
                    {(meta.profile_id || meta.model) && (
                      <span className="font-mono">
                        {[meta.profile_id, meta.model].filter(Boolean).join(' · ')}
                      </span>
                    )}
                  </span>
                </button>
                <span className="font-mono text-[11px] text-muted">v{meta.latest_version}</span>
                <Tag tone={meta.mode === 'generated' ? 'accent' : 'neutral'}>{MODE_LABEL[meta.mode] ?? meta.mode}</Tag>
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
