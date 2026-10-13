// The template picker (WP11, plan §WP11 "a template picker in ReportViewer").
//
// Two pieces the viewer places separately: `TemplatePicker` — the header
// control (a select over the Project's `report_template` uploads plus
// "Built-in default", and an "Upload template…" file input) — and
// `TemplateOutlineSummary`, the one-line summary of what the reader found
// in the bound template (mode, detected language, size, TOC, and the
// elements the writer does not touch).
//
// Both read the binding from the viewer's `GET …/{id}/template` query
// (`REPORT_TEMPLATE_KEY`); the picker's mutations write the bind response
// into that cache and invalidate it so the plan editor re-reads the stored
// plan. Uploading is `uploadFile(project, file, 'report_template')` — the
// same route ChatPanel uses, with the kind that stores the file as a
// template — followed by a bind of the new upload. The `.docx` / 25 MB
// gate mirrors ChatPanel's `UPLOAD_ACCEPT` / `UPLOAD_MAX_BYTES`.
import { useRef, type ChangeEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  setReportTemplate,
  templateErrorMessage,
  type ReportTemplateBinding,
  type ReportTemplateState,
} from '../../api/reports'
import { listUploads, uploadFile, type UploadMeta } from '../../api/uploads'
import { Btn, Tag } from '../../components/PageKit'
import { REPORT_TEMPLATE_KEY } from './useReportJob'

/** The Project's `report_template` uploads, newest first. */
export const REPORT_TEMPLATES_KEY = (project: string) => ['uploads', project, 'report_template'] as const

export const TEMPLATE_ACCEPT = '.docx'
export const TEMPLATE_MAX_BYTES = 25 * 1024 * 1024

function formatUploadDate(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString()
}

/** The label of a template in the select and on the export button. */
export function templateLabel(upload: UploadMeta): string {
  const date = formatUploadDate(upload.uploaded_at)
  return date ? `${upload.filename} · ${date}` : upload.filename
}

/** The Project's report templates — shared by the picker and the viewer's export label. */
export function useReportTemplates(project: string) {
  return useQuery({
    queryKey: REPORT_TEMPLATES_KEY(project),
    queryFn: () => listUploads(project, 'report_template'),
    retry: false,
  })
}

export function TemplatePicker({
  project, reportId, binding, disabled,
}: {
  project: string
  reportId: string
  /** The viewer's `GET …/template` data; `undefined` while loading. */
  binding: ReportTemplateState | null | undefined
  disabled?: boolean
}) {
  const qc = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const templates = useReportTemplates(project)

  const bind = useMutation({
    mutationFn: (fileId: string | null) => setReportTemplate(project, reportId, fileId),
    onSuccess: (resp: ReportTemplateBinding, fileId) => {
      // The bind route answers without the plan; keep a stored plan only
      // when the binding did not change, then re-read the truth.
      qc.setQueryData<ReportTemplateState>(REPORT_TEMPLATE_KEY(project, reportId), (prev) => ({
        ...resp,
        plan: prev && prev.template_file_id === resp.template_file_id ? prev.plan : null,
      }))
      void qc.invalidateQueries({ queryKey: REPORT_TEMPLATE_KEY(project, reportId) })
      const name = templates.data?.find(t => t.file_id === fileId)?.filename
      toast.success(fileId
        ? `Template bound${name ? `: ${name}` : ''} — ${resp.mode ?? 'unknown'} mode${resp.language ? `, language ${resp.language}` : ''}`
        : 'Template removed — the built-in writer is in effect')
    },
    onError: (e) => toast.error(`Could not bind the template: ${templateErrorMessage(e)}`),
  })

  const upload = useMutation({
    mutationFn: (file: File) => uploadFile(project, file, 'report_template'),
    onSuccess: async (meta) => {
      await qc.invalidateQueries({ queryKey: REPORT_TEMPLATES_KEY(project) })
      bind.mutate(meta.file_id)
    },
    onError: (e) => toast.error(`Could not upload the template: ${templateErrorMessage(e)}`),
  })

  function onFilePicked(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.docx')) {
      toast.error('A report template is a Word file (.docx).')
      return
    }
    if (file.size > TEMPLATE_MAX_BYTES) {
      toast.error(`The template is larger than 25 MB (${(file.size / 1024 / 1024).toFixed(1)} MB).`)
      return
    }
    upload.mutate(file)
  }

  const busy = bind.isPending || upload.isPending
  const current = binding?.template_file_id ?? ''

  return (
    <div className="inline-flex items-center gap-1.5" data-testid="template-picker">
      <label className="inline-flex items-center gap-1.5 text-[11px] text-muted">
        Template
        <select
          aria-label="Template"
          className="px-2 py-1 text-[11px] border border-border rounded bg-bg text-text max-w-[220px]"
          value={current}
          disabled={disabled || busy || binding === undefined}
          onChange={e => bind.mutate(e.target.value || null)}
          title={templates.isError
            ? `Could not list the Project's templates: ${templateErrorMessage(templates.error)}`
            : 'The Word file this report renders into; "Built-in default" is the bundled writer'}
        >
          <option value="">Built-in default</option>
          {(templates.data ?? []).map(t => (
            <option key={t.file_id} value={t.file_id}>{templateLabel(t)}</option>
          ))}
        </select>
      </label>
      <input
        ref={fileInput}
        type="file"
        accept={TEMPLATE_ACCEPT}
        className="hidden"
        onChange={onFilePicked}
        data-testid="template-file-input"
      />
      <Btn
        onClick={() => fileInput.current?.click()}
        disabled={disabled || busy}
        title="Upload a Word file (.docx, up to 25 MB) as a template of this Project and bind it to this report"
        data-testid="template-upload"
      >
        <Upload size={12} /> {upload.isPending ? 'Uploading…' : bind.isPending ? 'Binding…' : 'Upload template…'}
      </Btn>
    </div>
  )
}

export function TemplateOutlineSummary({ binding }: { binding: ReportTemplateState | null | undefined }) {
  const outline = binding?.outline
  if (!binding || !outline || !binding.template_file_id) return null
  const mode = binding.mode ?? outline.mode
  return (
    <div className="flex flex-col gap-1 text-[11px] text-muted" data-testid="template-outline">
      <p className="inline-flex flex-wrap items-center gap-2">
        <span data-testid="template-mode">
          <Tag tone={mode === 'tagged' ? 'accent' : 'purple'}>{mode}</Tag>
        </span>
        <span>language <span className="font-mono text-text">{binding.language ?? outline.language ?? 'unknown'}</span></span>
        <span>{outline.n_paragraphs} paragraphs</span>
        <span>{outline.headings.length} heading{outline.headings.length === 1 ? '' : 's'}</span>
        <span>TOC {outline.has_toc ? 'yes' : 'no'}</span>
        {outline.tags.length > 0 && <span>{outline.tags.length} tag{outline.tags.length === 1 ? '' : 's'}</span>}
        {outline.placeholders.length > 0 && (
          <span>{outline.placeholders.length} placeholder{outline.placeholders.length === 1 ? '' : 's'}</span>
        )}
        {outline.tables.length > 0 && <span>{outline.tables.length} table{outline.tables.length === 1 ? '' : 's'}</span>}
      </p>
      {outline.unsupported.length > 0 && (
        <p className="inline-flex items-start gap-1.5 text-warn" data-testid="template-unsupported">
          <AlertTriangle size={11} className="shrink-0 mt-0.5" aria-hidden="true" />
          <span>
            Template elements the writer cannot fill — {outline.unsupported.join(', ')} — are kept where
            the writer does not touch them.
          </span>
        </p>
      )}
    </div>
  )
}
