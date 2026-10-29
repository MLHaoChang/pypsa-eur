import client from './client'
import type { ImportSummary } from './types'

export interface ResultsWorkbookView {
  view_id: string; label: string; card: string; card_label: string; selected: boolean
}

export const ioApi = {
  exportNetcdf: () => client.get('/io/export/netcdf', { responseType: 'blob' }).then(r => r.data as Blob),
  exportCsv: () => client.get('/io/export/csv', { responseType: 'blob' }).then(r => r.data as Blob),
  exportExcel: () => client.get('/io/export/excel', { responseType: 'blob' }).then(r => r.data as Blob),
  exportMatpower: () => client.get('/io/export/matpower', { responseType: 'blob' }).then(r => r.data as Blob),

  /** The result views a workbook can hold; `selected` = included by the project's cards. */
  resultsWorkbookViews: () => client.get<{ views: ResultsWorkbookView[] }>('/results/workbook/views')
    .then(r => r.data.views),
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
  importExcel: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<ImportSummary>('/io/import/excel', fd).then(r => r.data)
  },
  importMatpower: (file: File) => {
    const fd = new FormData(); fd.append('file', file)
    return client.post<ImportSummary>('/io/import/matpower', fd).then(r => r.data)
  },
}
