// Edge Investment Case commercial clients (IC P3 WP3.5): the Library, the
// value-flow config (its own route, If-Match always sent), commercial sub-tree
// writes through the solver-config route, and the commercial results.
import client from './client'
import type {
  CommercialConfig, LibraryItemRef, LibraryRef, SolverConfig, Tariff, ValueFlowConfig,
  ValueFlowsState,
} from './types'

export type LibraryItemKind = LibraryItemRef['kind']

export interface LibraryItem<P = Record<string, unknown>> {
  ref: LibraryItemRef
  payload: P
  meta: Record<string, unknown>
}
export interface UrdbImportRequest {
  urdb_response: Record<string, unknown>
  name: string
  cyclic_year?: boolean
  accept_partial?: boolean
  valid_from?: string | null
  tariff_id?: string | null
  jurisdiction?: string | null
}
export interface UrdbImportResult {
  ref: LibraryItemRef
  notes: string[]
  refusals: Array<{ field: string; reason: string }>
  unsupported_fields: string[]
}
export interface SeriesDetail {
  ref: LibraryRef
  timestamps: string[]
  values: number[]
  timezone: string | null
  meta: Record<string, unknown>
}
export interface MeterDataResult {
  ref: LibraryRef
  meter_history_peaks_kw: Record<string, number>
  meter_history_energy_kwh: Record<string, number>
  settlement: string
  notes: string[]
  help: string
}

/** A 412: the value-flow config changed since it was read (reload, re-apply). */
export class StaleEditError extends Error {
  constructor(message: string) { super(message); this.name = 'StaleEditError' }
}
/** A 409 `solver_in_flight`: change the config after the solve. */
export class SolverInFlightError extends Error {
  constructor(message: string) { super(message); this.name = 'SolverInFlightError' }
}

function detailOf(e: unknown): { status?: number; code?: string; message?: string } {
  const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const d = r?.data?.detail
  const obj = (d && typeof d === 'object' && !Array.isArray(d)) ? d as Record<string, unknown> : {}
  return { status: r?.status, code: obj.code as string | undefined,
           message: (obj.message as string | undefined) ?? (typeof d === 'string' ? d : undefined) }
}

function typed(e: unknown): never {
  const { status, code, message } = detailOf(e)
  if (status === 412) throw new StaleEditError(message ?? 'changed since it was read')
  if (status === 409 && code === 'solver_in_flight') {
    throw new SolverInFlightError(message ?? 'a solve is running')
  }
  throw e
}

const enc = encodeURIComponent

export const libraryApi = {
  listItems: (kind: LibraryItemKind) =>
    client.get<LibraryItemRef[]>(`/library/items/${kind}`).then(r => r.data),
  getItem: <P = Record<string, unknown>>(kind: LibraryItemKind, name: string, version?: number) =>
    client.get<LibraryItem<P>>(`/library/items/${kind}/${enc(name)}`,
      { params: version === undefined ? {} : { version } }).then(r => r.data),
  putItem: (kind: LibraryItemKind, name: string, payload: unknown, meta: Record<string, unknown> = {}) =>
    client.put<LibraryItemRef>(`/library/items/${kind}/${enc(name)}`, { payload, meta })
      .then(r => r.data),
  importUrdb: (body: UrdbImportRequest) =>
    client.post<UrdbImportResult>('/library/items/tariff/import_urdb', body).then(r => r.data),
  listSeries: () => client.get<LibraryRef[]>('/library/series').then(r => r.data),
  getSeries: (name: string, version?: number) =>
    client.get<SeriesDetail>(`/library/series/${enc(name)}`,
      { params: version === undefined ? {} : { version } }).then(r => r.data),
  uploadSeries: (file: File, opts: { name: string; timezone?: string | null; source?: string }) => {
    const form = new FormData()
    form.append('file', file)
    form.append('name', opts.name)
    if (opts.timezone) form.append('timezone', opts.timezone)
    if (opts.source) form.append('source', opts.source)
    return client.post<LibraryRef>('/library/series/upload', form).then(r => r.data)
  },
  uploadMeterData: (file: File, opts: { name: string; unit: string; settlement?: string;
                                        label?: string; timezone?: string | null }) => {
    const form = new FormData()
    form.append('file', file)
    form.append('name', opts.name)
    form.append('unit', opts.unit)
    if (opts.settlement) form.append('settlement', opts.settlement)
    if (opts.label) form.append('label', opts.label)
    if (opts.timezone) form.append('timezone', opts.timezone)
    return client.post<MeterDataResult>('/library/meter_data', form).then(r => r.data)
  },
}

