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
 * Phase-1 routes only. Generation (`generate`, `regenerate`, `abort`,
 * `status`) is WP3's job and lands with WP7b.
 */
import { rawFetchHeaders } from './csrf'
import type { UploadMeta } from './uploads'

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

/**
 * `POST /{name}/reports/{id}/export` — renders one version to `.docx` and
 * stores it as an `agent_export` upload of the Project. The returned
 * `UploadMeta.file_id` feeds `getUploadBlobUrl` for the download.
 */
export async function exportReport(
  projectName: string,
  reportId: string,
  opts: { version?: number; filename?: string } = {},
): Promise<UploadMeta> {
  const body: { version?: number; filename?: string } = {}
  if (opts.version != null) body.version = opts.version
  if (opts.filename) body.filename = opts.filename
  return _json<UploadMeta>(await fetch(
    `${base(projectName)}/${encodeURIComponent(reportId)}/export`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('POST') },
      body: JSON.stringify(body),
    },
  ))
}
