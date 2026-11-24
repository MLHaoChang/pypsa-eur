// Plain words for the market-pack card (SCU06 K8a, UX 3 terms 1–3): a product by its
// kind and direction, a cell by its product and field, never a slot key or a code.
// An unknown code reads as a plain fallback; the card puts the raw cell in a title.
import type { LibraryRef, ReserveProduct } from '../../../api/types'
import { librarySeriesLabel } from '../../../plainNames'

const KIND: Record<string, string> = {
  fast_symmetric: 'Fast frequency reserve',
  secondary: 'Secondary reserve',
  tertiary: 'Tertiary reserve',
}
const DIRECTION: Record<string, string> = {
  symmetric: 'up and down',
  up: 'up',
  down: 'down',
}
const FIELD: Record<string, string> = {
  id: 'product',
  kind: 'kind',
  direction: 'direction',
  sustain_h: 'hours it must hold',
  capacity_price: 'capacity price',
  max_commit_mw: 'most it may offer (MW)',
  storages: 'storage that may offer it',
  counterparty: 'buyer',
}
const BUYER: Record<string, string> = { tso: 'the grid operator (TSO)' }

/** "Secondary reserve (up)"; "Reserve product" for a kind the card does not know. */
export function productWords(p: Pick<ReserveProduct, 'kind' | 'direction'>): string {
  const kind = KIND[p.kind] ?? 'Reserve product'
  const dir = DIRECTION[p.direction]
  return dir ? `${kind} (${dir})` : kind
}

const CELL = /^reserve_products\/([^/]+)\/(.+)$/

/** The product id of a `reserve_products/<id>/<field>` cell, or null. */
export function productOf(cell: string): string | null {
  return CELL.exec(cell)?.[1] ?? null
}

/** The field of a cell (`sustain_h`), or null for the export pick. */
export function cellField(cell: string): string | null {
  return CELL.exec(cell)?.[2] ?? null
}

/** The products a card names: the project's own, then those only the pack lists. */
export function namedProducts(held: ReserveProduct[], pack: ReserveProduct[]): ReserveProduct[] {
  return [...held, ...pack.filter(p => !held.some(q => q.id === p.id))]
}

/** A cell (`export_price_ref` or `reserve_products/<id>/<field>`) in words. */
export function cellWords(cell: string, products: ReserveProduct[]): string {
  if (cell === 'export_price_ref') return 'Export price'
  const m = CELL.exec(cell)
  if (!m) return 'Another setting'
  const prod = products.find(p => p.id === m[1])
  const name = prod ? productWords(prod) : 'A reserve product'
  return m[2] === 'id' ? name : `${name}: ${FIELD[m[2]] ?? 'another setting'}`
}

function isRef(v: unknown): v is LibraryRef {
  return typeof v === 'object' && v !== null && 'id' in v && 'version' in v
}

/** A cell's value as the user reads it; `field` picks the words for a kind,
 *  a direction, a buyer or a product id, which are never shown raw. */
export function valueWords(v: unknown, field: string | null = null, products: ReserveProduct[] = []): string {
  if (v === null || v === undefined) return field === 'id' ? 'not in the project' : 'none'
  if (isRef(v)) return `Library series ${librarySeriesLabel(v.id, v.version)}`
  if (Array.isArray(v)) return v.length ? v.map(x => valueWords(x)).join(', ') : 'any'
  if (typeof v === 'string') {
    if (field === 'kind') return KIND[v] ?? 'another kind'
    if (field === 'direction') return DIRECTION[v] ?? 'another direction'
    if (field === 'counterparty') return BUYER[v] ?? 'another buyer'
    if (field === 'id') {
      const prod = products.find(p => p.id === v)
      return prod ? productWords(prod) : 'a reserve product'
    }
    return v
  }
  if (typeof v === 'number') return String(v)
  return 'a value the card cannot show'
}

/** A product the pack adds, in one line. */
export function addedWords(p: ReserveProduct): string {
  const parts = [`holds ${p.sustain_h} h`,
    p.storages.length ? `storage ${p.storages.join(', ')}` : 'any storage that may offer reserves',
    `buyer ${valueWords(p.counterparty, 'counterparty')}`]
  if (p.max_commit_mw != null) parts.push(`at most ${p.max_commit_mw} MW`)
  parts.push(p.capacity_price ? `price ${valueWords(p.capacity_price)}` : 'no price yet')
  return `Added: ${parts.join(', ')}`
}

/** A refused pack or a failed request as one plain sentence: the server's own
 *  sentence when it sent one, else a fixed one; never raw transport or exception text. */
export function refusalText(e: unknown): string {
  const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  const d = r?.data?.detail
  if (typeof d === 'string' && d.trim()) return d
  const msg = (d as { message?: unknown } | undefined)?.message
  if (typeof msg === 'string' && msg.trim()) return msg
  if (r) return 'The server could not do this. Try again; if it happens again, reload the page.'
  return 'The server could not be reached. Check the connection, then try again.'
}
