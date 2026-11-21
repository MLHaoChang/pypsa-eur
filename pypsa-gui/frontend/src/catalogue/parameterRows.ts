/**
 * The ⌘K palette's "Parameters" rows (spec expert-catalogue §4.3): an asset's value
 * (`PV roof: Capacity · 2.4 MW`) for each asset of the entry's class, or the entry
 * itself (`Capacity · Generator · Size and limits · None in this project`) when the
 * project has none. Rows carry plain names only: a hit by model-file name says so in
 * words (RAW-NAMES-1). Words of the query that name assets narrow the asset rows.
 */
import { componentLabel } from '../utils/componentLabels'
import { CARRIER_CATALOG } from '../utils/carrierCatalog'
import type { Catalogue, CatalogueEntry } from './api'
import { canOpenParameter, type ParameterTarget } from './openParameter'
import { norm, plainText, words, type ParameterHit, type ParameterIndex } from './searchIndex'

export type PaletteAsset = Record<string, unknown> & { name: string }

export interface ParameterRow {
  id: string
  title: string
  subtitle: string
  /** The value on that asset, with that asset's unit. */
  hint?: string
  /** Where Enter goes; none for an entry with no asset in the project (the row is disabled). */
  target?: ParameterTarget
}

/** Asset rows shown per entry; the rest are counted on the last row. */
export const ASSETS_PER_ENTRY = 5
/** Entries shown at most. */
export const ENTRY_LIMIT = 8
export const MODEL_NAME_NOTE = 'matches the model-file name'
export const NONE_NOTE = 'None in this project'
export const moreNote = (n: number) => `and ${n} more: type the asset name to narrow`
/** Characters of help or guide text shown around a text hit. */
const SNIPPET = 60

type Unit = Pick<CatalogueEntry, 'kind' | 'scale' | 'unit_display' | 'options' | 'options_plain'>

/** A stored value as a person reads it: display units, option words, Yes/No; "— Not set" when empty. */
export function formatValue(entry: Unit, raw: unknown): string {
  if (raw === null || raw === undefined || raw === '' || (typeof raw === 'number' && Number.isNaN(raw))) return '— Not set'
  if (typeof raw === 'boolean') return raw ? 'Yes' : 'No'
  if (typeof raw === 'number') {
    if (!Number.isFinite(raw)) return raw > 0 ? 'No limit' : 'No lower limit'
    const shown = (raw * (entry.scale || 1)).toLocaleString('en', { maximumFractionDigits: 3 })
    return entry.unit_display ? `${shown} ${entry.unit_display}` : shown
  }
  const s = String(raw)
  const i = entry.options.indexOf(s)
  return i >= 0 && entry.options_plain[i] ? entry.options_plain[i] : s
}

// ── Per-asset units (backend `fields.size_units` / `cost_units`) ─────────────
// A size on a tonne bus is in t/h (t for a stock), and its costs are per tonne at scale 1.

/** The bus each class's size is counted on (a Link's is its input side). */
const SIZE_BUS: Record<string, string> = { Generator: 'bus', StorageUnit: 'bus', Store: 'bus', Load: 'bus', Link: 'bus0' }
const TONNE_COST_UNIT: Record<string, string> = {
  'EUR/kW': 'EUR/(t/h)', 'EUR/kW/yr': 'EUR/(t/h)/yr', 'EUR/kWh': 'EUR/t', 'EUR/kWh/yr': 'EUR/t/yr', 'EUR/MWh': 'EUR/t',
}
const CARRIER_UNIT = new Map(CARRIER_CATALOG.map(c => [c.name, c.unit ?? '']))

/** A bus counts in tonnes: its own `unit`, else its carrier's catalogue unit (`process_products.bus_unit`). */
export function isTonneBus(bus: PaletteAsset | undefined): boolean {
  if (!bus) return false
  const own = String(bus.unit ?? '').trim()
  if (own === 't' || own === 'MWh') return own === 't'
  return CARRIER_UNIT.get(String(bus.carrier ?? '')) === 't'
}

/** The entry's unit and scale on this asset: the class-level ones, or the tonne ones on a tonne bus. */
export function unitOn(entry: CatalogueEntry, asset: PaletteAsset, busByName: ReadonlyMap<string, PaletteAsset>): Unit {
  const col = SIZE_BUS[entry.scope]
  if (!col || !isTonneBus(busByName.get(String(asset[col] ?? '')))) return entry
  if (entry.unit_display === 'MW') return { ...entry, unit_display: 't/h' }
  if (entry.unit_display === 'MWh') return { ...entry, unit_display: 't' }
  const cost = TONNE_COST_UNIT[entry.unit_display]
  return cost ? { ...entry, unit_display: cost, scale: 1 } : entry
}

