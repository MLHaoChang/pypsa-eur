/**
 * Chat's Excel input workbook cards (excel-input-workbook issue 06, IMPORT-FLOW-1).
 *
 * - `WorkbookImportCard`: what approving `import_input_workbook` creates, on its
 *   confirmation card (backend `harness/workbook_card.py`): the new project's
 *   name, the file, and the same report the import dialog shows. Nothing is
 *   written until the user approves, and the import never overwrites.
 * - `WorkbookDownloadCard`: a `get_input_workbook` result's download, fetched by
 *   the browser through the same routes the Export chooser uses.
 */
import { useState } from 'react'
import { workbookApi, type WorkbookContents } from '../../api/workbook'
import { WorkbookReport } from './WorkbookReport'
import { AS_LAST_SAVED, INPUT_WORKBOOK_LABEL, TEMPLATE_LABEL } from './workbookWords'

export interface WorkbookImportCard extends WorkbookContents {
  version: 1
  kind: 'workbook_import'
  file_id: string
  filename: string
  sha256: string
  name: string
  says: string
}

const LISTS = ['unmapped', 'assumed', 'warnings', 'suggestions'] as const

/** The card, or null when it arrived malformed (then the request is never approvable). */
export function workbookCardOf(value: unknown): WorkbookImportCard | null {
  const o = value as Record<string, unknown> | null
  if (!o || typeof o !== 'object' || o.version !== 1 || o.kind !== 'workbook_import') return null
  if (typeof o.name !== 'string' || !o.name || typeof o.filename !== 'string' || typeof o.sha256 !== 'string') return null
  const r = o.report as Record<string, unknown> | null
  if (!r || typeof r !== 'object' || !r.counts || typeof r.counts !== 'object') return null
  if (!LISTS.every(k => Array.isArray(r[k]))) return null
  if (!Array.isArray(o.rederived) || !Array.isArray(o.not_carried)) return null
  return o as unknown as WorkbookImportCard
}

/** How many rows of a list the card left out (the server caps each list). */
function leftOut(card: WorkbookImportCard): number {
  const r = card.report as unknown as Record<string, unknown>
  return LISTS.reduce((sum, k) => {
    const total = r[`${k}_total`]
    return sum + (typeof total === 'number' ? total - (r[k] as unknown[]).length : 0)
  }, 0)
}

export function WorkbookImportCardBody({ card }: { card: WorkbookImportCard }) {
  const more = leftOut(card)
  return (
    <div className="mb-2 text-xs" data-testid="chat-workbook-card">
      <div className="font-medium mb-1" data-testid="chat-workbook-card-title">
        New project “{card.name}” from {card.filename}
      </div>
      <p className="mb-2 text-muted">
        Creates a new project; nothing is overwritten. The open project stays as it is, and the new one stays closed.
      </p>
      <WorkbookReport contents={card} />
      {more > 0 && (
        <p className="mt-1 text-muted" data-testid="chat-workbook-card-more">
          {more} more {more === 1 ? 'line' : 'lines'} not listed here; import it from New project › Import file to see them all.
        </p>
      )}
    </div>
  )
}

interface DownloadResult {
  workbook_download: 'template' | 'project'
  filename: string
  project?: string
}

/** A `get_input_workbook` result, or null for any other tool result. */
export function workbookDownloadOf(value: unknown): DownloadResult | null {
  const o = value as Record<string, unknown> | null
  if (!o || typeof o !== 'object' || typeof o.filename !== 'string') return null
  if (o.workbook_download === 'template') return o as unknown as DownloadResult
  if (o.workbook_download === 'project' && typeof o.project === 'string' && o.project) return o as unknown as DownloadResult
  return null
}

export function WorkbookDownloadCard({ result }: { result: unknown }) {
  const offer = workbookDownloadOf(result)
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle')
  const [error, setError] = useState<string | null>(null)
  if (!offer) return null
  const project = offer.workbook_download === 'project' ? offer.project : undefined
  const download = async () => {
    setState('busy'); setError(null)
    try {
      const f = project ? await workbookApi.project(project) : await workbookApi.template()
      const url = URL.createObjectURL(f.blob)
      const a = document.createElement('a')
      a.href = url; a.download = f.filename; a.click()
      URL.revokeObjectURL(url)
      setState('done')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The download failed.')
      setState('idle')
    }
  }
  return (
    <div className="mt-1 rounded border border-border p-2 font-sans text-[11px]" data-testid="chat-workbook-download">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-text" title={offer.filename}>
          {project ? INPUT_WORKBOOK_LABEL : TEMPLATE_LABEL} · {offer.filename}
        </span>
        <button type="button" className="text-accent disabled:opacity-40" onClick={download}
          disabled={state === 'busy'} data-testid="chat-workbook-download-button">
          {state === 'busy' ? 'Downloading…' : state === 'done' ? 'Download again' : 'Download'}
        </button>
      </div>
      {project && <p className="mt-0.5 text-muted">{AS_LAST_SAVED}</p>}
      {error && <p role="alert" className="mt-0.5 text-warn" data-testid="chat-workbook-download-error">{error}</p>}
    </div>
  )
}
