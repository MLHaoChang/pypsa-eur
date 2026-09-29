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
//
// WP7b — regenerate: each SectionCard offers "Regenerate…"; the viewer posts
// the instruction through `useReportJob().regenerate`, shows the job strip
// while the section is rewritten, and on the poll where the job finishes the
// hook invalidates this document's queries and the viewer switches to the
// latest version (`version = null`) — the bumped `latest_version` arrives
// through the panel's refreshed list meta.
//
// WP7b — "evidence changed since v{N}": the panel passes the evidence hash of
// the NEWEST evidence-only report as `currentEvidenceHash`, and the header
// says so when this document was written from different evidence. There is
// no `GET …/evidence_hash` route yet (the plan names one); comparing against
// the newest evidence-only document is the phase-3 approximation and a
// dedicated route that hashes the session's live evidence is a follow-up.
//
// WP11 — templates: the header holds `TemplatePicker` (bind one of the
// Project's `report_template` uploads, or the built-in default) and the
// body the outline summary; `GET …/{id}/template` is one query here
// (`REPORT_TEMPLATE_KEY`) that the picker and the plan editor share. An
// untagged template gets the `MappingPlanEditor` below the header; the
// export button names the template in effect. After an export the upload
// meta is kept so the "Preview of the exported file" panel can render it
// with docx-preview (`DocxPreview`, collapsed until asked) or download it
// again. A `mapping` job's strip is the editor's; the viewer's `onFinished`
// only toasts it.
//
// WP14 — the round trip: `RoundTripPanel` (below the header) uploads an
// edited export and merges it as the next version WITHOUT a job, so the
// viewer itself does what the hook does on a finished job — switch to the
// latest version and invalidate the document, the list and the template
// binding (the upload may have become the template). "Regenerate with
// this" (on the panel's comments and on a section's pending-instruction
// chip) posts a regenerate with an EMPTY body: the backend then uses the
// section's `pending_instruction` and clears it. "Compare…" next to the
// version switcher opens `VersionDiffView`. The PDF button exists only when
// `GET …/reports/capabilities` says `pdf: true` (LibreOffice on the
// server); a PDF export downloads but is not previewed (docx-preview reads
// Word files only).
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowLeft, ChevronDown, ChevronRight, Download, Eye, FileDown, FileText, GitCompareArrows,
} from 'lucide-react'
import toast from 'react-hot-toast'
import {
  exportReport,
  getReport,
  getReportCapabilities,
  getReportTemplate,
  reportErrorMessage,
  reportJobErrorMessage,
  roundTripErrorMessage,
  templateErrorMessage,
  type ReportDocument,
  type ReportMeta,
  type RoundTripResponse,
} from '../../api/reports'
import { getUploadBlobUrl, type UploadMeta } from '../../api/uploads'
import { Btn, PageSection, Tag } from '../../components/PageKit'
import { DocxPreview } from './DocxPreview'
import { MappingPlanEditor } from './MappingPlanEditor'
import { ReportJobStrip } from './ReportJobStrip'
import { RoundTripPanel } from './RoundTripPanel'
import { SectionCard } from './SectionCard'
import { TemplateOutlineSummary, TemplatePicker, useReportTemplates } from './TemplatePicker'
import { VersionDiffView } from './VersionDiffView'
import { REPORT_DOC_PREFIX, REPORT_TEMPLATE_KEY, REPORTS_LIST_KEY, useReportJob } from './useReportJob'

