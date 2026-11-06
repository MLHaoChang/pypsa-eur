// GET /results/reserves (DC grid readiness 06): reserve products on the
// backup storage, `backend/services/results/reserves.py`. Per storage that may
// carry a reserve: its backup floor (kept for outages), energy capacity, state
// of charge and the MW committed per product; the capacity-payment lines.
//
// 204 without reserve products (null here); 409 `solver_in_flight` during a
// solve. A missing or out-of-date solve gives `committed` and every `amount`
// null with the flag `reserve_not_solved`, never zeros.
import client from './client'

export type ReserveKind = 'fast_symmetric' | 'secondary' | 'tertiary'
export type ReserveDirection = 'symmetric' | 'up' | 'down'

export interface ReserveProduct {
  id: string
  kind: ReserveKind
  direction: ReserveDirection
  sustain_h: number
  counterparty: string
  /** The capacity price series' Library source (`demo` is a demo series). */
  price_source: string
}

export interface ReserveStorage {
  name: string
  /** One of the DC backup strings. */
  string: boolean
  floor_mwh: number | null
  e_cap_mwh: number | null
  soc_mwh: (number | null)[] | null
  /** MWh the committed up (and symmetric) products keep above the floor, Σ r·d/η_dis; null when not solved. */
  reserved_up_mwh?: (number | null)[] | null
  /** MWh of room the committed down (and symmetric) products keep free below the energy capacity, Σ r·d·η_store. */
  reserved_down_mwh?: (number | null)[] | null
  /** MW per product id, one value per snapshot; null without a current solve. */
  committed: Record<string, (number | null)[]> | null
}

export type ReserveLineFlag =
  | 'activation_not_valued' | 'reserve_not_solved' | 'reserve_inputs_changed_since_solve'

export interface ReserveLine {
  period: number | null
  /** `reserve:<product id>`. */
  contract_id: string
  payer: string | null
  payee: string | null
  value_stream: 'reserve_capacity'
  quantity_mwh: null
  /** null when not solved, never 0. */
  amount: number | null
  flags: string[]
}

export interface ReservesPayload {
  products: ReserveProduct[]
  snapshots: string[]
  storages: ReserveStorage[]
  lines: ReserveLine[]
  flags: string[]
  headroom_h_per_string: number | null
  /** The revenue's currency (the project's, else the bound prices'); null when unknown. */
  currency: string | null
  /** The money year the investment case books these lines in; null when unread. */
  money_year: number | null
  /** What the capacity payments leave out, in words. */
  not_modelled: string
}

/** "GBP, 2030 money": the revenue header's unit, naming what is not known. */
export function revenueUnit(p: Pick<ReservesPayload, 'currency' | 'money_year'>): string {
  return `${p.currency ?? 'currency not stated'}, ${p.money_year != null ? `${p.money_year} money` : 'money year not known'}`
}

/** The product id a line pays for (`reserve:<id>`). */
export const lineProduct = (l: ReserveLine): string =>
  l.contract_id.startsWith('reserve:') ? l.contract_id.slice('reserve:'.length) : l.contract_id

/** The sum of the lines' amounts, or null when none is solved (never 0 for "not solved"). */
export function reserveRevenue(lines: readonly ReserveLine[]): number | null {
  const known = lines.map(l => l.amount).filter((a): a is number => a != null && Number.isFinite(a))
  return known.length ? known.reduce((s, a) => s + a, 0) : null
}

/** True when a product prices on a demo series: every total that sums its
 *  revenue carries the DEMO tag (#1174). */
export const reservesUseDemo = (p: ReservesPayload): boolean =>
  p.products.some(pr => pr.price_source === 'demo')

/** True when the solve read other products or floors than the project has now. */
export const reservesOutOfDate = (p: ReservesPayload): boolean =>
  p.flags.includes('reserve_inputs_changed_since_solve')
  || p.lines.some(l => l.flags.includes('reserve_inputs_changed_since_solve'))

/** The `resultsSectionRequest` that scrolls Storage cycling to the Reserve
 *  products card (Grid study's "Storage view ›"). */
export const RESERVE_SECTION = 'reserves'

export const reservesApi = {
  /** null when the project has no reserve products (204). A 409 during a
   *  solve is an expected answer, so it raises quietly. */
  get: () =>
    client.get<ReservesPayload>('/results/reserves', { skipErrorToast: true })
      .then(r => (r.status === 204 ? null : r.data)),
}
