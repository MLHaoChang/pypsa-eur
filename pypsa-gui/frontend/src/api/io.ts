import client from './client'
import type { ImportSummary } from './types'

export interface ResultsWorkbookView {
  view_id: string; label: string; card: string; card_label: string; selected: boolean
  available: boolean; reason: string | null
}

/** The run a results workbook holds: the latest completed run; null when none is recorded. */
export interface ResultsWorkbookRun { run_id: string; started_at: string | null }
export interface ResultsWorkbookListing { views: ResultsWorkbookView[]; run: ResultsWorkbookRun | null }

export const EXPORT_TIMEOUT_MS = 600000

/** A blob-typed request returns its JSON error as a Blob: read the server's `detail` sentence. */
export async function blobErrorReason(e: unknown): Promise<string | null> {
  const body = (e as { response?: { data?: unknown } }).response?.data
  if (!(body instanceof Blob)) return null
  try {
    const d = JSON.parse(await body.text())?.detail
    return typeof d === 'string' ? d : d?.message ?? null
  } catch { return null }
}

async function rethrowWithServerReason(e: unknown): Promise<never> {
  const msg = await blobErrorReason(e)
  if (msg) throw new Error(msg)
  throw e
}

export const ioApi = {
  exportNetcdf: () => client.get('/io/export/netcdf', { responseType: 'blob' }).then(r => r.data as Blob),
  exportCsv: () => client.get('/io/export/csv', { responseType: 'blob' }).then(r => r.data as Blob),
  /** An hourly year takes ~45 s to write, past the 30 s default: size the timeout to the export.
   *  A refusal's reason is read out of the Blob body (see `blobErrorReason`). */
  exportExcel: () => client.get('/io/export/excel', { responseType: 'blob', timeout: EXPORT_TIMEOUT_MS, skipErrorToast: true })
    .then(r => r.data as Blob).catch(rethrowWithServerReason),
  exportMatpower: () => client.get('/io/export/matpower', { responseType: 'blob' }).then(r => r.data as Blob),

  /** The result views a workbook can hold; `selected` = included by the project's cards. */
  /** No error toast: the chooser says in place that the tabs could not be listed. */
  resultsWorkbookViews: () => client.get<ResultsWorkbookListing>('/results/workbook/views', { skipErrorToast: true })
    .then(r => ({ views: r.data.views, run: r.data.run ?? null })),
  /** The results workbook (RW-TABS-1): the cards' views plus `views`, or every view with `all`.
   *  Null before a solve (204). A refusal carries the backend's sentence in its Blob body. */
  exportResultsWorkbook: async (views: string[], all: boolean): Promise<Blob | null> => {
    try {
      const r = await client.get('/results/workbook.xlsx', {
        params: { views: views.join(','), all: all || undefined },
        responseType: 'blob', timeout: 180000, skipErrorToast: true,
      })
      return r.status === 204 ? null : r.data as Blob
    } catch (e) {
      const body = (e as { response?: { data?: unknown } }).response?.data
      if (body instanceof Blob) {
        try {
          const d = JSON.parse(await body.text())?.detail
          const msg = typeof d === 'string' ? d : d?.message
          if (msg) throw new Error(msg)
        } catch (inner) { if (inner instanceof Error && !(inner instanceof SyntaxError)) throw inner }
      }
      throw e
    }
  },

  importNetcdf: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<ImportSummary>('/io/import/netcdf', fd).then(r => r.data)
  },
  importCsv: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<ImportSummary>('/io/import/csv', fd).then(r => r.data)
  },
  /** No error toast: an input workbook's 422 `import_as_new_project` opens the
   *  workbook dialog instead, and the Import zone shows any other refusal once. */
  importExcel: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<ImportSummary>('/io/import/excel', fd, { skipErrorToast: true }).then(r => r.data)
  },
  importMatpower: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<ImportSummary>('/io/import/matpower', fd).then(r => r.data)
  },
}
