// The Excel input workbook (IMPORTS-1, excel-input-workbook S2 routes): the
// blank template, a project's workbook as last saved, and the preview-then-
// import of a filled one as a NEW project (IMPORT-FLOW-1). The preview and the
// import never touch the open network; the caller opens the new project.
import client, { formatApiDetail } from './client'
import { bundleErrorMessage } from './projects'
import { fileNameOf } from './resultsEvidence'
import type { ImportSummary } from './types'

export interface WorkbookReport {
  source_format: string
  source_files: string[]
  /** Rows read per sheet, keyed by the workbook's own sheet words ("Nodes"). */
  counts: Record<string, number>
  unmapped: { source_item: string; reason: string }[]
  assumed: { asset: string; parameter: string; value: unknown; why: string }[]
  warnings: string[]
  suggestions: { target: string; parameter: string; current: unknown; proposed: unknown; why: string; source: string }[]
}

/** What a preview and an import both say about the file. */
export interface WorkbookContents {
  summary: ImportSummary
  report: WorkbookReport
  /** Assets whose costs were worked out again from their parts. */
  rederived: string[]
  /** What a workbook never carries (the About sheet's list). */
  not_carried: string[]
}

export interface WorkbookPreview extends WorkbookContents { sha256: string; suggested_name: string }
export interface WorkbookImported extends WorkbookContents { project_id: string; name: string }

export interface WorkbookFile { blob: Blob; filename: string }

// A preview reads and checks every sheet; a large workbook takes a while.
const LONG = 180000

async function download(url: string, fallback: string): Promise<WorkbookFile> {
  try {
    const r = await client.get<Blob>(url, { responseType: 'blob', timeout: LONG, skipErrorToast: true })
    return { blob: r.data, filename: fileNameOf(r.headers['content-disposition']) ?? fallback }
  } catch (e) {
    // The chooser shows this once, in the backend's words.
    throw new Error(await bundleErrorMessage(e))
  }
}

export const workbookApi = {
  template: () => download('/workbook/template', 'PyPSA Studio input workbook.xlsx'),
  /** The project as last saved: unsaved edits are not in it. */
  project: (name: string) => download(`/projects/${encodeURIComponent(name)}/workbook`, `${name}.xlsx`),
  /** Writes nothing. The dialog shows its own refusals (no toast). */
  preview: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<WorkbookPreview>('/workbook/preview', fd, { timeout: LONG, skipErrorToast: true }).then(r => r.data)
  },
  /** Creates the project and leaves it closed; `expectedSha256` is the preview's. */
  import: (file: File, expectedSha256: string, name: string) => {
    const fd = new FormData(); fd.append('file', file); fd.append('expected_sha256', expectedSha256); fd.append('name', name)
    return client.post<WorkbookImported>('/workbook/import', fd, { timeout: LONG, skipErrorToast: true }).then(r => r.data)
  },
}

/** What the dialog offers beside a refusal, on the same line. */
export type RefusalAction = 'preview-again' | 'choose-file' | 'try-again'

export interface WorkbookRefusal {
  message: string
  action: RefusalAction
  /** The taken name, when the import was refused for a name clash. */
  clash?: string
}

const CLASH = /^Project '(.+)' already exists/

/** A preview or import refusal in plain words, with its one action. */
export function workbookRefusal(e: unknown): WorkbookRefusal {
  const res = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const status = res?.status
  const detail = res?.data?.detail
  const kind = (detail as { error_kind?: unknown } | undefined)?.error_kind
  const said = formatApiDetail(detail, '')
  if (status === 409 && kind === 'import_file_changed') return { message: said, action: 'preview-again' }
  if (status === 409 && typeof detail === 'string' && CLASH.test(detail)) {
    const clash = CLASH.exec(detail)![1]
    return { message: `A project named “${clash}” already exists, so nothing was created.`, action: 'try-again', clash }
  }
  if (status === 413 && typeof detail === 'string') {
    // read_capped: "Upload exceeds the N MB limit."
    const mb = /(\d+)\s*MB/.exec(detail)?.[1]
    return {
      message: mb ? `The file is larger than the ${mb} MB upload limit. Nothing was read.`
        : 'The file is larger than the upload limit. Nothing was read.',
      action: 'choose-file',
    }
  }
  if ((status === 413 || status === 422) && said) return { message: said, action: 'choose-file' }
  if (status === 500 && kind === 'import_write_failed') {
    return { message: 'The project could not be written; nothing was created.', action: 'try-again' }
  }
  // Any other refusal the backend words itself (503: no workbook in this build).
  if (typeof kind === 'string' && said) return { message: said, action: 'try-again' }
  if (!res) return { message: 'The app did not answer: nothing was created.', action: 'try-again' }
  return { message: 'The workbook could not be imported: nothing was created.', action: 'try-again' }
}
