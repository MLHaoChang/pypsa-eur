// Business case clients (business-case 09/10): the line table's inputs (their own
// route, If-Match always sent), the preset, the table's options, and the run.
// Wire shapes mirror `models/business_case.py` and `services/business_case/runner.py`.
import client from './client'
import { SolverInFlightError, StaleEditError } from './commercial'

export { SolverInFlightError, StaleEditError }

export type LineKind = 'revenue' | 'opex' | 'capex'
export type DefaultColumn = 'price' | 'quantity' | 'life_years' | 'rate'

export interface CostDefault {
  key: string
  value: number
  unit: string
  source: string
  source_year: number
  currency?: string | null
  money_year?: number | null
  pack: string
  status: 'assumed'
  range_low?: number | null
  range_high?: number | null
}

export interface PriceHint {
  low: number
  high: number
  unit: string
  source: string
  year: number
  note?: string
}

export interface PresetRule {
  requires: string[]
  excludes: string[]
  price_includes_energy: boolean
}

export interface LineItem {
  name: string
  kind: LineKind
  category: string
  unit: string
  quantity_source: string
  quantity?: number | null
  quantity_by_year?: Record<string, number>
  price?: number | null
  price_by_year?: Record<string, number>
  money_year?: number | null
  escalation: string
  indexation?: number | null
  start_year?: number | null
  end_year?: number | null
  depreciation?: 'straight_line' | 'declining_balance' | null
  life_years?: number | null
  rate?: number | null
  refresh_years?: number | null
  note?: string
  assumed?: Partial<Record<DefaultColumn, CostDefault>>
  default_keys?: Partial<Record<DefaultColumn, string>>
  default_units?: Partial<Record<DefaultColumn, string>>
  preset_rule?: PresetRule | null
  linked_share?: number | null
  linked_share_by_year?: Record<string, number>
  hint?: PriceHint | null
}

export interface BusinessCaseInputs {
  preset?: string | null
  receivable_days?: number | null
  lines: LineItem[]
}

/** `BusinessCaseConfig`: the lines plus the run's parameters. */
export interface BusinessCaseConfig {
  inputs: BusinessCaseInputs
  kw_per_gpu?: number | null
  lhv_kwh_per_kg?: number | null
  capacity?: Record<string, number>
  capacity_unit?: string
  break_even_line?: string | null
}

export interface BusinessCaseInputsState {
  business_case: BusinessCaseConfig | null
  digest: string
  status: 'not_set' | 'ok' | 'business_case_inputs_invalid'
  message?: string
}

export interface OfferedRow {
  label: string
  units: string[]
  sources: string[]
  line: LineItem
}

export interface LinkedOutput {
  id: string
  label: string
  units: string[]
  derived: boolean
}

/** A typed refusal's wire form (G9): `field` is `lines[<i>].<column>`. */
export interface BusinessCaseRefusal {
  code: string
  refusal?: string
  field?: string
  message: string
}

export interface BusinessCaseOptions {
  template_id: string | null
  variants: Record<string, string>
  offered_rows: OfferedRow[]
  categories: Record<LineKind, string[]>
  escalations: string[]
  /** The units a line may take per quantity source (the backend's `allowed_units`):
   *  typed and share_of per kind; linked per output id, then per kind. */
  units_by_source: {
    typed: Record<LineKind, string[]>
    share_of: Record<LineKind, string[]>
    linked: Record<string, Record<LineKind, string[]>>
  }
  catalogue_version: string
  outputs: LinkedOutput[] | null
  outputs_reason: string | null
  /** The investment case's operating years, first and last; null without one. */
  horizon: [number, number] | null
  base_year: number | null
  refusals: BusinessCaseRefusal[] | null
  refusals_reason: string | null
}

export interface PresetResponse {
  template_id: string
  variants: Record<string, string>
  business_case: { inputs: BusinessCaseInputs } | null
}

/** One IC ledger line the business case carries (G2): "from the energy model". */
export interface LockedRow {
  stream: string
  key: string
  /** [year, share] per year a revenue line supersedes it (G3); share null = not established. */
  dropped: [number, number | null][]
  superseded_by: string[]
}

export interface BusinessCaseReport {
  currency?: string
  basis?: { id: string; label: string }
  locked?: LockedRow[]
  [key: string]: unknown
}

export interface BusinessCaseStudy {
  status: string
  stage?: string | null
  refusal?: string | null
  report?: BusinessCaseReport | null
  staleness?: { stale: boolean | null; reason?: string | null; [key: string]: unknown }
  [key: string]: unknown
}

/** A 422 from the inputs PUT or the preset GET, its detail kept for placement. */
export class BusinessCaseRejected extends Error {
  constructor(public detail: {
    code?: string; refusal?: string; field?: string; message?: string
    errors?: { loc: string[]; msg: string }[]
  }) {
    super(detail.message ?? detail.code ?? 'refused')
    this.name = 'BusinessCaseRejected'
  }
}

const QUIET = { skipErrorToast: true } as const

function detailOf(e: unknown): { status?: number; detail: Record<string, unknown> } {
  const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const d = r?.data?.detail
  const detail = (d && typeof d === 'object' && !Array.isArray(d)) ? d as Record<string, unknown>
    : typeof d === 'string' ? { message: d } : {}
  return { status: r?.status, detail }
}

function typed(e: unknown): never {
  const { status, detail } = detailOf(e)
  const message = detail.message as string | undefined
  if (status === 412) throw new StaleEditError(message ?? 'the business-case inputs changed since they were read')
  if (status === 409 && detail.code === 'solver_in_flight') {
    throw new SolverInFlightError(message ?? 'a solve is running')
  }
  if (status === 422) throw new BusinessCaseRejected(detail as BusinessCaseRejected['detail'])
  throw e
}

export const businessCaseApi = {
  getInputs: () =>
    client.get<BusinessCaseInputsState>('/results/business_case/inputs', QUIET)
      .then(r => r.data, typed),
  /** `ifMatch` is the digest of the GET this edit started from: always sent. */
  putInputs: (business_case: BusinessCaseConfig | null, ifMatch: string) =>
    client.put<BusinessCaseInputsState>('/results/business_case/inputs', { business_case },
      { headers: { 'If-Match': ifMatch }, skipErrorToast: true })
      .then(r => r.data, typed),
  getOptions: () =>
    client.get<BusinessCaseOptions>('/results/business_case/options', QUIET).then(r => r.data),
  /** The run's record and stored report; 204 (never run) → null. */
  getStudy: () =>
    client.get<BusinessCaseStudy>('/results/business_case', QUIET)
      .then(r => (r.status === 204 ? null : r.data)),
  /** No variant: the variants only (`business_case` null when the template has some). */
  getPreset: (variant?: string) =>
    client.get<PresetResponse>('/results/business_case/preset',
      { ...QUIET, params: variant ? { variant } : {} })
      .then(r => r.data, typed),
}
