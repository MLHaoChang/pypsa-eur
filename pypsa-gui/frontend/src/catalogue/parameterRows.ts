/**
 * The ⌘K palette's "Parameters" rows (spec expert-catalogue §4.3): an asset's value
 * (`PV roof › Capacity · 2.4 MW`) for each asset of the entry's class, or the entry
 * itself (`Capacity · Generator · Size and limits`) when the project has none.
 * Rows carry plain names only: a hit by model-file name says so in words.
 */
import { componentLabel } from '../utils/componentLabels'
import type { Catalogue, CatalogueEntry } from './api'
import { canOpenParameter, type ParameterTarget } from './openParameter'
import type { ParameterHit } from './searchIndex'

export type PaletteAsset = Record<string, unknown> & { name: string }

export interface ParameterRow {
  id: string
  title: string
  subtitle: string
  /** The value on that asset, with its unit. */
  hint?: string
  /** Where Enter goes; none for an entry with no asset in the project. */
  target?: ParameterTarget
}

/** Asset rows shown per entry; the rest are counted on the last row. */
export const ASSETS_PER_ENTRY = 5
/** Entries shown at most. */
export const ENTRY_LIMIT = 8

export const MODEL_NAME_NOTE = 'matches the model-file name'

/** A stored value as a person reads it: display units, option words, Yes/No. */
export function formatValue(entry: Pick<CatalogueEntry, 'kind' | 'scale' | 'unit_display' | 'options' | 'options_plain'>,
  raw: unknown): string {
  if (raw === null || raw === undefined || raw === '' || (typeof raw === 'number' && Number.isNaN(raw))) return 'Not set'
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

export function parameterRows(hits: readonly ParameterHit[], catalogue: Pick<Catalogue, 'groups'>,
  assetsByClass: Readonly<Record<string, readonly PaletteAsset[]>>): ParameterRow[] {
  const groupName = new Map(catalogue.groups.map(g => [g.id, g.plain_name]))
  const rows: ParameterRow[] = []
  for (const { entry, via } of hits.filter(h => canOpenParameter(h.entry)).slice(0, ENTRY_LIMIT)) {
    const where = [componentLabel(entry.scope), groupName.get(entry.group) ?? ''].filter(Boolean).join(' · ')
    const note = via === 'model' ? ` · ${MODEL_NAME_NOTE}` : ''
    const assets = assetsByClass[entry.scope] ?? []
    if (!assets.length) {
      rows.push({ id: `param-${entry.id}`, title: entry.plain_name, subtitle: `${where} · None in this project${note}` })
      continue
    }
    const shown = assets.slice(0, ASSETS_PER_ENTRY)
    const more = assets.length - shown.length
    shown.forEach((a, i) => {
      const last = i === shown.length - 1 && more > 0
      rows.push({
        id: `param-${entry.id}-${a.name}`,
        title: `${a.name} › ${entry.plain_name}`,
        subtitle: `${where}${note}${last ? ` · and ${more} more` : ''}`,
        hint: formatValue(entry, a[entry.model_name]),
        target: { entry, asset: a.name },
      })
    })
  }
  return rows
}
