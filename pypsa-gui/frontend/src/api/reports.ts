/**
 * Study reports API (WP7a) — `/api/projects/{name}/reports…`
 * (backend `routers/reports.py`, models in `models/report.py`).
 *
 * The types below mirror the backend's `ReportDocument` / `Section` / block
 * union / `Table` / `Figure` / `ReportMeta` one to one: the viewer renders
 * what the store holds and derives nothing of its own — tables and figures
 * are rendered by backend code from the evidence, prose blocks only
 * REFERENCE them (`table_ref` / `figure_ref`).
 *
 * Raw `fetch`, like `uploads.ts`, and for the same reason: the export result
 * is an upload whose blob URL feeds an anchor download, and the figure route
 * feeds an `<img src>`. Raw fetch bypasses the axios CSRF interceptor, so the
 * three mutations add the header themselves through `rawFetchHeaders`.
 *
 * Phase-1 routes (WP7a) plus WP3's generation job (WP7b, backend
 * `routers/report_jobs.py`): `generateReport`, `getGenerateStatus` (204 →
 * `null`, polled by `pages/reports/useReportJob.ts`), `abortGenerate` and
 * `regenerateSection`.
 *
 * Increment 3 (WP14, backend `routers/reports.py` per the plan's pinned
 * route contract): the round trip (`uploadRoundTrip` = a `report_roundtrip`
 * upload followed by `POST …/{id}/roundtrip`), the version diff, the
 * capabilities probe and `exportReport`'s `format` ("pdf" only when the
 * capability is there).
 */
import { rawFetchHeaders } from './csrf'
import { uploadFile, type UploadMeta } from './uploads'

// ── blocks ──────────────────────────────────────────────────────────────────

export interface ParagraphBlock { type: 'paragraph'; md: string }
export interface BulletsBlock { type: 'bullets'; items: string[] }
export interface TableRefBlock { type: 'table_ref'; table_id: string; caption?: string | null }
export interface FigureRefBlock { type: 'figure_ref'; figure_id: string; caption?: string | null }
export type CalloutKind = 'disclosure' | 'gap' | 'not_established'
export interface CalloutBlock { type: 'callout'; kind: CalloutKind; text: string }
export interface FieldBlock { type: 'field'; key: string; value: string }

export type Block =
  | ParagraphBlock
  | BulletsBlock
  | TableRefBlock
  | FigureRefBlock
  | CalloutBlock
  | FieldBlock

// ── sections ────────────────────────────────────────────────────────────────

export interface VerifiedNumber { text: string; path: string }

/** The number audit's verdict (WP3): a check, not a rewrite. */
export interface SectionAudit {
  unverified: string[]
  verified: VerifiedNumber[]
}

export type SectionSource = 'llm' | 'user_edit' | 'code'
/** The completeness enum of the EH spec §4: a section that was not
 *  established is STATED with its note, never omitted. */
export type SectionStatus = 'ok' | 'not_established' | 'skipped'

export interface Section {
  section_id: string
  heading: string
  source: SectionSource
  status: SectionStatus
  blocks: Block[]
  note?: string | null
  audit: SectionAudit
  /** WP14: a Word comment the round trip attached; the next regenerate
   *  without an instruction of its own uses it and clears it. */
  pending_instruction?: string | null
  /** WP14: the comments the round trip found on this section. */
  comments?: string[]
}

// ── tables and figures ──────────────────────────────────────────────────────

export interface Table {
  table_id: string
  columns: string[]
  rows: string[][]
  caption?: string | null
  /** JSON pointer into the evidence the rows came from. */
  source_path?: string | null
}

export interface Figure {
  figure_id: string
  png_file: string
  caption?: string | null
  source_path?: string | null
}

// ── the document ────────────────────────────────────────────────────────────

export type ReportMode = 'evidence_only' | 'generated'

export interface ReportDocument {
  schema_version: 1
  report_id: string
  version: number
  title: string
  language: string
  created_at: string
  evidence_hash: string
  profile_id?: string | null
  model?: string | null
  mode: ReportMode
  template_file_id?: string | null
  sections: Section[]
  tables: Record<string, Table>
  figures: Record<string, Figure>
}

