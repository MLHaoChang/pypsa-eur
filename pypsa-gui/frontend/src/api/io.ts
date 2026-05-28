import client from './client'
import type { ImportSummary } from './types'

export const ioApi = {
  exportNetcdf: () => client.get('/io/export/netcdf', { responseType: 'blob' }).then(r => r.data as Blob),
  exportCsv: () => client.get('/io/export/csv', { responseType: 'blob' }).then(r => r.data as Blob),
  exportExcel: () => client.get('/io/export/excel', { responseType: 'blob' }).then(r => r.data as Blob),
  exportMatpower: () => client.get('/io/export/matpower', { responseType: 'blob' }).then(r => r.data as Blob),

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
