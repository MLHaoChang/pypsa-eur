// The read-only report viewer (WP7a; assessment §3.4 reading level L0).
//
// Fetches one version of a `ReportDocument` and renders its sections in
// order through `SectionCard`. Export posts to the export route — which
// renders the `.docx` server-side and stores it as an `agent_export` upload
// of the Project — and then hands the upload's blob URL to an anchor with a
// `download` attribute, the same declarative pattern ChatPanel's export chip
// uses. That anchor is what the desktop shell's `ALLOW_DOWNLOADS` setting
// turns into a native save panel (`backend/desktop/downloads.py`); the
// status code is already known at that point, since the export POST itself
// answered, so the "an anchor cannot see a 404" caveat does not apply here.
//
// The version switcher appears only when more than one version exists
// (`meta.latest_version > 1`); `null` means "latest", and the query key
// carries the version so switching never shows a cached other version.
import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeft, FileDown } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  exportReport,
  getReport,
  reportErrorMessage,
  type ReportDocument,
  type ReportMeta,
} from '../../api/reports'
import { getUploadBlobUrl, type UploadMeta } from '../../api/uploads'
import { Btn, PageSection, Tag } from '../../components/PageKit'
import { SectionCard } from './SectionCard'

export const REPORT_DOC_KEY = (project: string, reportId: string, version: number | null) =>
  ['reports', 'doc', project, reportId, version] as const

const MODE_LABEL: Record<ReportDocument['mode'], string> = {
  evidence_only: 'evidence only',
  generated: 'generated',
}

/** The anchor download of an exported `.docx` — see the header comment. */
export function downloadExport(project: string, upload: UploadMeta): void {
  const a = document.createElement('a')
  a.href = getUploadBlobUrl(project, upload.file_id)
  a.download = upload.filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

export function ReportViewer({
  project, meta, onBack,
}: {
  project: string
  meta: ReportMeta
  onBack?: () => void
}) {
  const [version, setVersion] = useState<number | null>(null)

  const doc = useQuery({
    queryKey: REPORT_DOC_KEY(project, meta.report_id, version),
    queryFn: () => getReport(project, meta.report_id, version ?? undefined),
    retry: false,
  })

  const shownVersion = doc.data?.version ?? version ?? meta.latest_version

  const exportDocx = useMutation({
    mutationFn: () => exportReport(project, meta.report_id, { version: shownVersion }),
    onSuccess: (upload) => {
      downloadExport(project, upload)
      toast.success(`Exported ${upload.filename} — it is also in the Project's files`)
    },
    onError: (e) => toast.error(`Could not export the report: ${reportErrorMessage(e)}`),
  })

  const versions = Array.from({ length: meta.latest_version }, (_, i) => meta.latest_version - i)

  return (
    <div className="flex flex-col gap-4" data-testid="report-viewer">
      <PageSection
        title={doc.data?.title ?? meta.title}
        hint={
          <span className="inline-flex items-center gap-2">
            <span className="font-mono">v{shownVersion}</span>
            <Tag tone={meta.mode === 'generated' ? 'accent' : 'neutral'}>{MODE_LABEL[meta.mode] ?? meta.mode}</Tag>
            {doc.data && <span>{formatDate(doc.data.created_at)}</span>}
            {(doc.data?.profile_id ?? meta.profile_id) && (
              <span className="font-mono">{doc.data?.profile_id ?? meta.profile_id}</span>
            )}
            {(doc.data?.model ?? meta.model) && (
              <span className="font-mono">{doc.data?.model ?? meta.model}</span>
            )}
          </span>
        }
        right={
          <div className="flex items-center gap-2">
            {meta.latest_version > 1 && (
              <label className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                Version
                <select
                  aria-label="Version"
                  className="px-2 py-1 text-[11px] border border-border rounded bg-bg text-text"
                  value={String(version ?? meta.latest_version)}
                  onChange={e => {
                    const v = Number(e.target.value)
                    setVersion(v === meta.latest_version ? null : v)
                  }}
                >
                  {versions.map(v => (
                    <option key={v} value={String(v)}>v{v}{v === meta.latest_version ? ' (latest)' : ''}</option>
                  ))}
                </select>
              </label>
            )}
            <Btn
              variant="primary"
              onClick={() => exportDocx.mutate()}
              disabled={!doc.data || exportDocx.isPending}
              title="Render this version to Word and download it"
              data-testid="report-export"
            >
              <FileDown size={12} /> {exportDocx.isPending ? 'Exporting…' : 'Export .docx'}
            </Btn>
            {onBack && (
              <Btn onClick={onBack} title="Back to the list of reports" data-testid="report-back">
                <ArrowLeft size={12} /> Reports
              </Btn>
            )}
          </div>
        }
      >
        {doc.isError ? (
          <p className="text-[12px] text-danger" data-testid="report-error">
            {reportErrorMessage(doc.error, 'Could not load the report')}
          </p>
        ) : doc.data ? (
          <p className="text-[11px] text-muted">
            {doc.data.sections.length} section{doc.data.sections.length === 1 ? '' : 's'} ·{' '}
            {Object.keys(doc.data.tables).length} table{Object.keys(doc.data.tables).length === 1 ? '' : 's'} ·{' '}
            {Object.keys(doc.data.figures).length} figure{Object.keys(doc.data.figures).length === 1 ? '' : 's'}
            {' · '}evidence <span className="font-mono">{doc.data.evidence_hash.slice(0, 12)}</span>
          </p>
        ) : (
          <p className="text-[12px] text-muted">Loading…</p>
        )}
      </PageSection>

      {doc.data && doc.data.sections.map(section => (
        <SectionCard key={section.section_id} section={section} doc={doc.data} project={project} />
      ))}
    </div>
  )
}