/** `reports/<report_id>/meta.json` — what the list route shows per report. */
export interface ReportMeta {
  report_id: string
  title: string
  created_at: string
  updated_at: string
  latest_version: number
  mode: ReportMode
  evidence_hash: string
  profile_id?: string | null
  model?: string | null
}

export interface DeleteReportResponse { deleted: boolean; report_id: string }

// ── the generation job (WP3 / WP7b) ─────────────────────────────────────────

export type ReportJobStatus = 'running' | 'done' | 'failed' | 'aborted'
/** `mapping` (WP11): the model proposes a mapping plan for an untagged template. */
export type ReportJobMode = 'generate' | 'regenerate' | 'mapping'

/** One section the model could not write: stated in the document as
 *  `not_established` with the reason, and listed on the job record. */
export interface ProseFailure {
  section_id: string
  reason: string
  raw_head?: string | null
  repairs: number
}

/** `GET …/reports/generate/status` — `services/reports/report_job.py::public_record`. */
export interface ReportJobRecord {
  status: ReportJobStatus
  report_id: string
  /** The version the job wrote; `null` while running. */
  version: number | null
  mode: ReportJobMode
  /** The section id of a `regenerate` job; `null` for a whole report. */
  section: string | null
  progress: { done: number; total: number; current: string | null }
  repairs: number
  prose_failures: ProseFailure[]
  error: string | null
  /** Unix seconds (`time.time()`). */
  started_at: number
  finished_at: number | null
  profile_id: string
  model: string
}

export interface GenerateReportOptions {
  title?: string
  /** BCP-47-ish free text; the backend defaults to `"en"`. */
  language?: string
  /** Section ids to write; omitted = the backend's default set. */
  sections?: string[]
  instruction?: string
  /** A `report_template` upload the new report is bound to (WP11). */
  template_file_id?: string
}

export interface RegenerateSectionOptions {
  instruction?: string
  language?: string
}

export interface GenerateReportResponse { status: 'running'; report_id: string }
export interface AbortGenerateResponse { status: ReportJobStatus; aborting: boolean }
export interface RegenerateSectionResponse { status: 'running'; report_id: string; version: number }

// ── templates (WP11, Increment 2) ───────────────────────────────────────────
// Mirrors `services/reports/docx_reader.py::TemplateOutline` and
// `services/reports/template_untagged.py::MappingPlan` one to one (the plan's
// pinned interfaces).

export type TemplateMode = 'tagged' | 'untagged'

export interface TemplateHeading {
  index: number
  level: number
  text: string
  style: string
  is_body_start: boolean
}

export interface TemplateTag {
  kind: 'var' | 'for' | 'endfor' | 'if' | 'endif' | 'other'
  text: string
  /** `-1` for a tag in the header or footer. */
  paragraph_index: number
}

export interface TemplatePlaceholder { text: string; paragraph_index: number }

export interface TemplateTable {
  index: number
  n_rows: number
  n_cols: number
  header: string[]
  style: string | null
}

export interface TemplateOutline {
  mode: TemplateMode
  language: string | null
  headings: TemplateHeading[]
  tags: TemplateTag[]
  placeholders: TemplatePlaceholder[]
  tables: TemplateTable[]
  header_text: string
  footer_text: string
  body_start_index: number | null
  n_paragraphs: number
  has_toc: boolean
  /** "text box", "SmartArt", "content control", … — kept where the writer does not touch them. */
  unsupported: string[]
}

export type MappingAction = 'keep' | 'rename' | 'drop'

export interface MappingEntry {
  heading_index: number
  action: MappingAction
  new_text: string | null
  section_ids: string[]
}

export interface MappingInsert {
  after_heading_index: number
  section_id: string
  heading: string
}

export interface MappingPlan {
  entries: MappingEntry[]
  inserted: MappingInsert[]
  /** `"[Client name]"` → value. */
  placeholders: Record<string, string>
  unmapped_sections: string[]
  notes: string[]
}

