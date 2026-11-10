// The tariff builder's model (IC P3 WP3.7b). The form edits a `Tariff` as it
// is; `normaliseTariff` writes it back in the P2 model's shape — optional
// fields and defaults left out — so a tariff loaded into the builder and saved
// unchanged is the same JSON (tested on H3). Minimal client checks only: the
// server is the source of truth (its 422s are mapped to the fields).
import type { Tariff } from '../../../api/types'

export type Item = Tariff['items'][number]
export type Period = Item['periods'][number]

export const KINDS: Item['kind'][] = ['energy', 'demand', 'capacity', 'fixed', 'certificate', 'tax_levy']
export const UNITS: Item['unit'][] = ['per_kwh', 'per_kw_month', 'per_kw_year', 'per_month',
                                      'per_kva_year', 'per_day']
export const MEASURED: NonNullable<Item['measured_on']>[] = ['import', 'export', 'net', 'peak_import']
export const SETTLEMENTS: NonNullable<Item['settlement']>[] = ['15min', '30min', 'h']

const DEFAULT_UNIT: Record<Item['kind'], Item['unit']> = {
  energy: 'per_kwh', demand: 'per_kw_month', capacity: 'per_kw_year', fixed: 'per_month',
  certificate: 'per_kwh', tax_levy: 'per_kwh',
}

export function blankTariff(): Tariff {
  return { id: 'tariff', name: 'New tariff', jurisdiction: 'DE',
           valid_from: `${new Date().getFullYear()}-01-01`,
           items: [blankItem('energy', 'energy')] }
}

export function blankItem(kind: Item['kind'], id: string): Item {
  return { id, kind, unit: DEFAULT_UNIT[kind], periods: [{ name: 'all', rate: 0 }] }
}

/** Items with tiers whose periods carry `tier_rates` (a windowed tiered item). */
export const isWindowed = (item: Item) =>
  !!item.tiers?.length && item.periods.some(p => p.tier_rates != null)

const drop = <T extends object>(o: T, keys: Array<keyof T>, when: (v: unknown) => boolean) => {
  const out = { ...o }
  for (const k of keys) if (when(out[k])) delete out[k]
  return out
}
const empty = (v: unknown) => v === undefined || v === null || (Array.isArray(v) && v.length === 0)

export function normaliseTariff(t: Tariff): Tariff {
  const base = drop({ ...t }, ['dso_or_retailer', 'valid_to', 'pack_hash', 'currency'] as Array<keyof Tariff>, empty)
  const unsupported = (t as { unsupported_fields?: string[] }).unsupported_fields
  if (!unsupported?.length) delete (base as { unsupported_fields?: unknown }).unsupported_fields
  return {
    ...base,
    items: t.items.map(item => {
      let it: Item = { ...item, periods: item.periods.map(p => drop(
        { ...p }, ['months', 'weekdays', 'start_hour', 'end_hour', 'tier_rates'], empty)) }
      it = drop(it, ['tiers', 'ratchet'], empty)
      if (it.settlement === '15min') delete it.settlement
      if (it.measured_on === 'import') delete it.measured_on
      if (it.direction === 'cost') delete it.direction
      return it
    }),
  }
}

/** "6, 7, 8" → [6, 7, 8]; blank → undefined; anything else → null (invalid). */
export function parseIntList(text: string): number[] | undefined | null {
  if (!text.trim()) return undefined
  const parts = text.split(/[\s,]+/).filter(Boolean).map(Number)
  return parts.every(Number.isInteger) ? parts : null
}

export function parseNumberList(text: string): number[] | undefined | null {
  if (!text.trim()) return undefined
  const parts = text.split(/[\s,]+/).filter(Boolean).map(Number)
  return parts.every(Number.isFinite) ? parts : null
}

export const listText = (v: number[] | null | undefined) => (v ?? []).join(', ')

/** A server error's location (`['items', 0, 'periods', 1, 'rate']`, with or
 *  without the request's `body` / `tariff` / `payload` prefix) as a field key. */
export function fieldKey(loc: Array<string | number>): string {
  const i = loc.findIndex(x => x === 'items' || x === 'id' || x === 'name'
    || x === 'jurisdiction' || x === 'valid_from')
  if (i >= 0) return loc.slice(i).join('.')
  // A tariff-level error (`['import_tariff']`, `['body', 'tariff']`, a model
  // validator) is the tariff's (review #3).
  const t = loc.findIndex(x => x === 'import_tariff' || x === 'tariff' || x === 'payload')
  return t >= 0 ? 'tariff' : (loc.join('.') || 'tariff')
}

