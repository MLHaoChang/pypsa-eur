// The business case's ids in words (walk 2026-11-17 issue 06, RAW-NAMES-1): line
// categories, escalation classes and the P&L streams. A test pins each list to the
// backend's ids (`models/business_case.py`, `services/finance/cashflow.py`).

import { displayName } from '../../../utils/displayName'

export const CATEGORY_WORDS: Record<string, string> = {
  // revenue
  product_sale: 'Product sales',
  by_product: 'By-product sales',
  service_fee: 'Service fee',
  lease: 'Lease',
  capacity_payment: 'Capacity payment',
  tariff: 'Tariff',
  cost_recovery: 'Cost recovery (pass-through)',
  incentive: 'Incentive or premium',
  // opex
  staff: 'Staff',
  maintenance: 'Maintenance',
  insurance: 'Insurance',
  property_tax: 'Property tax',
  feedstock: 'Feedstock',
  consumables: 'Consumables',
  network: 'Network charges',
  land: 'Land lease',
  sga: 'Overheads (SG&A)',
  // capex
  facility: 'Facility (shell and M&E)',
  equipment: 'Equipment',
  it_hardware: 'IT hardware',
  other: 'Other',
}

/** Escalation classes: the finance-input rate each line moves with. */
export const ESCALATION_WORDS: Record<string, string> = {
  opex: 'Operating cost rate',
  fuel: 'Fuel price rate',
  tariff: 'Grid tariff rate',
  ppa: 'PPA price rate',
  export: 'Export price rate',
  capex: 'Capex rate',
  contract: 'Contract indexation (per line)',
}

/** The P&L streams: the ledger's and the business-case lines' (`bc_<category>`). */
export const STREAM_WORDS: Record<string, string> = {
  ancillary: 'Ancillary services',
  capex: 'Capital cost',
  certificates: 'Certificates',
  cfd_settlement: 'CfD settlement',
  demand_charge: 'Demand charge',
  dr_activation: 'Demand response activation',
  dr_availability: 'Demand response availability',
  eaas_fee: 'Energy-as-a-service fee',
  energy_export: 'Energy export',
  energy_import: 'Energy import',
  energy_wholesale: 'Wholesale market energy',
  fom: 'Fixed O&M (energy assets)',
  fuel: 'Fuel',
  lease: 'Lease',
  lost_production: 'Lost production',
  network_capacity: 'Network capacity charge',
  network_energy: 'Network energy charge',
  other: 'Other',
  ppa_settlement: 'PPA settlement',
  retail_fixed: 'Retail fixed charge',
  tax: 'Levies',
  vom: 'Variable O&M (energy assets)',
  byproduct_revenue: 'By-product revenue',
  capacity_revenue: 'Capacity revenue',
  lease_revenue: 'Lease revenue',
  other_revenue: 'Other revenue',
  power_passthrough: 'Power cost recovery',
  premium_revenue: 'Premium revenue',
  product_revenue: 'Product revenue',
  service_revenue: 'Service revenue',
  tariff_revenue: 'Tariff revenue',
  // the locked-row ids the options route serves
  bill: 'Energy bill',
  export: 'Export sales',
  ppa: 'PPA',
  cfd: 'CfD settlement',
}

const plain = (id: string) => id.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

export const categoryWords = (c: string) => CATEGORY_WORDS[c] ?? plain(c)
export const escalationWords = (x: string) => ESCALATION_WORDS[x] ?? plain(x)
/** A stream; `bc_<category>` reads its category's words. */
export function streamWords(id: string): string {
  if (STREAM_WORDS[id]) return STREAM_WORDS[id]
  const cat = id.startsWith('bc_') ? id.slice(3) : null
  return cat && CATEGORY_WORDS[cat] ? CATEGORY_WORDS[cat] : plain(id)
}

/** A public price range in another currency than the project's (walk 04): it is
 *  shown as published and never converted, so the row says so. Null when the two
 *  agree or either is unknown. */
export function hintCurrencyNote(hintUnit: string, projectCurrency: string | null): string | null {
  const cur = hintUnit.split('/', 1)[0]?.trim().toUpperCase()
  const proj = projectCurrency?.trim().toUpperCase()
  if (!cur || !proj || cur === proj || !/^[A-Z]{3}$/.test(cur)) return null
  return `In ${cur}, not converted: type the price in ${proj}, the project currency.`
}

const ID = /^[a-z0-9]+(?:[_-][a-z0-9]+)*$/
/** An id in its display name, with "ppa" read as PPA (`ppa_energy` → "PPA energy"). */
const idWords = (id: string) => displayName(id).replace(/\bPpa\b|\bppa\b/g, 'PPA')

/** A model-output label whose tail is a raw asset id ("Capacity in service: wind_farm")
 *  reads the asset's display name ("Capacity in service: Wind farm"); any other label
 *  is the server's words, kept (walk 06, UX 2). */
export function outputLabelWords(label: string): string {
  const i = label.lastIndexOf(': ')
  const tail = i < 0 ? '' : label.slice(i + 2)
  const head = ID.test(tail) ? `${label.slice(0, i + 2)}${idWords(tail)}` : label
  // An id inside the sentence ("the grid side of grid_export") reads its name too.
  return head.replace(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/g, idWords)
}

/** The ledger key of a locked energy-model row in words (walk 06, UX 2):
 *  `bill:network_charges` → "Network charges", `asset:fom:Generator:pv_plant` →
 *  "PV plant", `contract:ppa:ppa_energy` → "PPA energy", `bill_degradation:pv_plant`
 *  → "Degradation, PV plant". The last segment names the item; never an id. */
export function ledgerKeyWords(key: string, full = false): string {
  const parts = key.split(':').filter(Boolean)
  const last = parts[parts.length - 1] ?? ''
  const name = ID.test(last) ? idWords(last) : last
  if (full) {
    // Every segment but a component class ("Generator"), to tell two rows apart.
    const w = parts.filter(p => ID.test(p) || p === last).map(p => (ID.test(p) ? idWords(p) : p))
    return w.filter((x, i) => x !== w[i - 1]).join(' · ')
  }
  return parts[0] === 'bill_degradation' ? `Degradation, ${name}` : name
}

/** Each locked row's key in words; two rows that would read the same read in full. */
export function ledgerKeysWords(keys: string[]): string[] {
  const short = keys.map(k => ledgerKeyWords(k))
  return keys.map((k, i) => (short.filter(x => x === short[i]).length > 1 ? ledgerKeyWords(k, true) : short[i]))
}