/** `POST …/{id}/template` — the binding and the outline the reader produced. */
export interface ReportTemplateBinding {
  template_file_id: string | null
  mode: TemplateMode | null
  language: string | null
  outline: TemplateOutline | null
}

/** `GET …/{id}/template` — the binding plus the stored plan (all null when unbound). */
export interface ReportTemplateState extends ReportTemplateBinding {
  plan: MappingPlan | null
}

export interface ProposeMappingOptions { language?: string }
export interface ProposeMappingResponse { status: 'running'; report_id: string }

// ── the round trip (WP14, Increment 3) ──────────────────────────────────────
// Mirrors `services/reports/roundtrip.py::RoundTripSection` /
// `RoundTripResult` and the diff / capabilities routes of the plan's pinned
// route contract one to one.

export interface RoundTripSection {
  /** `null` when the heading matched no section of the base document. */
  section_id: string | null
  heading: string
  blocks: Block[]
  changed: boolean
  comments: string[]
}

export interface RoundTripResult {
  sections: RoundTripSection[]
  /** Content that matched no section — reported, never merged. */
  unmatched: string[]
  comments_global: string[]
  accepted_tracked_changes: number
}

/** `POST …/{id}/roundtrip` — the merged version and what the reader found. */
export interface RoundTripResponse {
  report_id: string
  version: number
  result: RoundTripResult
  template_file_id: string | null
}

export type VersionChange = 'unchanged' | 'changed' | 'added' | 'removed'

export interface VersionDiffSection {
  section_id: string
  heading: string
  change: VersionChange
  source_a: SectionSource | string | null
  source_b: SectionSource | string | null
  pending_instruction: string | null
  comments: string[]
}

/** `GET …/{id}/versions/{a}/diff/{b}`. */
export interface VersionDiff {
  a: number
  b: number
  sections: VersionDiffSection[]
}

/** `GET /{name}/reports/capabilities` — `pdf` is `soffice` on the server's PATH. */
export interface ReportCapabilities { pdf: boolean }

/**
 * `GET /{name}/reports/evidence_hash` — the hash of the session's CURRENT
 * evidence (what an evidence-only report made now would carry as
 * `evidence_hash`), plus how many sections are established. The viewer's
 * "evidence changed since vN" badge compares a document against it. Never
 * 204: the empty evidence has a hash too.
 */
export interface EvidenceHashStatus {
  evidence_hash: string
  sections_ok: number
  sections_total: number
}

export type ExportFormat = 'docx' | 'pdf'

// ── errors ──────────────────────────────────────────────────────────────────

/**
 * The backend's `error_kind` values on these routes: the store's refusals
 * (`routers/reports.py::_http`), the phase-1 mode gate, the edit-lock check
 * (`routers/projects.py::_check_project_lock`, 409), and the two the wrapper
 * itself adds for a body it could not read.
 */
export type ReportErrorKind =
  | 'report_not_found'
  | 'invalid_report_id'
  | 'figure_not_found'
  | 'report_mode_not_supported'
  | 'project_locked'
  | 'tool_error'
  | 'unknown'
  // the job routes (`routers/report_jobs.py`)
  | 'report_job_in_flight'
  | 'report_job_not_found'
  | 'report_section_not_found'
  | 'no_evidence'
  | 'missing_api_key'
  | 'sdk_not_installed'
  // the template routes (WP11)
  | 'upload_not_found'
  | 'template_not_a_template'
  | 'template_unreadable'
  | 'no_template'
  | 'template_not_untagged'
  | 'invalid_mapping_plan'
  | 'tagged_render_error'
  // the round trip, diff and PDF routes (WP14)
  | 'roundtrip_unreadable'
  | 'roundtrip_not_a_report'
  | 'report_version_not_found'
  | 'pdf_not_available'
  | 'pdf_conversion_failed'

export interface ReportErrorDetail {
  error_kind: ReportErrorKind | string
  message: string
  [extra: string]: unknown
}

export class ReportsError extends Error {
  detail: ReportErrorDetail
  status: number
  constructor(detail: ReportErrorDetail, status: number) {
    super(detail.message)
    this.name = 'ReportsError'
    this.detail = detail
    this.status = status
  }
}