/** `GET …/reports/capabilities` — one query per Project (WP14). */
export const REPORT_CAPABILITIES_KEY = (project: string) => ['reports', 'capabilities', project] as const
export const PDF_UNAVAILABLE_HINT = 'PDF export needs LibreOffice on the server'

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
  project, meta, onBack, currentEvidenceHash,
}: {
  project: string
  meta: ReportMeta
  onBack?: () => void
  /** The newest evidence-only report's `evidence_hash` (see the header comment). */
  currentEvidenceHash?: string | null
}) {
  const [version, setVersion] = useState<number | null>(null)
  // A finished job whose outcome should stay visible here (a failure, or
  // sections the model could not write); a clean `done` is a toast.
  const [outcomeVisible, setOutcomeVisible] = useState(false)
  // WP11: the last export of this visit, for the preview panel.
  const [lastExport, setLastExport] = useState<UploadMeta | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  // WP14: the version comparison, opened from the version switcher.
  const [diffOpen, setDiffOpen] = useState(false)
  const qc = useQueryClient()

  const job = useReportJob(project, {
    onFinished: (record) => {
      if (record.report_id !== meta.report_id) return
      if (record.mode === 'mapping') {
        // The editor shows the strip and the hook re-reads the plan.
        if (record.status === 'done') toast.success('Mapping plan proposed — review it below')
        else if (record.status === 'aborted') toast('Mapping proposal stopped')
        else toast.error(`Mapping proposal failed: ${record.error ?? 'unknown error'}`)
        return
      }
      setVersion(null)
      const keep = record.status !== 'done' || record.prose_failures.length > 0
      setOutcomeVisible(keep)
      if (record.status === 'done') {
        toast.success(record.mode === 'regenerate'
          ? `Section rewritten — now v${record.version ?? ''}`
          : `Report written — v${record.version ?? ''}`)
      } else if (record.status === 'aborted') {
        toast(`Generation stopped — v${record.version ?? ''} keeps what was written`)
      } else {
        toast.error(`Generation failed: ${record.error ?? 'unknown error'}`)
      }
    },
  })

  const doc = useQuery({
    queryKey: REPORT_DOC_KEY(project, meta.report_id, version),
    queryFn: () => getReport(project, meta.report_id, version ?? undefined),
    retry: false,
  })

  const shownVersion = doc.data?.version ?? version ?? meta.latest_version

  const sectionTitles = doc.data
    ? Object.fromEntries(doc.data.sections.map(s => [s.section_id, s.heading]))
    : undefined
  const sectionChoices = useMemo(
    () => doc.data?.sections.map(s => ({ id: s.section_id, title: s.heading })) ?? [],
    [doc.data],
  )

  // WP11: the template binding (+ stored plan) and the Project's templates.
  const template = useQuery({
    queryKey: REPORT_TEMPLATE_KEY(project, meta.report_id),
    queryFn: () => getReportTemplate(project, meta.report_id),
    retry: false,
  })
  const templates = useReportTemplates(project)
  const boundTemplate = template.data?.template_file_id
    ? templates.data?.find(t => t.file_id === template.data?.template_file_id) ?? null
    : null
  const templateLabel = template.data?.template_file_id
    ? (boundTemplate?.filename ?? template.data.template_file_id)
    : 'built-in default'

  // An empty instruction sends `{}`: the backend then uses the section's
  // `pending_instruction` when it has one (WP14), else rewrites as before.
  async function regenerate(sectionId: string, instruction: string) {
    try {
      setOutcomeVisible(false)
      const text = instruction.trim()
      await job.regenerate(meta.report_id, sectionId, text ? { instruction: text } : {})
    } catch (e) {
      toast.error(`Could not regenerate the section: ${reportJobErrorMessage(e)}`)
    }
  }

  // WP14: the merged version arrived without a job — do what the hook does
  // on a finished one.
  function onMerged(resp: RoundTripResponse) {
    setVersion(null)
    setOutcomeVisible(false)
    void qc.invalidateQueries({ queryKey: REPORT_DOC_PREFIX(project, resp.report_id) })
    void qc.invalidateQueries({ queryKey: REPORTS_LIST_KEY(project) })
    void qc.invalidateQueries({ queryKey: REPORT_TEMPLATE_KEY(project, resp.report_id) })
  }

  // WP14: what the server can convert to.
  const capabilities = useQuery({
    queryKey: REPORT_CAPABILITIES_KEY(project),
    queryFn: () => getReportCapabilities(project),
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
  const pdfAvailable = capabilities.data?.pdf === true

  const evidenceChanged =
    doc.data != null
    && currentEvidenceHash != null
    && currentEvidenceHash !== ''
    && doc.data.evidence_hash !== currentEvidenceHash

  const exportDocx = useMutation({
    mutationFn: () => exportReport(project, meta.report_id, { version: shownVersion }),
    onSuccess: (upload) => {
      downloadExport(project, upload)
      setLastExport(upload)
      toast.success(`Exported ${upload.filename} — it is also in the Project's files`)
    },
    onError: (e) => toast.error(`Could not export the report: ${templateErrorMessage(e)}`),
  })

  const exportPdf = useMutation({
    mutationFn: () => exportReport(project, meta.report_id, { version: shownVersion, format: 'pdf' }),
    onSuccess: (upload) => {
      downloadExport(project, upload)
      toast.success(`Exported ${upload.filename} — it is also in the Project's files`)
    },
    onError: (e) => toast.error(`Could not export the PDF: ${roundTripErrorMessage(e)}`),
  })

  const exporting = exportDocx.isPending || exportPdf.isPending
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
            {evidenceChanged && (
              <span
                className="inline-flex items-center gap-1 text-warn"
                data-testid="evidence-changed"
                title="The session's evidence (the newest evidence-only report) has a different hash than the evidence this version was written from"
              >
                <AlertTriangle size={11} aria-hidden="true" /> evidence changed since v{shownVersion}
              </span>
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
            {meta.latest_version > 1 && (
              <Btn
                onClick={() => setDiffOpen(o => !o)}
                title="Compare two versions section by section"
                data-testid="report-compare"
                aria-expanded={diffOpen}
              >
                <GitCompareArrows size={12} /> Compare…
              </Btn>
            )}
            <TemplatePicker
              project={project}
              reportId={meta.report_id}
              binding={template.data}
              disabled={exporting}
            />
            <Btn
              variant="primary"
              onClick={() => exportDocx.mutate()}
              disabled={!doc.data || exporting}
              title={`Render this version to Word (${templateLabel}) and download it${
                capabilities.data && !pdfAvailable ? ` · ${PDF_UNAVAILABLE_HINT}` : ''}`}
              data-testid="report-export"
            >
              <FileDown size={12} /> {exportDocx.isPending ? 'Exporting…' : 'Export .docx'}
              <span className="font-normal opacity-80" data-testid="report-export-template">· {templateLabel}</span>
            </Btn>
            {pdfAvailable && (
              <Btn
                onClick={() => exportPdf.mutate()}
                disabled={!doc.data || exporting}
                title={`Render this version to Word (${templateLabel}), convert it to PDF with LibreOffice and download it`}
                data-testid="report-export-pdf"
              >
                <FileText size={12} /> {exportPdf.isPending ? 'Converting…' : 'Export PDF'}
              </Btn>
            )}
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
          <div className="flex flex-col gap-1.5">
            <p className="text-[11px] text-muted">
              {doc.data.sections.length} section{doc.data.sections.length === 1 ? '' : 's'} ·{' '}
              {Object.keys(doc.data.tables).length} table{Object.keys(doc.data.tables).length === 1 ? '' : 's'} ·{' '}
              {Object.keys(doc.data.figures).length} figure{Object.keys(doc.data.figures).length === 1 ? '' : 's'}
              {' · '}evidence <span className="font-mono">{doc.data.evidence_hash.slice(0, 12)}</span>
            </p>
            <TemplateOutlineSummary binding={template.data} />
          </div>
        ) : (
          <p className="text-[12px] text-muted">Loading…</p>
        )}
      </PageSection>

      {diffOpen && meta.latest_version > 1 && (
        <VersionDiffView
          project={project}
          reportId={meta.report_id}
          latestVersion={meta.latest_version}
          onClose={() => setDiffOpen(false)}
        />
      )}

      {template.data?.mode === 'untagged' && template.data.outline && (
        <MappingPlanEditor
          project={project}
          reportId={meta.report_id}
          outline={template.data.outline}
          plan={template.data.plan}
          language={template.data.language}
          sections={sectionChoices}
          job={job}
          onExport={() => exportDocx.mutate()}
          exportPending={exportDocx.isPending}
        />
      )}

      <RoundTripPanel
        project={project}
        reportId={meta.report_id}
        disabled={exporting || job.isRunning || job.isStarting}
        onMerged={onMerged}
        onRegenerateWith={(sectionId) => regenerate(sectionId, '')}
        regenerateDisabled={job.isRunning || job.isStarting}
      />

      {lastExport && (
        <div data-testid="export-preview-panel">
        <PageSection
          title="Preview of the exported file"
          hint={<span className="font-mono">{lastExport.filename}</span>}
          right={
            <div className="flex items-center gap-2">
              <Btn
                onClick={() => setPreviewOpen(o => !o)}
                title={previewOpen ? 'Hide the preview' : 'Render the exported file here (docx-preview; the download is the authority)'}
                data-testid="export-preview-toggle"
                aria-expanded={previewOpen}
              >
                {previewOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <Eye size={12} /> Preview
              </Btn>
              <Btn
                onClick={() => downloadExport(project, lastExport)}
                title="Download the exported file again"
                data-testid="export-download"
              >
                <Download size={12} /> Download
              </Btn>
            </div>
          }
          bodyClassName={previewOpen ? 'p-4' : 'px-4 py-2'}
        >
          {previewOpen ? (
            <DocxPreview url={getUploadBlobUrl(project, lastExport.file_id)} filename={lastExport.filename} />
          ) : (
            <p className="text-[11px] text-muted">
              Exported with {templateLabel}. Preview renders the file in the browser; the download is the authority.
            </p>
          )}
        </PageSection>
        </div>
      )}

      {job.record && job.record.mode !== 'mapping'
        && (job.isRunning || (outcomeVisible && job.record.report_id === meta.report_id)) && (
        <ReportJobStrip
          record={job.record}
          titles={sectionTitles}
          progressPct={job.progressPct}
          aborting={job.aborting}
          onAbort={() => { job.abort().catch(e => toast.error(reportJobErrorMessage(e))) }}
        />
      )}

      {doc.data && doc.data.sections.map(section => (
        <SectionCard
          key={section.section_id}
          section={section}
          doc={doc.data}
          project={project}
          onRegenerate={regenerate}
          regenerateDisabled={job.isRunning || job.isStarting}
        />
      ))}
    </div>
  )
}