/** The currencies a jurisdiction implies: a copy of the backend's frozen
 *  `JURISDICTION_CURRENCY` (`models/commercial.py`), shown as the currency input's default. */
const EURO = ['AT', 'BE', 'CY', 'DE', 'EE', 'ES', 'FI', 'FR', 'GR', 'HR', 'IE', 'IT', 'LT', 'LU',
              'LV', 'MT', 'NL', 'PT', 'SI', 'SK', 'EU']
export const JURISDICTION_CURRENCY: Record<string, string> = {
  ...Object.fromEntries(EURO.map(c => [c, 'EUR'])),
  GB: 'GBP', UK: 'GBP', US: 'USD', CA: 'CAD', CH: 'CHF', AU: 'AUD', NZ: 'NZD', DK: 'DKK',
  SE: 'SEK', NO: 'NOK', PL: 'PLN', CZ: 'CZK', HU: 'HUF', RO: 'RON', JP: 'JPY',
}

/** The currency `jurisdiction` implies (`NL` → `EUR`, `US-CA` → `USD`), or null. */
export function impliedCurrency(jurisdiction: string, validFrom?: string): string | null {
  const code = jurisdiction.trim().toUpperCase().replace(/_/g, '-').split('-')[0]
  if (code === 'BG') return validFrom && validFrom >= '2026-01-01' ? 'EUR' : validFrom ? 'BGN' : null
  return JURISDICTION_CURRENCY[code] ?? null
}

/** The tariff header's field words, one map: `text` is the visible label, `aria` the
 *  accessible name and the error list's prefix. A tariff-level error is "Tariff". */
export const FIELD_WORDS = {
  id: { text: 'Tariff ID', aria: 'Tariff id' },
  name: { text: 'Name', aria: 'Tariff name' },
  jurisdiction: { text: 'Jurisdiction', aria: 'Jurisdiction' },
  currency: { text: 'Currency', aria: 'Currency' },
  valid_from: { text: 'Valid from', aria: 'Valid from' },
} as const

/** An item's field words: `text` is the visible label, `aria` the suffix of the
 *  accessible name ("Item energy kind"). */
export const ITEM_WORDS = {
  id: { text: 'Item ID', aria: 'id' },
  kind: { text: 'Charge type', aria: 'kind' },
  unit: { text: 'Unit', aria: 'unit' },
  measured_on: { text: 'Measured on', aria: 'measured on' },
  direction: { text: 'Direction', aria: 'direction' },
  settlement: { text: 'Settlement interval', aria: 'settlement' },
} as const

/** The words shown for a stored option value (a kind, unit, measure or settlement). */
export const OPTION_WORDS: Record<Item['kind'] | Item['unit'] | NonNullable<Item['measured_on']>
  | NonNullable<Item['settlement']>, string> = {
  energy: 'energy', demand: 'demand', capacity: 'capacity', fixed: 'fixed', certificate: 'certificate',
  tax_levy: 'tax or levy',
  per_kwh: 'per kWh', per_kw_month: 'per kW-month', per_kw_year: 'per kW-year', per_month: 'per month',
  per_kva_year: 'per kVA-year', per_day: 'per day',
  import: 'import', export: 'export', net: 'net', peak_import: 'peak import',
  '15min': '15 min', '30min': '30 min', h: '1 hour',
}
/** An option's words; a stored value the map does not know shows as stored. */
export const optionWords = (value: string): string => (OPTION_WORDS as Record<string, string>)[value] ?? value
export function fieldLabel(key: string): string {
  const [head, ...rest] = key.split('.')
  const word = head === 'tariff' ? 'Tariff' : (FIELD_WORDS as Record<string, { aria: string }>)[head]?.aria
  return [word ?? head, ...rest].join('.')
}

/** The server's validation errors (preview 422 `errors`, or FastAPI's list) by field key. */
export function errorsByField(detail: unknown): Record<string, string[]> {
  const list = Array.isArray(detail) ? detail
    : Array.isArray((detail as { errors?: unknown })?.errors) ? (detail as { errors: unknown[] }).errors : []
  const out: Record<string, string[]> = {}
  for (const e of list as Array<{ loc?: Array<string | number>; msg?: string }>) {
    const key = fieldKey(e.loc ?? [])
    ;(out[key] ??= []).push(String(e.msg ?? 'invalid'))
  }
  return out
}

/** Errors at or under `prefix` (an item's or a period's fields). */
export function errorsUnder(errors: Record<string, string[]>, prefix: string): string[] {
  return Object.entries(errors).filter(([k]) => k === prefix || k.startsWith(`${prefix}.`))
    .flatMap(([k, msgs]) => msgs.map(m => `${k}: ${m}`))
}