export function isReportError(e: unknown, kind?: ReportErrorKind): e is ReportsError {
  if (!(e instanceof ReportsError)) return false
  return kind == null || e.detail.error_kind === kind
}

/** The text a toast shows for any failure of these calls. */
export function reportErrorMessage(e: unknown, fallback = 'Request failed'): string {
  if (isReportError(e)) return e.detail.message || fallback
  if (e instanceof Error && e.message) return e.message
  return fallback
}

/**
 * The toast copy for a refused generate / regenerate / abort. The kinds the
 * job routes answer with get a sentence that names the next action; any
 * other kind falls back to the backend's own message.
 */
export function reportJobErrorMessage(e: unknown, fallback = 'Request failed'): string {
  if (isReportError(e)) {
    switch (e.detail.error_kind) {
      case 'report_job_in_flight':
        return 'A report is already being written — wait for it to finish or abort it.'
      case 'no_evidence':
        return 'Nothing to report on yet: run a study first (the Energy Hub reference design or an adequacy study).'
      case 'missing_api_key':
      case 'sdk_not_installed':
        return `No usable LLM profile: configure an LLM profile in Settings. (${e.detail.message})`
      case 'project_locked':
        return e.detail.message || 'The Project is being edited by another user.'
      default:
        return e.detail.message || fallback
    }
  }
  return reportErrorMessage(e, fallback)
}

/**
 * The toast copy for a refused template bind / plan / templated export
 * (WP11). The plan and job kinds fall through to `reportJobErrorMessage`,
 * so an in-flight job or a missing LLM profile reads the same everywhere.
 */
export function templateErrorMessage(e: unknown, fallback = 'Request failed'): string {
  if (isReportError(e)) {
    switch (e.detail.error_kind) {
      case 'upload_not_found':
        return `No such upload in this Project — refresh the template list. (${e.detail.message})`
      case 'template_not_a_template':
        return `That upload is not a Word template (.docx). (${e.detail.message})`
      case 'template_unreadable':
        return `The template could not be read as a Word file. (${e.detail.message})`
      case 'no_template':
        return 'Bind a template first — a mapping plan needs an untagged template.'
      case 'template_not_untagged':
        return 'A tagged template fills itself from its {{ tags }}; a mapping plan is only for an untagged one.'
      case 'invalid_mapping_plan':
        return `The plan was refused: ${e.detail.message}`
      case 'tagged_render_error':
        return `The template's tags could not be rendered: ${e.detail.message}`
      default:
        return reportJobErrorMessage(e, fallback)
    }
  }
  return reportErrorMessage(e, fallback)
}

/**
 * The toast copy for a refused round trip, version diff or PDF export
 * (WP14). The upload's own refusal (`UploadError`) carries the same
 * `{error_kind, message}` shape and falls through to its message; every
 * other kind falls through to the template / job copy.
 */
export function roundTripErrorMessage(e: unknown, fallback = 'Request failed'): string {
  if (isReportError(e)) {
    switch (e.detail.error_kind) {
      case 'roundtrip_unreadable':
        return `The edited copy could not be read as a Word file. (${e.detail.message})`
      case 'roundtrip_not_a_report':
        return `This file does not look like an export of this report — nothing to merge. (${e.detail.message})`
      case 'report_version_not_found':
        return `No such version of this report. (${e.detail.message})`
      case 'pdf_not_available':
        return `PDF export needs LibreOffice on the server. (${e.detail.message})`
      case 'pdf_conversion_failed':
        return `PDF conversion failed: ${e.detail.message}`
      default:
        return templateErrorMessage(e, fallback)
    }
  }
  return reportErrorMessage(e, fallback)
}

