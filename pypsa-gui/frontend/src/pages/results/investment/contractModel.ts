// The contracts editor's model (IC P3 WP3.7c): one field spec per P2 contract
// type, so the form is generated and every type round-trips to its model
// unchanged. Allowed combinations are the SERVER's to judge (its 422s are
// mapped to the contract they name); the specs only say which fields exist.
import type { CommercialContract } from '../../../api/types'

export type ContractType = CommercialContract['type']
export type FieldKind = 'text' | 'number' | 'int' | 'party' | 'ids' | 'select' | 'bool' | 'series'
export interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  options?: string[]
  /** Required by the P2 model (a blank required field is left for the server to refuse). */
  required?: boolean
  /** Shown only when this holds on the contract. */
  when?: (c: Record<string, unknown>) => boolean
}

const common = (extra: FieldSpec[]): FieldSpec[] => [
  { key: 'id', label: 'id', kind: 'text', required: true },
  ...extra,
  { key: 'base_year', label: 'base year (indexation)', kind: 'int' },
]

export const CONTRACT_FIELDS: Record<ContractType, FieldSpec[]> = {
  ppa: common([
    { key: 'kind', label: 'kind', kind: 'select', required: true,
      options: ['pay_as_produced', 'baseload', 'as_consumed_btm', 'sleeved'] },
    { key: 'seller', label: 'seller', kind: 'party', required: true },
    { key: 'buyer', label: 'buyer', kind: 'party', required: true },
    { key: 'asset_ids', label: 'assets (generators)', kind: 'ids', required: true },
    { key: 'tenor_years', label: 'tenor (years)', kind: 'int', required: true },
    { key: 'pricing', label: 'pricing', kind: 'select', options: ['fixed', 'market_plus_premium'] },
    { key: 'price', label: 'price (per MWh)', kind: 'number', required: true },
    { key: 'premium_eur_per_mwh', label: 'premium (per MWh)', kind: 'number',
      when: c => c.pricing === 'market_plus_premium' },
    { key: 'reference_price', label: 'reference price series', kind: 'series' },
    { key: 'indexation_pct_per_year', label: 'indexation (% a year)', kind: 'number' },
    { key: 'floor', label: 'floor', kind: 'number' },
    { key: 'cap', label: 'cap', kind: 'number' },
    { key: 'volume_cap_mwh_per_year', label: 'volume cap (MWh a year)', kind: 'number' },
    { key: 'baseload_mw', label: 'baseload (MW)', kind: 'number', when: c => c.kind === 'baseload' },
    { key: 'sleeving_party', label: 'sleeving party', kind: 'party', when: c => c.kind === 'sleeved' },
    { key: 'sleeving_fee_eur_per_mwh', label: 'sleeving fee (per MWh)', kind: 'number',
      when: c => c.kind === 'sleeved' },
    { key: 'changes_dispatch', label: 'changes the dispatch (in the LP)', kind: 'bool' },
  ]),
  cfd: common([
    { key: 'generator_owner', label: 'generator owner', kind: 'party' },
    { key: 'counterparty', label: 'counterparty', kind: 'party' },
    { key: 'asset_ids', label: 'assets (generators)', kind: 'ids', required: true },
    { key: 'strike', label: 'strike (per MWh)', kind: 'number', required: true },
    { key: 'tenor_years', label: 'tenor (years)', kind: 'int', required: true },
    { key: 'reference_price', label: 'reference price series', kind: 'series' },
    { key: 'reference', label: 'reference', kind: 'select', options: ['interval', 'monthly_capture'] },
    { key: 'indexation_pct_per_year', label: 'indexation (% a year)', kind: 'number' },
    { key: 'suspend_on_negative_price', label: 'suspended at negative prices', kind: 'bool' },
  ]),
  dr: common([
    { key: 'counterparty', label: 'counterparty (pays)', kind: 'party' },
    { key: 'load_ids', label: 'loads', kind: 'ids' },
    { key: 'asset_ids', label: 'assets', kind: 'ids' },
    { key: 'contracted_mw', label: 'contracted MW', kind: 'number' },
    { key: 'availability_eur_per_mw_year', label: 'availability (per MW-year)', kind: 'number', required: true },
    { key: 'activation_eur_per_mwh', label: 'activation (per MWh)', kind: 'number', required: true },
    { key: 'max_events', label: 'max events', kind: 'int' },
    { key: 'max_duration_h', label: 'max duration (h)', kind: 'number' },
    { key: 'notice_h', label: 'notice (h)', kind: 'number' },
  ]),
  lease: common([
    { key: 'lessor', label: 'lessor', kind: 'party', required: true },
    { key: 'lessee', label: 'lessee', kind: 'party', required: true },
    { key: 'asset_ids', label: 'assets', kind: 'ids', required: true },
    { key: 'annual_payment', label: 'annual payment', kind: 'number', required: true },
    { key: 'tenor_years', label: 'tenor (years)', kind: 'int', required: true },
  ]),
  eaas: common([
    { key: 'provider', label: 'provider', kind: 'party', required: true },
    { key: 'customer', label: 'customer', kind: 'party', required: true },
    { key: 'asset_ids', label: 'assets', kind: 'ids', required: true },
    { key: 'fee_eur_per_mwh', label: 'fee (per MWh)', kind: 'number' },
    { key: 'fee_eur_per_year', label: 'fee (per year)', kind: 'number' },
    { key: 'tenor_years', label: 'tenor (years)', kind: 'int', required: true },
  ]),
  retail: common([
    { key: 'retailer', label: 'retailer', kind: 'party', required: true },
    { key: 'customer', label: 'customer', kind: 'party', required: true },
    { key: 'tariff_id', label: 'tariff id', kind: 'text', required: true },
    { key: 'tenor_years', label: 'tenor (years)', kind: 'int', required: true },
  ]),
}

