import client from '../../../api/client'
import type { AssetRef, AssetResultsResponse, ViewMode } from './types'

export interface AssetQueryParams {
  componentClass: string
  name: string
  category: string
  metrics: string[]
  source: 'lopf' | 'ac_pf'
  fromIso: string | null
  toIso: string | null
  period: number | string | null
  mode: ViewMode
}

function query(p: AssetQueryParams, extra: Record<string, string> = {}) {
  const q: Record<string, string> = {
    category: p.category,
    metrics: p.metrics.join(','),
    source: p.source,
    mode: p.mode,
    ...extra,
  }
  if (p.fromIso) q.from = p.fromIso
  if (p.toIso) q.to = p.toIso
  if (p.period != null) q.period = String(p.period)
  return q
}

const path = (p: AssetQueryParams) =>
  `/results/asset/${encodeURIComponent(p.componentClass)}/${encodeURIComponent(p.name)}`

export const assetResultsApi = {
  async listAssets(): Promise<AssetRef[]> {
    const { data } = await client.get('/results/asset/assets')
    return data.assets ?? []
  },

  async get(p: AssetQueryParams): Promise<AssetResultsResponse> {
    const { data } = await client.get(path(p), { params: query(p) })
    return data
  },

  /** Absolute URL for the workbook — used as an <a download> href so the
   *  browser streams it straight to disk without buffering in JS. */
  exportXlsxUrl(p: AssetQueryParams, scope: 'view' | 'full'): string {
    const qs = new URLSearchParams(query(p, { scope })).toString()
    return `${client.defaults.baseURL ?? ''}${path(p)}/export.xlsx?${qs}`
  },
}
