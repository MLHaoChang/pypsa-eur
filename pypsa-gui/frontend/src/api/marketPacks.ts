import client from './client'
import type { LibraryRef, ReserveProduct } from './types'

export type { ReserveProduct } from './types'

// Market packs (SCU06 K8a): the shipped NO-1..NO-5 reserve-product and day-ahead
// templates. List `GET /api/simulation/commercial/market_packs`, preview
// `GET .../{pack_id}/preview` (a read), apply `POST .../{pack_id}/apply` (an ordinary
// commercial edit through PUT /solver_config; backend: routers/simulation.py,
// services/commercial/market_packs.py).

export interface PackRefusal { code: string; message: string }

export interface MarketPackRow {
  id: string
  label: string
  region: string
  currency: string
  valid_year: number
  pack_hash: string
  flags: string[]
  day_ahead: LibraryRef | null
  products: ReserveProduct[]
  /** Why the pack does not fit this project (zone or currency); null when it fits. */
  refusal: PackRefusal | null
  /** The last apply was this pack. */
  pinned: boolean
  update_available: boolean
}

export interface MarketPackList {
  zone: string | null
  currency: string | null
  saved: boolean
  pin_error: PackRefusal | null
  packs: MarketPackRow[]
}

/** A cell: `export_price_ref` or `reserve_products/<product id>/<field>`. */
export interface PackChange { cell: string; from: unknown; to: unknown }
export interface PackConflict { cell: string; yours: unknown; pack: unknown; last_applied: unknown }
export interface PackKept { cell: string; reason: 'empty_ref' | 'no_export_link' }

export interface MarketPackPlan {
  pack_id: string
  pack_hash: string
  changes: PackChange[]
  conflicts: PackConflict[]
  kept: PackKept[]
}

export interface MarketPackApplied extends MarketPackPlan { changed: number }

const enc = encodeURIComponent

export const marketPacksApi = {
  list: (): Promise<MarketPackList> =>
    client.get('/simulation/commercial/market_packs').then(r => r.data),
  preview: (packId: string): Promise<MarketPackPlan> =>
    client.get(`/simulation/commercial/market_packs/${enc(packId)}/preview`).then(r => r.data),
  apply: (packId: string, packHash: string): Promise<MarketPackApplied> =>
    client.post(`/simulation/commercial/market_packs/${enc(packId)}/apply`, { pack_hash: packHash })
      .then(r => r.data),
}
