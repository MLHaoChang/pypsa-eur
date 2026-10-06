// Edge Investment Case commercial clients (IC P3 WP3.5): the Library, the
// value-flow config (its own route, If-Match always sent), commercial sub-tree
// writes through the solver-config route, and the commercial results.
import client from './client'
import type {
  AssetOwnership, CommercialConfig, LibraryItemRef, LibraryRef, SolverConfig, Tariff,
  ValueFlowConfig, ValueFlowsState,
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
/** No (valid) commercial config yet: set it up (poc_link) before editing its parts. */
export class NoCommercialConfigError extends Error {
  constructor(message: string) { super(message); this.name = 'NoCommercialConfigError' }
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
  if (status === 409 && (code === 'no_commercial_config' || code === 'commercial_config_invalid')) {
    throw new NoCommercialConfigError(message ?? 'set up the commercial config first')
  }
  throw e
}

/** Result requests never toast (the tab shows the state itself), so their
 *  callers MUST render an error state: nothing is toasted or logged. 409s become
 *  the typed errors. */
function resultError(e: unknown): never {
  return typed(e)
}
const QUIET = { skipErrorToast: true } as const

const enc = encodeURIComponent

export const libraryApi = {
  listItems: (kind: LibraryItemKind) =>
    client.get<LibraryItemRef[]>(`/library/items/${kind}`).then(r => r.data),
  getItem: <P = Record<string, unknown>>(kind: LibraryItemKind, name: string, version?: number) =>
    client.get<LibraryItem<P>>(`/library/items/${kind}/${enc(name)}`,
      { params: version === undefined ? {} : { version } }).then(r => r.data),
  putItem: (kind: LibraryItemKind, name: string, payload: unknown, meta: Record<string, unknown> = {}) =>
    client.put<LibraryItemRef>(`/library/items/${kind}/${enc(name)}`, { payload, meta }, QUIET)
      .then(r => r.data),
  importUrdb: (body: UrdbImportRequest) =>
    client.post<UrdbImportResult>('/library/items/tariff/import_urdb', body, QUIET).then(r => r.data),
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
    return client.post<LibraryRef>('/library/series/upload', form, QUIET).then(r => r.data)
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
    return client.post<MeterDataResult>('/library/meter_data', form, QUIET).then(r => r.data)
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
    /** Null when the party has a line of unknown amount (never a partial sum). */
    by_participant: Record<string, { paid: number | null; received: number | null;
                                     net: number | null;
                                     by_stream: Record<string, number | null> }>
    sankey: { nodes: Array<{ id: string; label: string; side: 'payer' | 'payee';
                             internal: boolean }>
              links: Array<{ source: string; target: string; value: number; stream: string }> }
    conservation: { ok: boolean | null; checks: ValueFlowCheck[] }
    /** Model-only amounts shown beside the ledger, never lines (DSR slack, VoLL). */
    disclosures: Record<string, number | null>
  }>
  conservation_ok?: boolean | null
  flags?: string[]
  notes?: string[]
  provenance?: Record<string, unknown>
}
/** GET /simulation/value_flows/designer (IC P3 WP3.6). */
export interface DesignerContext {
  site_party: string
  assets: Array<{ component: AssetOwnership['component']; name: string; bus: string
                  side: 'site' | 'grid' | 'unclassified'; ownable: boolean; flags: string[]
                  carrier: string }>
  tariff_items: Array<{ id: string; kind: string; default_payee: string; stream: string }>
  contract_parties: string[]
  group_members: string[]
  default_externals: string[]
}
export interface TemplateResult {
  config: ValueFlowConfig
  draft_contracts: Array<Record<string, unknown>>
  notes: string[]
}

const orNull = <T,>(r: { status: number; data: T }) => (r.status === 204 ? null : r.data)

export const commercialApi = {
  getValueFlows: () =>
    client.get<ValueFlowsState>('/simulation/commercial/value_flows', QUIET)
      .then(r => r.data, typed),
  /** `ifMatch` is the digest of the GET this edit started from — always sent. */
  putValueFlows: (valueFlows: ValueFlowConfig | null, ifMatch: string) =>
    client.put<ValueFlowsState>('/simulation/commercial/value_flows',
      { value_flows: valueFlows },
      { headers: { 'If-Match': ifMatch }, skipErrorToast: true })
      .then(r => r.data, typed),
  buildTemplate: (template: NonNullable<ValueFlowConfig['template']>) =>
    client.post<TemplateResult>('/simulation/value_flows/template', { template }, QUIET)
      .then(r => r.data),
  /**
   * Replace commercial sub-trees (tariff, contracts, connection, …) on the
   * LATEST stored config. `value_flows` is never sent: the server keeps the
   * stored value, so a concurrent participants edit is not lost (IC P3 C7).
   * The GET and the PUT are two requests, not one step: two editors saving
   * different sub-trees at the same instant can still race (accepted by the
   * plan); every save re-binds the whole commercial config, so any editor can
   * meet `library_ref_stale` / `import_tariff_ref_conflict` 409s. Callers
   * invalidate the results queries after a save.
   */
  saveCommercial: async (patch: Partial<Omit<CommercialConfig, 'value_flows'>>) => {
    const current = (await client.get<SolverConfig>('/simulation/solver_config')).data
    const base = { ...(current.commercial ?? {}) } as Partial<CommercialConfig>
    delete base.value_flows
    const next = { ...base, ...patch } as Partial<CommercialConfig>
    delete (next as { value_flows?: unknown }).value_flows
    if (!next.poc_link) {
      throw new NoCommercialConfigError('set the commercial config’s poc_link before editing its parts')
    }
    return client.put<SolverConfig>('/simulation/solver_config', { commercial: next })
      .then(r => r.data, typed)
  },
  /** Append contracts (a template's priced drafts, IC P3 WP3.6) to the LATEST
   *  stored list, through the solver-config route like `saveCommercial`. */
  appendContracts: async (contracts: Array<Record<string, unknown>>) => {
    const current = (await client.get<SolverConfig>('/simulation/solver_config')).data
    const have = (current.commercial?.contracts ?? []) as unknown[]
    return commercialApi.saveCommercial({ contracts: [...have, ...contracts] as never })
  },
  getBilling: () =>
    client.get<BillingPayload>('/results/billing', QUIET).then(orNull, resultError),
  getCfeScore: () =>
    client.get<CfeScorePayload>('/results/cfe_score', QUIET).then(orNull, resultError),
  getValueFlowsResult: () =>
    client.get<ValueFlowsPayload>('/results/value_flows', QUIET).then(orNull, resultError),
  /** What the participants designer offers (IC P3 WP3.6). */
  getDesigner: () =>
    client.get<DesignerContext>('/simulation/value_flows/designer', QUIET)
      .then(r => r.data, typed),
  previewBilling: (tariff: Tariff) =>
    client.post<BillingPayload>('/results/billing/preview', { tariff }, QUIET)
      .then(orNull, resultError),
  /** The stored commercial config (the editors' starting point), or null. */
  getCommercial: () =>
    client.get<SolverConfig>('/simulation/solver_config')
      .then(r => (r.data.commercial ?? null) as CommercialConfig | null),
}
