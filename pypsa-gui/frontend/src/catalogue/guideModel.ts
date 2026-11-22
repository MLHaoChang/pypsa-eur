/**
 * What the Parameter guide shows for an entry (spec expert-catalogue §6.2), worked out
 * from the catalogue alone: "Used by", "Typical values", "Default" and "Related" come
 * from the entry, "What it means" and "Example" from its guide markdown. A row with
 * nothing to say is left out (null or empty), never shown blank.
 */
import { componentLabel } from '../utils/componentLabels'
import type { Catalogue, CatalogueEntry } from './api'
import type { BrowseTab } from './guideStore'
import { findEntry } from './openParameter'
import { formatValue } from './parameterRows'

export type Resolved =
  | { kind: 'entry'; entry: CatalogueEntry }
  | { kind: 'browse'; tab: BrowseTab; item: string }

const BROWSE_PREFIX: Record<string, BrowseTab> = { asset_type: 'type', group: 'group', function: 'function' }

/** A guide id as something to show: an entry (current or former id), or an asset type, group or function to browse. */
export function resolveGuideId(catalogue: Catalogue, id: string): Resolved | null {
  const entry = findEntry(catalogue, id)
  if (entry) return { kind: 'entry', entry }
  const dot = id.indexOf('.')
  const tab = BROWSE_PREFIX[id.slice(0, dot)]
  const item = id.slice(dot + 1)
  if (dot > 0 && tab && browseItems(catalogue, tab).some(i => i.id === item)) return { kind: 'browse', tab, item }
  return null
}

/** The guide markdown cut in two: its "Example" section, and the rest ("What it means"). */
export function splitGuide(md: string | null): { meaning: string; example: string } {
  if (!md) return { meaning: '', example: '' }
  const meaning: string[] = []
  const example: string[] = []
  let into = meaning
  for (const line of md.split('\n')) {
    const heading = /^#{1,6}\s+(.*?)\s*#*\s*$/.exec(line)
    if (heading) {
      const title = heading[1].toLowerCase()
      if (title === 'example' || title === 'examples') { into = example; continue }
      into = meaning
      // The heading the guide row already carries.
      if (title === 'what it means') continue
    }
    into.push(line)
  }
  return { meaning: meaning.join('\n').trim(), example: example.join('\n').trim() }
}

/** Functions by their plain names: the ones that need the entry, and the ones that only read it. */
export function usedBy(entry: CatalogueEntry, catalogue: Pick<Catalogue, 'functions'>): { needed: string[]; also: string[] } {
  const needed: string[] = []
  const also: string[] = []
  for (const f of catalogue.functions) {
    const use = entry.functions[f.id]
    if (use === 'needs') needed.push(f.plain_name)
    else if (use) also.push(f.plain_name)
  }
  return { needed, also }
}

const asNumber = (v: unknown): unknown => (v === 'inf' ? Infinity : v === '-inf' ? -Infinity : v)

/** "Built-in default 0 MW" or "Generic default 1 MW (Assumed)"; null when the entry has none. */
export function defaultText(entry: CatalogueEntry): string | null {
  const d = entry.default
  if (!d || d.source === 'none' || d.value === null || d.value === undefined || d.value === '') return null
  const shown = formatValue(entry, asNumber(d.value))
  return d.source === 'library' ? `Generic default ${shown} (Assumed)` : `Built-in default ${shown}`
}

/** "0.1–50 MW · hard limit ≥ 0"; null when the catalogue gives no range. */
export function typicalText(entry: CatalogueEntry): string | null {
  const r = entry.range
  if (!r) return null
  const unit = entry.unit_display ? ` ${entry.unit_display}` : ''
  const num = (v: number) => (v * (entry.scale || 1)).toLocaleString('en', { maximumFractionDigits: 3 })
  const parts: string[] = []
  if (r.typical) parts.push(`${num(r.typical[0])}–${num(r.typical[1])}${unit}`)
  const hasMin = typeof r.min === 'number' && Number.isFinite(r.min)
  const hasMax = typeof r.max === 'number' && Number.isFinite(r.max)
  if (hasMin && hasMax) parts.push(`hard limit ${num(r.min!)}–${num(r.max!)}${unit}`)
  else if (hasMin) parts.push(`hard limit ≥ ${num(r.min!)}${unit}`)
  else if (hasMax) parts.push(`hard limit ≤ ${num(r.max!)}${unit}`)
  return parts.length ? parts.join(' · ') : null
}

/** The related entries the catalogue holds (an id it does not know is skipped). */
export function relatedEntries(entry: CatalogueEntry, catalogue: Catalogue): CatalogueEntry[] {
  return entry.related.map(id => findEntry(catalogue, id)).filter((e): e is CatalogueEntry => !!e && e.id !== entry.id)
}

/** Where the entry sits: "Generator · Size and limits". */
export function placeOf(entry: CatalogueEntry, catalogue: Pick<Catalogue, 'groups'>): string {
  const group = catalogue.groups.find(g => g.id === entry.group)?.plain_name
  return [componentLabel(entry.scope), group].filter(Boolean).join(' · ')
}

export interface BrowseItem { id: string; plain_name: string; short_help: string }

/** What a browse tab lists: asset types (not internal ones), groups, or functions. */
export function browseItems(catalogue: Catalogue, tab: BrowseTab): BrowseItem[] {
  if (tab === 'type') return catalogue.asset_types.filter(t => !t.internal).map(t => ({ id: t.id, plain_name: t.plain_name, short_help: '' }))
  if (tab === 'group') return [...catalogue.groups].sort((a, b) => a.order - b.order)
  return catalogue.functions
}

const byPlace = (catalogue: Catalogue) => {
  const groupOrder = new Map(catalogue.groups.map(g => [g.id, g.order]))
  return (a: CatalogueEntry, b: CatalogueEntry) =>
    (groupOrder.get(a.group) ?? 99) - (groupOrder.get(b.group) ?? 99) || a.order - b.order
    || a.plain_name.localeCompare(b.plain_name)
}

/**
 * The entries under one browse item, in sections: an asset type's entries by group;
 * a group's by class; a function's "Needed" ones, then the ones it also reads.
 */
export function browseEntries(catalogue: Catalogue, tab: BrowseTab, item: string): { title: string; entries: CatalogueEntry[] }[] {
  const sort = byPlace(catalogue)
  if (tab === 'function') {
    const needs = catalogue.entries.filter(e => e.functions[item] === 'needs').sort(sort)
    const uses = catalogue.entries.filter(e => e.functions[item] && e.functions[item] !== 'needs').sort(sort)
    return [{ title: 'Needed', entries: needs }, { title: 'Also used', entries: uses }].filter(s => s.entries.length)
  }
  if (tab === 'type') {
    const type = catalogue.asset_types.find(t => t.id === item)
    const of = catalogue.entries.filter(e => type?.components.includes(e.scope)).sort(sort)
    return catalogue.groups.slice().sort((a, b) => a.order - b.order)
      .map(g => ({ title: g.plain_name, entries: of.filter(e => e.group === g.id) }))
      .filter(s => s.entries.length)
  }
  const of = catalogue.entries.filter(e => e.group === item).sort(sort)
  const classes = [...new Set(of.map(e => e.scope))]
  return classes.map(c => ({ title: componentLabel(c), entries: of.filter(e => e.scope === c) }))
}