export function blankContract(type: ContractType, id: string, site: string): CommercialContract {
  switch (type) {
    case 'ppa': return { type, id, kind: 'pay_as_produced', price: 0, tenor_years: 10,
                         seller: '', buyer: site, asset_ids: [] }
    case 'cfd': return { type, id, strike: 0, tenor_years: 10, asset_ids: [] }
    case 'dr': return { type, id, availability_eur_per_mw_year: 0, activation_eur_per_mwh: 0,
                        load_ids: [] }
    case 'lease': return { type, id, lessor: '', lessee: site, annual_payment: 0, tenor_years: 10,
                           asset_ids: [] }
    case 'eaas': return { type, id, provider: '', customer: site, tenor_years: 10, asset_ids: [] }
    case 'retail': return { type, id, retailer: '', customer: site, tariff_id: '', tenor_years: 1 }
  }
}

/** Set a field; a cleared optional field is removed (the model's default). */
export function setField(c: CommercialContract, spec: FieldSpec, value: unknown): CommercialContract {
  const out = { ...c } as Record<string, unknown>
  const empty = value === '' || value === null || value === undefined
    || (typeof value === 'number' && Number.isNaN(value))
  if (empty && !spec.required) delete out[spec.key]
  else out[spec.key] = empty ? (spec.kind === 'ids' ? [] : value === '' ? '' : null) : value
  return out as unknown as CommercialContract
}

export function nextContractId(contracts: CommercialContract[], type: ContractType): string {
  const taken = new Set(contracts.map(c => c.id))
  let n = contracts.length + 1
  while (taken.has(`${type}_${n}`)) n += 1
  return `${type}_${n}`
}

/** A 422 of the solver-config route by contract index (its `loc` names
 *  `contracts, <i>, …`), the rest under -1. */
export function contractErrors(detail: unknown): Map<number, string[]> {
  const out = new Map<number, string[]>()
  const list = Array.isArray(detail) ? detail as Array<{ loc?: Array<string | number>; msg?: string }> : []
  for (const e of list) {
    const loc = e.loc ?? []
    const i = loc.indexOf('contracts')
    const idx = i >= 0 && typeof loc[i + 1] === 'number' ? loc[i + 1] as number : -1
    const where = (idx >= 0 ? loc.slice(i + 2) : loc).join('.')
    const msg = `${where ? `${where}: ` : ''}${e.msg ?? 'invalid'}`
    out.set(idx, [...(out.get(idx) ?? []), msg])
  }
  if (!list.length && detail != null) {
    const d = detail as { message?: string }
    out.set(-1, [typeof detail === 'string' ? detail : String(d.message ?? 'refused')])
  }
  return out
}