/** Payload of GET /results/billing (IC P2 WP2.5); periods keyed "_" or the year. */
export interface BillingPayload {
  summary: Record<string, { total: number | null; total_supported: number | null;
                            per_item: Record<string, number | null> } | null>
  flags: string[]
  contracts: { lines: Array<{ period: number | null; contract_id: string; payer: string | null;
                              payee: string | null; value_stream: string;
                              quantity_mwh: number | null; amount: number | null;
                              flags: string[] }>
               flags: string[]; retail: Record<string, [string, string] | null> }
  gap_summary: { gates: string[]; periods: Record<string, Record<string, {
    lp: number | null; billed: number | null; unattributed_pct: number | null }>> }
  per_period: Record<string, Record<string, unknown> | null>
  gap: Record<string, unknown>
  provenance: Record<string, unknown>
}
/** Payload of GET /results/cfe_score (IC P2 WP2.5). */
export interface CfeScorePayload {
  per_period: Record<string, Record<string, unknown>>
  flags: string[]
  notes: string[]
  clean_carriers: string[]
  site_buses: string[]
  site_loads: string[]
  onsite_clean_generators: string[]
}
/** One ledger line (IC P3 WP3.1): payer → payee, amount ≥ 0 or null. */
export interface ValueFlowLine {
  period: string
  payer: string | null
  payee: string | null
  value_stream: string
  source: string
  source_id: string
  amount: number | null
  basis: 'cash' | 'annuity' | 'model_only'
  tariff_item: string | null
  tariff_item_kind: string | null
  contract_id: string | null
  asset: string | null
  flags: string[]
}
export interface ValueFlowCheck { name: string; ok: boolean | null; detail: unknown }
/** Payload of GET /results/value_flows (IC P3 WP3.4). */
export interface ValueFlowsPayload {
  status: 'ok' | 'not_established' | 'value_flows_invalid'
  reason?: string
  participants?: Array<{ id: string; name: string; role: string }>
  externals?: string[]
  template?: string
  template_version?: string | null
  periods?: Record<string, {
    lines: ValueFlowLine[]
    by_participant: Record<string, { paid: number; received: number; net: number;
                                     by_stream: Record<string, number> }>
    sankey: { nodes: Array<{ id: string; label: string; side: 'payer' | 'payee';
                             internal: boolean }>
              links: Array<{ source: string; target: string; value: number; stream: string }> }
    conservation: { ok: boolean | null; checks: ValueFlowCheck[] }
  }>
  conservation_ok?: boolean | null
  flags?: string[]
  notes?: string[]
  provenance?: Record<string, unknown>
}
export interface TemplateResult {
  config: ValueFlowConfig
  draft_contracts: Array<Record<string, unknown>>
  notes: string[]
}

const orNull = <T,>(r: { status: number; data: T }) => (r.status === 204 ? null : r.data)

export const commercialApi = {
  getValueFlows: () =>
    client.get<ValueFlowsState>('/simulation/commercial/value_flows').then(r => r.data),
  /** `ifMatch` is the digest of the GET this edit started from — always sent. */
  putValueFlows: (valueFlows: ValueFlowConfig | null, ifMatch: string) =>
    client.put<ValueFlowsState>('/simulation/commercial/value_flows',
      { value_flows: valueFlows },
      { headers: { 'If-Match': ifMatch }, skipErrorToast: true })
      .then(r => r.data, typed),
  buildTemplate: (template: NonNullable<ValueFlowConfig['template']>) =>
    client.post<TemplateResult>('/simulation/value_flows/template', { template })
      .then(r => r.data),
  /**
   * Replace commercial sub-trees (tariff, contracts, connection, …) on the
   * LATEST stored config. `value_flows` is never sent: the server keeps the
   * stored value, so a concurrent participants edit is not lost (IC P3 C7).
   */
  saveCommercial: async (patch: Partial<Omit<CommercialConfig, 'value_flows'>>) => {
    const current = (await client.get<SolverConfig>('/simulation/solver_config')).data
    const base = { ...(current.commercial ?? {}) } as Partial<CommercialConfig>
    delete base.value_flows
    const next = { ...base, ...patch } as Partial<CommercialConfig>
    delete (next as { value_flows?: unknown }).value_flows
    if (!next.poc_link) {
      throw new Error('set the commercial config’s poc_link before editing its parts')
    }
    return client.put<SolverConfig>('/simulation/solver_config', { commercial: next })
      .then(r => r.data, typed)
  },
  getBilling: () => client.get<BillingPayload>('/results/billing').then(orNull),
  getCfeScore: () => client.get<CfeScorePayload>('/results/cfe_score').then(orNull),
  getValueFlowsResult: () => client.get<ValueFlowsPayload>('/results/value_flows').then(orNull),
  previewBilling: (tariff: Tariff) =>
    client.post<BillingPayload>('/results/billing/preview', { tariff }).then(orNull),
}