async function _parseError(resp: Response): Promise<ReportsError> {
  let detail: ReportErrorDetail = {
    error_kind: 'unknown',
    message: `reports request failed: ${resp.status} ${resp.statusText}`.trim(),
  }
  try {
    const body = await resp.json()
    if (body && typeof body === 'object' && body.detail && typeof body.detail === 'object') {
      const d = body.detail as Partial<ReportErrorDetail>
      detail = {
        ...d,
        error_kind: typeof d.error_kind === 'string' ? d.error_kind : 'unknown',
        message: typeof d.message === 'string' ? d.message : detail.message,
      }
    }
  } catch {
    // Non-JSON response — keep the generic detail.
  }
  return new ReportsError(detail, resp.status)
}

async function _json<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw await _parseError(resp)
  return resp.json() as Promise<T>
}

// ── routes ──────────────────────────────────────────────────────────────────

function base(projectName: string): string {
  return `/api/projects/${encodeURIComponent(projectName)}/reports`
}

/** `GET /{name}/reports` — every report of the Project, newest first. */
export async function listReports(projectName: string): Promise<ReportMeta[]> {
  return _json<ReportMeta[]>(await fetch(base(projectName)))
}

/** `GET /{name}/reports/{id}[?version=N]` — the latest version by default. */
export async function getReport(
  projectName: string,
  reportId: string,
  version?: number,
): Promise<ReportDocument> {
  const q = version != null ? `?version=${encodeURIComponent(String(version))}` : ''
  return _json<ReportDocument>(
    await fetch(`${base(projectName)}/${encodeURIComponent(reportId)}${q}`),
  )
}

/** `GET /{name}/reports/{id}/versions/{v}` — one stored version. */
export async function getReportVersion(
  projectName: string,
  reportId: string,
  version: number,
): Promise<ReportDocument> {
  return _json<ReportDocument>(await fetch(
    `${base(projectName)}/${encodeURIComponent(reportId)}/versions/${encodeURIComponent(String(version))}`,
  ))
}

/** The figure PNG route — an `<img src>`, nothing else. */
export function reportFigureUrl(projectName: string, reportId: string, figureId: string): string {
  return `${base(projectName)}/${encodeURIComponent(reportId)}/figures/${encodeURIComponent(figureId)}`
}

/** `DELETE /{name}/reports/{id}` — every version; refused under a foreign edit lock. */
export async function deleteReport(
  projectName: string,
  reportId: string,
): Promise<DeleteReportResponse> {
  return _json<DeleteReportResponse>(await fetch(
    `${base(projectName)}/${encodeURIComponent(reportId)}`,
    { method: 'DELETE', headers: { ...rawFetchHeaders('DELETE') } },
  ))
}

/**
 * `POST /{name}/reports` with `mode: "evidence_only"` — the phase-1 report:
 * tables, figures, disclosures and not-established statements from the
 * session's current result state, no prose. Returns the meta plus the
 * stored document.
 */
export async function createEvidenceOnlyReport(
  projectName: string,
  title?: string,
): Promise<ReportMeta & { document: ReportDocument }> {
  const body: { mode: 'evidence_only'; title?: string } = { mode: 'evidence_only' }
  if (title != null && title.trim()) body.title = title
  return _json<ReportMeta & { document: ReportDocument }>(await fetch(base(projectName), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('POST') },
    body: JSON.stringify(body),
  }))
}

export interface ExportReportOptions {
  version?: number
  filename?: string
  /** WP14: `"pdf"` converts the rendered `.docx` with LibreOffice — 501
   *  `pdf_not_available` unless `getReportCapabilities().pdf`. Omitted =
   *  the backend's default (`docx`). */
  format?: ExportFormat
}

/**
 * `POST /{name}/reports/{id}/export` — renders one version to `.docx` (or,
 * with `format: "pdf"`, converts it) and stores it as an `agent_export`
 * upload of the Project. The returned `UploadMeta.file_id` feeds
 * `getUploadBlobUrl` for the download.
 */
export async function exportReport(
  projectName: string,
  reportId: string,
  opts: ExportReportOptions = {},
): Promise<UploadMeta> {
  const body: ExportReportOptions = {}
  if (opts.version != null) body.version = opts.version
  if (opts.filename) body.filename = opts.filename
  if (opts.format) body.format = opts.format
  return _json<UploadMeta>(await fetch(
    `${base(projectName)}/${encodeURIComponent(reportId)}/export`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('POST') },
      body: JSON.stringify(body),
    },
  ))
}

