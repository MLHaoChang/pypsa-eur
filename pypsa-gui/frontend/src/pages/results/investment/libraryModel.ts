// The Library browser's pure helpers (IC P3 WP3.7a): the rate an uploaded URDB
// file holds (never picked silently among several), what the project pins, and
// whether attaching a Library tariff would replace a hand-made inline one.
import type { CommercialConfig, LibraryItemRef } from '../../../api/types'

export type RateChoice =
  | { kind: 'rate'; rate: Record<string, unknown>
      /** Several rates in the file: the choice stays visible and changeable. */
      rates?: Array<{ index: number; label: string }>; index?: number }
  | { kind: 'choose'; rates: Array<{ index: number; label: string }> }
  | { kind: 'error'; message: string }

const text = (v: unknown, n = 60) => String(v ?? '').slice(0, n)

function rateLabels(items: Array<Record<string, unknown>>) {
  return items.map((r, i) => ({ index: i, label: `${text(r.name ?? r.label)}${
    r.startdate ? ` (from ${urdbDate(r.startdate)}${r.enddate ? ` to ${urdbDate(r.enddate)}` : ''})` : ''}` }))
}

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
      return { kind: 'choose', rates: rateLabels(items) }
    }
    const i = index ?? 0
    if (i < 0 || i >= items.length) return { kind: 'error', message: `no rate ${i}` }
    return { kind: 'rate', rate: items[i],
             ...(items.length > 1 ? { rates: rateLabels(items), index: i } : {}) }
  }
  const et = d.ElectricTariff as Record<string, unknown> | undefined
  if (et && typeof et === 'object') {
    if (et.urdb_response && typeof et.urdb_response === 'object') {
      return { kind: 'rate', rate: et.urdb_response as Record<string, unknown> }
    }
    return { kind: 'error', message: 'the REopt scenario carries no urdb_response (a URDB label '
                                      + 'or blended rates cannot be imported)' }
  }
  return { kind: 'rate', rate: d }
}

/** The Library item versions the project pins: its import tariff ref, each
 *  contract's `library_ref` and the connection agreement's (WP3.7a review #3). */
export function pinnedVersions(commercial: CommercialConfig | null | undefined,
                               kind: LibraryItemRef['kind'], id: string): number[] {
  const refs: Array<LibraryItemRef | null | undefined> =
    kind === 'tariff' ? [commercial?.import_tariff_ref]
      : kind === 'contract' ? (commercial?.contracts ?? []).map(c => c.library_ref)
        : [commercial?.connection?.library_ref]
  return [...new Set(refs.filter((r): r is LibraryItemRef => !!r && r.id === id && r.kind === kind)
    .map(r => r.version))].sort((a, b) => b - a)
}

/** Attaching `ref` would replace a tariff that is not a Library item: an
 *  inline tariff (or label) with NO ref. With a ref, the stored inline copy
 *  IS that Library item's — the solver-config route refuses a copy that does
 *  not hash to its ref (`import_tariff_ref_conflict`) — so a switch between
 *  Library tariffs replaces nothing hand-made (P2 semantics; WP3.7a review #1). */
export function replacesInline(commercial: CommercialConfig | null | undefined): boolean {
  if (!commercial?.import_tariff && !commercial?.import_tariff_id) return false
  return !commercial?.import_tariff_ref
}

/** A URDB epoch date (seconds) as YYYY-MM-DD, or the value as written. */
export function urdbDate(v: unknown): string {
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n) || n <= 0) return String(v ?? '')
  return new Date(n * 1000).toISOString().slice(0, 10)
}
