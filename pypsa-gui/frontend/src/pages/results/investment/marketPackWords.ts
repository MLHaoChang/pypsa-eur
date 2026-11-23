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
  return `${prod ? productWords(prod) : m[1]}: ${FIELD[m[2]] ?? m[2].replace(/_/g, ' ')}`
}

function isRef(v: unknown): v is LibraryRef {
  return typeof v === 'object' && v !== null && 'id' in v && 'version' in v
}

/** A cell's value as the user reads it. */
export function valueWords(v: unknown): string {
  if (v === null || v === undefined) return 'none'
  if (isRef(v)) return `Library series ${librarySeriesLabel(v.id, v.version)}`
  if (Array.isArray(v)) return v.length ? v.map(valueWords).join(', ') : 'any'
  if (typeof v === 'string') return KIND[v] ?? DIRECTION[v] ?? v
  if (typeof v === 'number') return String(v)
  return JSON.stringify(v)
}

/** A refused pack or a failed request as one plain sentence (never the code). */
export function refusalText(e: unknown): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  const msg = (d as { message?: unknown } | undefined)?.message
  if (typeof msg === 'string') return msg
  return e instanceof Error ? e.message : 'The request failed.'
}