// ── the generation job (WP3 / WP7b) ─────────────────────────────────────────

function _postJson(url: string, body: unknown): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('POST') },
    body: JSON.stringify(body),
  })
}

/**
 * `POST /{name}/reports/generate` — start a generated report (v1) on the
 * active LLM profile from the session's current result state. Refusals:
 * `report_job_in_flight` (409), `no_evidence` (400), `missing_api_key` /
 * `sdk_not_installed` (400), `project_locked` (409). Poll `getGenerateStatus`.
 */
export async function generateReport(
  projectName: string,
  opts: GenerateReportOptions = {},
): Promise<GenerateReportResponse> {
  const body: GenerateReportOptions = { language: (opts.language ?? '').trim() || 'en' }
  if (opts.title != null && opts.title.trim()) body.title = opts.title.trim()
  if (opts.sections && opts.sections.length > 0) body.sections = opts.sections
  if (opts.instruction != null && opts.instruction.trim()) body.instruction = opts.instruction.trim()
  if (opts.template_file_id) body.template_file_id = opts.template_file_id
  return _json<GenerateReportResponse>(await _postJson(`${base(projectName)}/generate`, body))
}

/** `GET /{name}/reports/generate/status` — the job record; `null` when never run (204). */
export async function getGenerateStatus(projectName: string): Promise<ReportJobRecord | null> {
  const resp = await fetch(`${base(projectName)}/generate/status`)
  if (resp.status === 204) return null
  return _json<ReportJobRecord>(resp)
}

/** `POST /{name}/reports/generate/abort` — stop after the current section (idempotent). */
export async function abortGenerate(projectName: string): Promise<AbortGenerateResponse> {
  return _json<AbortGenerateResponse>(await fetch(`${base(projectName)}/generate/abort`, {
    method: 'POST',
    headers: { ...rawFetchHeaders('POST') },
  }))
}

/**
 * `POST /{name}/reports/{id}/sections/{section_id}/regenerate` — write one
 * section again from the latest version, saved as the next version. Same
 * refusals as `generateReport` plus `report_not_found` /
 * `report_section_not_found`.
 */
export async function regenerateSection(
  projectName: string,
  reportId: string,
  sectionId: string,
  opts: RegenerateSectionOptions = {},
): Promise<RegenerateSectionResponse> {
  const body: RegenerateSectionOptions = {}
  if (opts.instruction != null && opts.instruction.trim()) body.instruction = opts.instruction.trim()
  if (opts.language != null && opts.language.trim()) body.language = opts.language.trim()
  return _json<RegenerateSectionResponse>(await _postJson(
    `${base(projectName)}/${encodeURIComponent(reportId)}/sections/${encodeURIComponent(sectionId)}/regenerate`,
    body,
  ))
}

// ── templates (WP11) ────────────────────────────────────────────────────────

function templateUrl(projectName: string, reportId: string): string {
  return `${base(projectName)}/${encodeURIComponent(reportId)}/template`
}

/**
 * `POST /{name}/reports/{id}/template` with `{file_id}` — bind a
 * `report_template` upload to the report (`null` unbinds → the built-in
 * writer). The backend reads the outline and detects the language.
 * Refusals: `report_not_found` / `upload_not_found` (404),
 * `template_not_a_template` / `template_unreadable` (400), `project_locked` (409).
 */
export async function setReportTemplate(
  projectName: string,
  reportId: string,
  fileId: string | null,
): Promise<ReportTemplateBinding> {
  return _json<ReportTemplateBinding>(await _postJson(templateUrl(projectName, reportId), { file_id: fileId }))
}

/** `GET /{name}/reports/{id}/template` — the binding, the outline and the stored plan. */
export async function getReportTemplate(
  projectName: string,
  reportId: string,
): Promise<ReportTemplateState> {
  return _json<ReportTemplateState>(await fetch(templateUrl(projectName, reportId)))
}

