/**
 * Phase A — chatbot uploads API.
 *
 * Thin wrappers over `/api/projects/{name}/uploads/*`. Used by ChatPanel
 * (drag-drop + paste + file-picker) and by chatStore's upload-chip strip.
 * Phase C will add an `attachment_file_ids` field to the chat stream
 * request that references these meta entries.
 *
 * These call `fetch` directly rather than the axios client — multipart upload
 * and blob URLs are simpler without it — which means they bypass the CSRF
 * request interceptor and have to add the header themselves. Only the two
 * mutating calls need it; the reads are exempt server-side.
 */
import { rawFetchHeaders } from './csrf'

/** `report_template` (WP11): a Word file a report renders into.
 *  `report_roundtrip` (WP14): an edited export of a report, uploaded to be merged back. */
export type UploadKind = 'user_upload' | 'agent_export' | 'report_template' | 'report_roundtrip'

export interface UploadMeta {
  schema_version: number
  file_id: string
  filename: string
  mime: string
  size: number
  sha256: string
  kind: UploadKind
  uploaded_at: number
  blob_ready: boolean
  version: number
  page_count?: number | null
  truncated_to_100_pages?: boolean | null
}

export interface DeleteUploadResponse {
  deleted: boolean
  file_id: string
  reason?: 'not_found' | 'in_use'
}

export interface UploadErrorDetail {
  error_kind: string
  message: string
  hint?: string
}

export class UploadError extends Error {
  detail: UploadErrorDetail
  status: number
  constructor(detail: UploadErrorDetail, status: number) {
    super(detail.message)
    this.detail = detail
    this.status = status
  }
}

async function _parseError(resp: Response): Promise<UploadError> {
  let detail: UploadErrorDetail = {
    error_kind: 'unknown',
    message: `upload request failed: ${resp.status} ${resp.statusText}`,
  }
  try {
    const body = await resp.json()
    if (body && typeof body === 'object' && body.detail) {
      detail = body.detail as UploadErrorDetail
    }
  } catch {
    // Non-JSON response — keep the generic detail
  }
  return new UploadError(detail, resp.status)
}

/**
 * Upload a file to the active project. The server returns the UploadMeta —
 * idempotent on bytes (same SHA256 → existing entry, original uploaded_at
 * preserved). Throws `UploadError` with the structured backend payload on
 * 4xx / 5xx so the caller can render `error_kind` directly.
 */
export async function uploadFile(
  projectName: string,
  file: File,
  /** `report_template` stores the file as a report template (WP11), `report_roundtrip`
   *  as an edited copy of a report (WP14); omitted = `user_upload`. */
  kind?: UploadKind,
): Promise<UploadMeta> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(
    `/api/projects/${encodeURIComponent(projectName)}/uploads${_kindQuery(kind)}`,
    // No Content-Type: the browser must set its own multipart boundary.
    { method: 'POST', body: form, headers: { ...rawFetchHeaders('POST') } },
  )
  if (!resp.ok) {
    throw await _parseError(resp)
  }
  return resp.json() as Promise<UploadMeta>
}

function _kindQuery(kind: UploadKind | undefined): string {
  return kind ? `?kind=${encodeURIComponent(kind)}` : ''
}

/** List the active project's uploads, newest-first; `kind` filters (WP11). */
export async function listUploads(projectName: string, kind?: UploadKind): Promise<UploadMeta[]> {
  const resp = await fetch(
    `/api/projects/${encodeURIComponent(projectName)}/uploads${_kindQuery(kind)}`,
  )
  if (!resp.ok) {
    throw await _parseError(resp)
  }
  return resp.json() as Promise<UploadMeta[]>
}

/** Read a single upload's meta.json. */
export async function getUploadMeta(
  projectName: string,
  fileId: string,
): Promise<UploadMeta> {
  const resp = await fetch(
    `/api/projects/${encodeURIComponent(projectName)}/uploads/${encodeURIComponent(fileId)}/meta`,
  )
  if (!resp.ok) {
    throw await _parseError(resp)
  }
  return resp.json() as Promise<UploadMeta>
}

/**
 * Return a stable URL for the blob — suitable as an `<img>` src or `<a>` href.
 * The server streams the bytes with `Content-Disposition: inline`. The browser
 * caches by URL; bust by query-string when the same file_id rotates kinds
 * (rare).
 */
export function getUploadBlobUrl(projectName: string, fileId: string): string {
  return `/api/projects/${encodeURIComponent(projectName)}/uploads/${encodeURIComponent(fileId)}/blob`
}

/**
 * Delete an upload. Always resolves; check the `deleted` field to distinguish
 * success vs missing/in-use. The Promise rejects only on network errors —
 * not on logical "not found", which is a normal 200 response.
 */
export async function deleteUpload(
  projectName: string,
  fileId: string,
): Promise<DeleteUploadResponse> {
  const resp = await fetch(
    `/api/projects/${encodeURIComponent(projectName)}/uploads/${encodeURIComponent(fileId)}`,
    { method: 'DELETE', headers: { ...rawFetchHeaders('DELETE') } },
  )
  if (!resp.ok) {
    throw await _parseError(resp)
  }
  return resp.json() as Promise<DeleteUploadResponse>
}
