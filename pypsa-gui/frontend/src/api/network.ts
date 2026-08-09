import client from './client'
import type { Bus, Carrier, CatalogPayload, Generator, GeneratorProfileMeta, Line, Link, LinkProfileMeta, Load, LoadProfileMeta, LoadAggregate, LoadSection, StorageUnit, Store, Transformer, TransformerType, SnapshotInfo, NetworkMeta, TimeseriesData, TimeseriesInfo } from './types'
import type { RescalePreview } from '../utils/rescale'

export const networkApi = {
  // Buses
  getBuses: () => client.get<Bus[]>('/network/buses').then(r => r.data),
  createBus: (b: Partial<Bus>) => client.post<{name:string}>('/network/buses', b).then(r => r.data),
  // update_bus's response is {name, rescale} (not the full bus row — see
  // _update_component), where `rescale` previews any connected lines' impedance
  // rescale triggered by a coordinate change (empty when x/y didn't change).
  // Unwrapped to the body (not the raw AxiosResponse) so callers — MapCanvas's
  // drag handler in particular — can read `.rescale` straight off the result.
  updateBus: (name: string, b: Partial<Bus>) =>
    client.put<{ name: string; rescale: RescalePreview[] }>(
      `/network/buses/${encodeURIComponent(name)}`, b,
    ).then(r => r.data),
  deleteBus: (name: string) => client.delete(`/network/buses/${encodeURIComponent(name)}`),
  deleteBusCascade: (name: string) => client.delete(`/network/buses/${encodeURIComponent(name)}/cascade`),
  renameBus: (oldName: string, newName: string) =>
    client.post<{ old_name: string; new_name: string }>(
      `/network/buses/${encodeURIComponent(oldName)}/rename`, { new_name: newName }
    ).then(r => r.data),

  // Carriers
  getCarriers: () => client.get<Carrier[]>('/network/carriers').then(r => r.data),
  createCarrier: (c: Partial<Carrier>) => client.post('/network/carriers', c),
  updateCarrier: (name: string, c: Partial<Carrier>) => client.put(`/network/carriers/${encodeURIComponent(name)}`, c),
  deleteCarrier: (name: string) => client.delete(`/network/carriers/${encodeURIComponent(name)}`),

  // Lines
  getLines: () => client.get<Line[]>('/network/lines').then(r => r.data),
  createLine: (l: Partial<Line>) => client.post('/network/lines', l),
  updateLine: (name: string, l: Partial<Line>) => client.put(`/network/lines/${encodeURIComponent(name)}`, l),
  deleteLine: (name: string) => client.delete(`/network/lines/${encodeURIComponent(name)}`),
  // Recompute line.length (km) from haversine distance between bus0 / bus1
  // coordinates. Skips lines whose buses don't have a usable (x, y) pair.
  // `rescale` previews the per-km-preserving impedance rescale each updated
  // line WOULD get — nothing is written until rescaleImpedances is called
  // with the caller's chosen subset.
  recalculateLineLengths: () =>
    client.post<{ updated: number; skipped: number; total: number; rescale: RescalePreview[] }>(
      '/network/lines/recalculate_lengths',
    ).then(r => r.data),
  // Writes the previewed impedances for an explicit list of lines. The only
  // write path for a rescale — see RescaleDialog / MapCanvas's applyRescale.
  rescaleImpedances: (lines: Array<{ name: string; r: number; x: number; b: number }>) =>
    client.post<{ updated: number; skipped: Array<{ name: string; reason: string }> }>(
      '/network/lines/rescale_impedances', { lines },
    ).then(r => r.data),

  // Links
  getLinks: () => client.get<Link[]>('/network/links').then(r => r.data),
  createLink: (l: Partial<Link>) => client.post('/network/links', l),
  updateLink: (name: string, l: Partial<Link>) => client.put(`/network/links/${encodeURIComponent(name)}`, l),
  deleteLink: (name: string) => client.delete(`/network/links/${encodeURIComponent(name)}`),

  // Generators
  getGenerators: () => client.get<Generator[]>('/network/generators').then(r => r.data),
  createGenerator: (g: Partial<Generator>) => client.post('/network/generators', g),
  updateGenerator: (name: string, g: Partial<Generator>) => client.put(`/network/generators/${encodeURIComponent(name)}`, g),
  deleteGenerator: (name: string) => client.delete(`/network/generators/${encodeURIComponent(name)}`),

  // Storage Units
  getStorageUnits: () => client.get<StorageUnit[]>('/network/storage_units').then(r => r.data),
  createStorageUnit: (s: Partial<StorageUnit>) => client.post('/network/storage_units', s),
  updateStorageUnit: (name: string, s: Partial<StorageUnit>) => client.put(`/network/storage_units/${encodeURIComponent(name)}`, s),
  deleteStorageUnit: (name: string) => client.delete(`/network/storage_units/${encodeURIComponent(name)}`),

  // Stores
  getStores: () => client.get<Store[]>('/network/stores').then(r => r.data),
  createStore: (s: Partial<Store>) => client.post('/network/stores', s),
  updateStore: (name: string, s: Partial<Store>) => client.put(`/network/stores/${encodeURIComponent(name)}`, s),
  deleteStore: (name: string) => client.delete(`/network/stores/${encodeURIComponent(name)}`),

  // Loads
  getLoads: () => client.get<Load[]>('/network/loads').then(r => r.data),
  createLoad: (l: Partial<Load>) => client.post('/network/loads', l),
  updateLoad: (name: string, l: Partial<Load>) => client.put(`/network/loads/${encodeURIComponent(name)}`, l),
  deleteLoad: (name: string) => client.delete(`/network/loads/${encodeURIComponent(name)}`),

  // Transformers
  getTransformers: () => client.get<Transformer[]>('/network/transformers').then(r => r.data),
  getTransformerTypes: () => client.get<TransformerType[]>('/network/transformers/types').then(r => r.data),
  createTransformer: (t: Partial<Transformer>) => client.post('/network/transformers', t),
  updateTransformer: (name: string, t: Partial<Transformer>) =>
    client.put(`/network/transformers/${encodeURIComponent(name)}`, t),
  deleteTransformer: (name: string) => client.delete(`/network/transformers/${encodeURIComponent(name)}`),

  // Snapshots
  getSnapshots: () => client.get<SnapshotInfo>('/network/snapshots').then(r => r.data),
  setSnapshots: (cfg: { start: string; end: string; freq: string; weightings?: number }) =>
    client.post('/network/snapshots', cfg),
  // Representative-week sampling — picks `n_weeks` random ISO calendar weeks
  // per month from an uploaded full-year hourly profile and re-indexes the
  // network to those weeks, with snapshot_weightings scaled per month so
  // results still aggregate to a full year. Works for flat + multi-period.
  sampleWeeks: (body: { n_weeks: number; seed?: number }) =>
    client.post<{
      count: number; n_weeks: number; seed: number | null
      multi_period: boolean; timesteps_per_period: number
      weeks: Array<{
        month: number; iso_year: number; iso_week: number
        start: string; end: string; weight: number
      }>
    }>('/network/snapshots/sample_weeks', body).then(r => r.data),
  // Per-row snapshot weighting editor. `all` sets every row uniformly
  // (representative-day pattern: 24 hours × weight=30 = 1 month). `updates`
  // allows fine-grained edits keyed by ISO timestamp or integer position.
  // Returns the post-update weightings.
  updateSnapshotWeightings: (body: {
    all?: number
    updates?: Record<string, { objective?: number; generators?: number; stores?: number }>
  }) => client.patch<{ count: number; weightings: Array<Record<string, unknown>> }>(
    '/network/snapshots/weightings', body,
  ).then(r => r.data),

  // Bulk weights via CSV — used for hourly/8760-row horizons where the inline
  // table editor isn't practical. The download dumps the current weightings;
  // the upload accepts the same shape back (snapshot column + one or more of
  // objective/generators/stores).
  downloadSnapshotWeightingsCsv: () =>
    client.get('/network/snapshots/weightings.csv', { responseType: 'blob' })
      .then(r => r.data as Blob),
  uploadSnapshotWeightingsCsv: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<{ applied: number; skipped: number; columns: string[] }>(
      '/network/snapshots/weightings.csv', fd,
    ).then(r => r.data)
  },

  // Global constraints (network-wide policy: CO2 cap, capacity-expansion limits, ...)
  getGlobalConstraints: () =>
    client.get<Array<Record<string, unknown>>>('/network/global_constraints').then(r => r.data),
  createGlobalConstraint: (body: {
    name: string; type: string; sense: string; constant: number
    carrier_attribute?: string; carrier?: string; investment_period?: number
  }) => client.post<{ name: string }>('/network/global_constraints', body).then(r => r.data),
  updateGlobalConstraint: (name: string, body: {
    name: string; type: string; sense: string; constant: number
    carrier_attribute?: string; carrier?: string; investment_period?: number
  }) => client.put<{ name: string }>(
    `/network/global_constraints/${encodeURIComponent(name)}`, body,
  ).then(r => r.data),
  deleteGlobalConstraint: (name: string) =>
    client.delete(`/network/global_constraints/${encodeURIComponent(name)}`),

  // Investment periods (multi-period planning)
  getInvestmentPeriods: () =>
    client.get<{ periods: number[]; weightings: Array<Record<string, number | string>> }>(
      '/network/investment_periods',
    ).then(r => r.data),
  setInvestmentPeriods: (body: {
    periods: number[]
    objective_weightings?: number[]
    years_weightings?: number[]
  }) => client.post('/network/investment_periods', body),

  // Per-period weighting editor — partial PATCH avoids re-uploading the full
  // periods list for a single-cell tweak. Body matches the snapshot-weightings
  // pattern: {all_years?, all_objective?, updates?: {<period>: {years?, objective?}}}
  updateInvestmentPeriodWeightings: (body: {
    all_years?: number
    all_objective?: number
    updates?: Record<string, { years?: number; objective?: number }>
  }) => client.patch<{ periods: number[]; weightings: Array<Record<string, number | string>> }>(
    '/network/investment_period_weightings', body,
  ).then(r => r.data),

  // Build a (period, timestep) MultiIndex snapshot index. Either pass `start`/
  // `end`/`freq` for the same operational year under every period, or
  // `per_period` to give each period its own DatetimeIndex.
  setMultiPeriodSnapshots: (body: {
    periods: number[]
    start?: string
    end?: string
    freq?: string
    per_period?: Array<{ start: string; end: string; freq?: string }>
  }) => client.post<{ count: number; periods: number[]; rows_per_period: number[] }>(
    '/network/snapshots/multi_period', body,
  ).then(r => r.data),

  // Meta
  getMeta: () => client.get<NetworkMeta>('/network/meta').then(r => r.data),
  updateMeta: (m: { name: string }) => client.put('/network/meta', m),
  resetNetwork: () => client.post('/network/reset'),

  // Per-period capacity bounds for multi-investment-period expansion. The
  // backend expands one asset into N vintage rows (one per period) at solve
  // time, each with its own p_nom_min / p_nom_max. Saved bounds survive
  // save/reload via n.meta.
  listVintageBounds: () => client.get<{
    bounds: Record<string, Record<string, Record<string, { p_nom_min?: number; p_nom_max?: number }>>>
    supported_components: string[]
  }>('/network/vintage_bounds').then(r => r.data),
  updateVintageBounds: (component_class: string, name: string, bounds: Record<string, { p_nom_min?: number | null; p_nom_max?: number | null }>) =>
    client.put<{ name: string; component_class: string; bounds: Record<string, { p_nom_min?: number; p_nom_max?: number }> }>(
      `/network/vintage_bounds/${encodeURIComponent(component_class)}/${encodeURIComponent(name)}`,
      { bounds },
    ).then(r => r.data),
  deleteVintageBounds: (component_class: string, name: string) =>
    client.delete<{ name: string; component_class: string; removed: boolean }>(
      `/network/vintage_bounds/${encodeURIComponent(component_class)}/${encodeURIComponent(name)}`,
    ).then(r => r.data),

  // Remove vintage rows that leaked through `_capture_and_drop_vintages` on
  // a previous solve. Pattern-matches the `{parent}@{year}` naming
  // convention used by the vintage expansion. Safe to call on a clean
  // network — returns total_removed=0.
  cleanupOrphanVintages: () =>
    client.post<{ total_removed: number; by_class: Record<string, string[]> }>(
      '/network/vintage_bounds/_cleanup_orphans',
    ).then(r => r.data),

  // Per-vintage solve results — populated at the end of an LP run for every
  // asset whose vintage_bounds were applied. Shape matches the backend's
  // n.meta["vintage_results"]: keyed by component class, then parent name.
  listVintageResults: () => client.get<{
    results: Record<string, Record<string, {
      capacity_field: string
      initial_capacity: number
      periods: Array<{ build_year: number; p_nom_opt: number; p_nom_min: number; p_nom_max: number | null }>
    }>>
  }>('/network/vintage_results').then(r => r.data),

  // Bulk update — sets the same field(s) on N components in one transaction.
  // Refuses the whole batch if any name is unknown, so the caller can rely on
  // partial-failure-safety. component_class must be a PyPSA class name like
  // "Generator" / "Bus" / "StorageUnit".
  // Two body forms (spec D9): names+updates applies one value per column to
  // every named row; rows carries a per-row patch, which is what a row-by-row
  // paste needs. Send one or the other, never both — the backend 400s on both.
  bulkUpdate: (body: {
    component_class: string
    names?: string[]
    updates?: Record<string, unknown>
    rows?: { name: string; updates: Record<string, unknown> }[]
  }) =>
    client.patch<{ updated: number; fields: string[] }>('/network/_bulk', body)
      .then(r => r.data),

  // Undo. /undo/info is a high-frequency poll — keep its timeout tight so a
  // wedged backend doesn't tie up axios sockets for 30s and bring down the UI.
  undoInfo: () => client.get<{ depth: number }>('/network/undo/info', { timeout: 5000 }).then(r => r.data),
  undo: () => client.post<{ undone: boolean; remaining: number }>('/network/undo').then(r => r.data),

  // Attribute catalog (spec D3/D24). Class-level metadata; cached forever by
  // hooks/useCatalog.ts under a deliberately unscoped key.
  getCatalog: (component: string) =>
    client.get<CatalogPayload>(`/network/catalog/${encodeURIComponent(component)}`).then(r => r.data),

  // Time series
  listTimeseries: () => client.get<TimeseriesInfo[]>('/network/timeseries').then(r => r.data),
  getTimeseries: (component: string, attribute: string, columns?: string[]) =>
    client.get<TimeseriesData>(`/network/timeseries/${component}/${attribute}`, {
      params: columns?.length ? { columns: columns.join(',') } : undefined,
    }).then(r => r.data),
  // `period` (optional) — when the network has MultiIndex snapshots, pin
  // this upload to a specific investment period. Subsequent uploads with a
  // different `period` for the same column stitch together; omitting it
  // broadcasts the same data under every period via level-1 lookup.
  uploadTimeseries: (component: string, attribute: string, file: File, period?: number) => {
    const fd = new FormData()
    fd.append('file', file)
    const periodQs = period !== undefined && Number.isFinite(period) ? `&period=${period}` : ''
    return client.post(`/network/timeseries/upload?component=${component}&attribute=${attribute}${periodQs}`, fd)
  },

  // Load profiles
  getLoadProfiles: () =>
    client.get<Record<string, LoadProfileMeta>>('/network/loads/profiles').then(r => r.data),
  // Section-aware template. Pass `section` to filter+shape, or `loadName` for a
  // single-column file. Both empty = every load with each load's own shape.
  // Horizon: by default the simulation snapshots; pass `start`/`end`/`freq` to
  // override (then the backend ignores `useSnapshots`).
  downloadLoadTemplate: (opts?: {
    section?: LoadSection
    loadName?: string
    start?: string
    end?: string
    freq?: string
    useSnapshots?: boolean
  }) => {
    const params: Record<string, string> = {}
    if (opts?.section) params.section = opts.section
    if (opts?.loadName) params.load_name = opts.loadName
    if (opts?.start) params.start = opts.start
    if (opts?.end) params.end = opts.end
    if (opts?.freq) params.freq = opts.freq
    if (opts?.useSnapshots === false) params.use_snapshots = 'false'
    return client.get('/network/loads/template', {
      params: Object.keys(params).length ? params : undefined,
      responseType: 'blob',
    }).then(r => r.data as Blob)
  },
  uploadLoadProfile: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<{ matched: string[]; unmatched: string[]; rows: number; snapshot_count: number }>(
      '/network/loads/upload_profile', fd
    ).then(r => r.data)
  },
  // Sum of load p_set across the chosen loads. Either `names` (explicit) or
  // `section` (every load in the section). `names` takes precedence when both
  // are supplied.
  getLoadAggregate: (opts?: { section?: LoadSection; names?: string[] }) => {
    const params: Record<string, string> = {}
    if (opts?.names?.length) params.names = opts.names.join(',')
    if (opts?.section) params.section = opts.section
    return client.get<LoadAggregate>('/network/loads/aggregate', {
      params: Object.keys(params).length ? params : undefined,
    }).then(r => r.data)
  },

  // Generator profiles
  getGeneratorProfiles: () =>
    client.get<Record<string, GeneratorProfileMeta>>('/network/generators/profiles').then(r => r.data),
  downloadGeneratorTemplate: (
    category: string,
    attribute = 'p_max_pu',
    horizon?: { start?: string; end?: string; freq?: string; useSnapshots?: boolean },
    name?: string,  // when set, single-column template for that generator
  ) => {
    const params: Record<string, string> = { category, attribute }
    if (name) params.name = name
    if (horizon?.start) params.start = horizon.start
    if (horizon?.end) params.end = horizon.end
    if (horizon?.freq) params.freq = horizon.freq
    if (horizon?.useSnapshots === false) params.use_snapshots = 'false'
    return client.get('/network/generators/template', { params, responseType: 'blob' })
      .then(r => r.data as Blob)
  },
  uploadGeneratorProfile: (file: File, attribute = 'p_max_pu') => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<{ matched: string[]; unmatched: string[]; rows: number; snapshot_count: number }>(
      `/network/generators/upload_profile?attribute=${attribute}`, fd
    ).then(r => r.data)
  },

  // Link profiles
  getLinkProfiles: () =>
    client.get<Record<string, LinkProfileMeta>>('/network/links/profiles').then(r => r.data),
  downloadLinkTemplate: (
    attribute = 'p_max_pu',
    horizon?: { start?: string; end?: string; freq?: string; useSnapshots?: boolean },
    name?: string,  // when set, single-column template for that link
  ) => {
    const params: Record<string, string> = { attribute }
    if (name) params.name = name
    if (horizon?.start) params.start = horizon.start
    if (horizon?.end) params.end = horizon.end
    if (horizon?.freq) params.freq = horizon.freq
    if (horizon?.useSnapshots === false) params.use_snapshots = 'false'
    return client.get('/network/links/template', { params, responseType: 'blob' })
      .then(r => r.data as Blob)
  },
  uploadLinkProfile: (file: File, attribute = 'p_max_pu') => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<{ matched: string[]; unmatched: string[]; rows: number; snapshot_count: number }>(
      `/network/links/upload_profile?attribute=${attribute}`, fd
    ).then(r => r.data)
  },

  // Delete an uploaded time-series profile. `component` is the PyPSA `_t`
  // namespace ('loads' / 'generators' / 'links' / ...). `name` omitted ⇒
  // drop ALL profiles under (component, attribute, *) — used for the
  // future "Clear all on this tab" gesture.
  deleteTimeseries: (opts: { component: string; attribute: string; name?: string }) => {
    const params: Record<string, string> = {
      component: opts.component,
      attribute: opts.attribute,
    }
    if (opts.name) params.name = opts.name
    return client.delete<{ deleted: string[]; component: string; attribute: string; snapshot_count: number }>(
      '/network/timeseries', { params }
    ).then(r => r.data)
  },

  // ── Clustering ────────────────────────────────────────────────────────────
  // Pre-LP topology reduction. The request shape mirrors PyPSA's clustering
  // function signatures (busmap_by_{kmeans,hac,greedy_modularity,stubs}) plus
  // two "preset" modes (zone, region) that build the busmap from existing bus
  // columns (country, sub_network) without running an algorithm. The backend
  // returns the post-cluster bus/line counts so the caller can confirm what
  // it reduced to.
  applyClustering: (body: {
    mode: 'nodal' | 'zone' | 'region' | 'custom'
    algorithm?: 'kmeans' | 'hac' | 'greedy_modularity' | 'stubs'
    n_clusters?: number
    weighting?: 'uniform' | 'load'
    kmeans?: { n_init: number; max_iter: number; tol: number; random_state: number }
    hac?: { affinity: string; linkage: string; feature_source: string }
    stubs?: { matching_attrs: string[] }
  }) => client.post<{ bus_count: number; line_count: number; message: string }>(
    '/network/cluster', body,
  ).then(r => r.data),
}
