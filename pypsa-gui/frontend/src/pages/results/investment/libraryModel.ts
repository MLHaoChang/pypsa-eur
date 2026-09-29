// The Library browser's pure helpers (IC P3 WP3.7a): the rate an uploaded URDB
// file holds (never picked silently among several), what the project pins, and
// whether attaching a Library tariff would replace a hand-made inline one.
import type { CommercialConfig, LibraryItemRef, Tariff } from '../../../api/types'

export type RateChoice =
  | { kind: 'rate'; rate: Record<string, unknown> }
  | { kind: 'choose'; rates: Array<{ index: number; label: string }> }
  | { kind: 'error'; message: string }

const text = (v: unknown, n = 60) => String(v ?? '').slice(0, n)

/** The rate of an uploaded URDB file: the rate object itself, one item of an
 *  OpenEI response (the user picks when there are several — superseded
 *  versions come back too), or a REopt scenario's `urdb_response`. */
export function urdbRate(data: unknown, index?: number): RateChoice {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return { kind: 'error', message: 'the file is not a URDB rate or an OpenEI response' }
  }
  const d = data as Record<string, unknown>
  if (Array.isArray(d.items)) {
    const items = d.items.filter(x => x && typeof x === 'object') as Array<Record<string, unknown>>
    if (!items.length) return { kind: 'error', message: 'the OpenEI response lists no rates' }
    if (index === undefined && items.length > 1) {
      return { kind: 'choose', rates: items.map((r, i) => ({
        index: i, label: `${text(r.name ?? r.label)}${r.startdate ? ` (from ${text(r.startdate, 24)})` : ''}` })) }
    }
    const i = index ?? 0
    if (i < 0 || i >= items.length) return { kind: 'error', message: `no rate ${i}` }
    return { kind: 'rate', rate: items[i] }
  }
  const et = d.ElectricTariff as Record<string, unknown> | undefined
  if (et && typeof et === 'object') {
    if (et.urdb_response && typeof et.urdb_response === 'object') {
      return { kind: 'rate', rate: et.urdb_response as Record<string, unknown> }
    }
    return { kind: 'error', message: 'the REopt scenario names a URDB label but holds no rate' }
  }
  return { kind: 'rate', rate: d }
}

/** How the project uses a Library item version: its import tariff ref. */
export function pinnedVersion(commercial: CommercialConfig | null | undefined,
                              kind: LibraryItemRef['kind'], id: string): number | null {
  const ref = kind === 'tariff' ? commercial?.import_tariff_ref : null
  return ref && ref.id === id ? ref.version : null
}

const canon = (v: unknown): string => JSON.stringify(v, (_k, x) =>
  x && typeof x === 'object' && !Array.isArray(x)
    ? Object.fromEntries(Object.entries(x).filter(([, y]) => y !== null && y !== undefined)
      .sort(([a], [b]) => a.localeCompare(b)))
    : x)

/** Attaching `ref` would replace a tariff that is not this item: an inline
 *  tariff with no ref, or with a ref to another item that it no longer equals
 *  (P2 semantics — a plain switch between Library tariffs replaces nothing
 *  hand-made; the server hashes, this compares canonical JSON). */
export function replacesInline(commercial: CommercialConfig | null | undefined,
                               ref: LibraryItemRef, currentPayloadOfOldRef?: Tariff | null): boolean {
  const inline = commercial?.import_tariff
  if (!inline && !commercial?.import_tariff_id) return false
  const old = commercial?.import_tariff_ref
  if (old && old.hash === ref.hash) return false
  if (old && inline && currentPayloadOfOldRef && canon(inline) === canon(currentPayloadOfOldRef)) {
    return false
  }
  return true
}