/** A short plain excerpt of `text` around the first query word it holds, or ''. */
function snippet(text: string, query: string): string {
  const plain = plainText(text)
  const queryWords = norm(query).split(' ').filter(Boolean)
  const lower = plain.toLowerCase()
  const at = queryWords.map(w => lower.indexOf(w)).filter(i => i >= 0).sort((a, b) => a - b)[0]
  if (at === undefined) return ''
  // Cut at word boundaries: start after the last space before the window, end at the last space inside it.
  let from = Math.max(0, Math.floor(at - SNIPPET / 3))
  if (from > 0) from = plain.lastIndexOf(' ', from) + 1
  let to = Math.min(plain.length, from + SNIPPET)
  if (to < plain.length) {
    const space = plain.lastIndexOf(' ', to)
    if (space > at) to = space
  }
  const cut = plain.slice(from, to).trim()
  return `${from > 0 ? '…' : ''}${cut}${to < plain.length ? '…' : ''}`
}

const nameWords = words

/**
 * Split a query into the words that name assets and the rest. A word that also
 * starts a word of some parameter's plain name or alias stays with the parameter.
 */
export function splitAssetWords(query: string, assetNames: readonly string[],
  isParameterWord: (w: string) => boolean): { parameterQuery: string; assetWords: string[] } {
  // Whitespace only: a model-file name (`p_nom`) stays one word.
  const queryWords = norm(query).split(' ').filter(Boolean)
  const names = assetNames.map(nameWords)
  const assetWords = queryWords.filter(w => !isParameterWord(w) && names.some(n => n.some(x => x.startsWith(w))))
  return { parameterQuery: queryWords.filter(w => !assetWords.includes(w)).join(' '), assetWords }
}

const namedBy = (asset: string, words: readonly string[]) => {
  const n = nameWords(asset)
  return words.every(w => n.some(x => x.startsWith(w)))
}

export function parameterRows(query: string, index: ParameterIndex, catalogue: Pick<Catalogue, 'groups' | 'entries'>,
  assetsByClass: Readonly<Record<string, readonly PaletteAsset[]>>): ParameterRow[] {
  const groupName = new Map(catalogue.groups.map(g => [g.id, g.plain_name]))
  const busByName = new Map((assetsByClass.Bus ?? []).map(b => [b.name, b]))
  const paramWords = new Set(catalogue.entries.flatMap(e => [e.plain_name, ...e.aliases].flatMap(nameWords)))
  const isParameterWord = (w: string) => [...paramWords].some(p => p.startsWith(w))
  const allNames = Object.values(assetsByClass).flatMap(list => list.map(a => a.name))
  const { parameterQuery, assetWords } = splitAssetWords(query, allNames, isParameterWord)
  const hits: ParameterHit[] = parameterQuery ? index.search(parameterQuery) : []
  const rows: ParameterRow[] = []
  for (const { entry, via } of hits.filter(h => canOpenParameter(h.entry)).slice(0, ENTRY_LIMIT)) {
    const parts = [componentLabel(entry.scope), groupName.get(entry.group) ?? '']
    if (via === 'model') parts.push(MODEL_NAME_NOTE)
    if (via === 'help' || via === 'guide') parts.push(snippet(via === 'help' ? entry.short_help : entry.guide ?? '', parameterQuery))
    const where = parts.filter(Boolean).join(' · ')
    const assets = (assetsByClass[entry.scope] ?? []).filter(a => namedBy(a.name, assetWords))
    if (!assets.length) {
      if (assetWords.length) continue
      rows.push({ id: `param-${entry.id}`, title: entry.plain_name, subtitle: `${where} · ${NONE_NOTE}` })
      continue
    }
    const shown = assets.slice(0, ASSETS_PER_ENTRY)
    const more = assets.length - shown.length
    shown.forEach((a, i) => {
      const last = i === shown.length - 1 && more > 0
      rows.push({
        id: `param-${entry.id}-${a.name}`,
        title: `${a.name}: ${entry.plain_name}`,
        subtitle: last ? `${where} · ${moreNote(more)}` : where,
        hint: formatValue(unitOn(entry, a, busByName), a[entry.model_name]),
        target: { entry, asset: a.name },
      })
    })
  }
  return rows
}
