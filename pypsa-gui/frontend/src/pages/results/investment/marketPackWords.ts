// Plain words for the market-pack card (SCU06 K8a, UX 3 terms 1–3): a product by its
// kind and direction, a cell by its product and field, never a slot key.
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

/** "Secondary reserve (up)". */
export function productWords(p: Pick<ReserveProduct, 'kind' | 'direction'>): string {
  return `${KIND[p.kind] ?? p.kind} (${DIRECTION[p.direction] ?? p.direction})`
}

/** A cell (`export_price_ref` or `reserve_products/<id>/<field>`) in words; `products`
 *  names a product by its kind, else the product's id is shown as given. */
export function cellWords(cell: string, products: ReserveProduct[]): string {
  if (cell === 'export_price_ref') return 'Export price'
  const m = /^reserve_products\/([^/]+)\/(.+)$/.exec(cell)
  if (!m) return cell
  const prod = products.find(p => p.id === m[1])
  const name = prod ? productWords(prod) : m[1]
  return m[2] === 'id' ? name : `${name}: ${FIELD[m[2]] ?? m[2].replace(/_/g, ' ')}`
}

function isRef(v: unknown): v is LibraryRef {
  return typeof v === 'object' && v !== null && 'id' in v && 'version' in v
}

const BUYER: Record<string, string> = { tso: 'the grid operator (TSO)' }

/** The field of a cell (`sustain_h`), or null for the export pick. */
export function cellField(cell: string): string | null {
  const m = /^reserve_products\/[^/]+\/(.+)$/.exec(cell)
  return m ? m[1] : null
}

/** A cell's value as the user reads it; `field` picks the words for a kind,
 *  a direction, a buyer or a product id, which are never shown raw. */
export function valueWords(v: unknown, field: string | null = null, products: ReserveProduct[] = []): string {
  if (v === null || v === undefined) return field === 'id' ? 'not in the project' : 'none'
  if (isRef(v)) return `Library series ${librarySeriesLabel(v.id, v.version)}`
  if (Array.isArray(v)) return v.length ? v.map(x => valueWords(x)).join(', ') : 'any'
  if (typeof v === 'string') {
    if (field === 'kind') return KIND[v] ?? v
    if (field === 'direction') return DIRECTION[v] ?? v
    if (field === 'counterparty') return BUYER[v] ?? v
    if (field === 'id') {
      const prod = products.find(p => p.id === v)
      return prod ? productWords(prod) : v
    }
    return v
  }
  if (typeof v === 'number') return String(v)
  return JSON.stringify(v)
}

/** A product the pack adds, in one line. */
export function addedWords(p: ReserveProduct): string {
  const parts = [`holds ${p.sustain_h} h`,
    p.storages.length ? `storage ${p.storages.join(', ')}` : 'any storage that may offer reserves',
    `buyer ${BUYER[p.counterparty] ?? p.counterparty}`]
  if (p.max_commit_mw != null) parts.push(`at most ${p.max_commit_mw} MW`)
  parts.push(p.capacity_price ? `price ${valueWords(p.capacity_price)}` : 'no price yet')
  return `Added: ${parts.join(', ')}`
}

/** A refused pack or a failed request as one plain sentence (never the code). */
export function refusalText(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  const msg = (d as { message?: unknown } | undefined)?.message
  if (typeof msg === 'string') return msg
  return e instanceof Error ? e.message : 'The request failed.'
}