/**
 * `POST /{name}/reports/{id}/template/plan` — ask the model for a mapping
 * plan (an untagged template only); runs as a report job with
 * `mode: "mapping"`, so `getGenerateStatus` / `abortGenerate` apply.
 * Refusals: `no_template` / `template_not_untagged` (400),
 * `report_job_in_flight` (409), `missing_api_key` / `sdk_not_installed` (400).
 */
export async function proposeMappingPlan(
  projectName: string,
  reportId: string,
  opts: ProposeMappingOptions = {},
): Promise<ProposeMappingResponse> {
  const body: ProposeMappingOptions = {}
  if (opts.language != null && opts.language.trim()) body.language = opts.language.trim()
  return _json<ProposeMappingResponse>(await _postJson(`${templateUrl(projectName, reportId)}/plan`, body))
}

/**
 * `PUT /{name}/reports/{id}/template/plan` — store a user-edited plan; the
 * backend validates it and answers with the stored plan. `strict: true`
 * turns every validation note into an `invalid_mapping_plan` refusal.
 */
export async function putMappingPlan(
  projectName: string,
  reportId: string,
  plan: MappingPlan,
  opts: { strict?: boolean } = {},
): Promise<MappingPlan> {
  const body: MappingPlan & { strict?: boolean } = { ...plan }
  if (opts.strict) body.strict = true
  return _json<MappingPlan>(await fetch(`${templateUrl(projectName, reportId)}/plan`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('PUT') },
    body: JSON.stringify(body),
  }))
}

// ── the round trip, the diff and the capabilities (WP14) ────────────────────

/**
 * `POST /{name}/reports/{id}/roundtrip` with `{file_id, bind_as_template}` —
 * merge a `report_roundtrip` upload into the report as its next version
 * (synchronous, no job). With `bind_as_template` (the default) the uploaded
 * file also becomes the report's template, so the styling the user changed
 * survives the next export. Refusals: `report_not_found` / `upload_not_found`
 * (404), `roundtrip_unreadable` / `roundtrip_not_a_report` (400),
 * `project_locked` / `report_job_in_flight` (409).
 */
export async function importRoundTrip(
  projectName: string,
  reportId: string,
  fileId: string,
  bindAsTemplate = true,
): Promise<RoundTripResponse> {
  return _json<RoundTripResponse>(await _postJson(
    `${base(projectName)}/${encodeURIComponent(reportId)}/roundtrip`,
    { file_id: fileId, bind_as_template: bindAsTemplate },
  ))
}

/**
 * The viewer's "Upload edited copy": `uploadFile(project, file,
 * 'report_roundtrip')` (the upload's own `UploadError` on refusal) followed
 * by `importRoundTrip` on the new upload.
 */
export async function uploadRoundTrip(
  projectName: string,
  reportId: string,
  file: File,
  bindAsTemplate = true,
): Promise<RoundTripResponse> {
  const meta = await uploadFile(projectName, file, 'report_roundtrip')
  return importRoundTrip(projectName, reportId, meta.file_id, bindAsTemplate)
}

/** `GET /{name}/reports/{id}/versions/{a}/diff/{b}` — per-section change between two versions. */
export async function getVersionDiff(
  projectName: string,
  reportId: string,
  a: number,
  b: number,
): Promise<VersionDiff> {
  return _json<VersionDiff>(await fetch(
    `${base(projectName)}/${encodeURIComponent(reportId)}/versions/${encodeURIComponent(String(a))}/diff/${encodeURIComponent(String(b))}`,
  ))
}

/** `GET /{name}/reports/capabilities` — what this server can do beyond `.docx` (`pdf`). */
export async function getReportCapabilities(projectName: string): Promise<ReportCapabilities> {
  return _json<ReportCapabilities>(await fetch(`${base(projectName)}/capabilities`))
}

/** `GET /{name}/reports/evidence_hash` — the live evidence's hash for the staleness badge. */
export async function getEvidenceHash(projectName: string): Promise<EvidenceHashStatus> {
  return _json<EvidenceHashStatus>(await fetch(`${base(projectName)}/evidence_hash`))
}
