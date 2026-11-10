import type { FlowHeld, FlowSectionId, FlowTarget } from '../../api/flow'
import { componentLabel } from '../../utils/componentLabels'
import { FIELD_LABELS } from '../../utils/fieldLabels'

// The words of a Guided section's list ("what this section holds"). Each word
// is the owning panel's: the class from `componentLabel`, a size from
// `FIELD_LABELS`, a load's peak from the Properties card, a series from the
// Time Series panel (parity: holdWords.test.ts).

/** A load's figure, as the Properties card names it. */
export const PEAK_LOAD = 'Peak load'

/** A time series, as the Time Series panel names its slot. */
export const SERIES_WORDS: Record<string, string> = {
  p_set: 'Profile',
  p_max_pu: 'Availability',
  p_min_pu: 'Must-run',
  marginal_cost: 'Marginal cost',
}

const number = (v: number): string => v.toLocaleString('en-GB', { maximumFractionDigits: 2 })

/** The row's kind: "Generator", or "Generator, solar" when it has a carrier. */
export function heldKind(h: FlowHeld): string {
  const kind = componentLabel(h.component)
  return h.carrier ? `${kind}, ${h.carrier}` : kind
}

/** The row's one figure in words, with its unit; null for a carrier. A missing
 *  figure says why instead of showing a dash. */
export function heldFigure(section: FlowSectionId, h: FlowHeld): string | null {
  if (section === 'carriers') return null
  if (section === 'profiles') {
    const word = h.field && (SERIES_WORDS[h.field] ?? FIELD_LABELS[h.field])
    return word ? `${word} time series` : 'Time series'
  }
  const label = section === 'loads' ? PEAK_LOAD : (h.field && FIELD_LABELS[h.field]) || 'Size'
  if (h.extendable) return `${label}: sized by the study`
  if (h.value === null) return `${label}: ${h.series ? 'its profile has no values yet' : 'not set'}`
  return `${label}: ${number(h.value)}${h.unit ? ` ${h.unit}` : ''}`
}

/** Where a row's "Edit ›" goes (UX S1). */
export type HeldHome =
  /** The component's own editor in the drawer over the card. */
  | { kind: 'drawer'; target: FlowTarget }
  /** The Time Series panel in the drawer over the section 6 card, on this row. */
  | { kind: 'series'; type: 'Load' | 'Generator' | 'Link'; name: string; field: string }
  /** No Guided editor: the section in Expert, with "Back to …" set. */
  | { kind: 'expert' }

const SERIES_TYPES = new Set(['Load', 'Generator', 'Link'])

export function heldHome(section: FlowSectionId, h: FlowHeld): HeldHome {
  if (h.component === 'Carrier') return { kind: 'expert' }
  if (section === 'profiles' && h.field && SERIES_TYPES.has(h.component)) {
    return { kind: 'series', type: h.component as 'Load' | 'Generator' | 'Link', name: h.entity, field: h.field }
  }
  return { kind: 'drawer',
    target: { section, panel: null, component: h.component, entity: h.entity, field: null } }
}

/** A group's title: the class as the Expert tables name their tabs. */
export const GROUP_TITLES: Record<string, string> = {
  Generator: 'Generators', StorageUnit: 'Storage', Store: 'Stores', Load: 'Loads', Link: 'Links',
  Carrier: 'Carriers',
}
export const groupTitle = (component: string): string => GROUP_TITLES[component] ?? componentLabel(component)

/** How many rows the list leaves out, in words; null when it is whole. */
export function heldRest(shown: number, total: number | undefined): string | null {
  if (total === undefined || total <= shown) return null
  return `Showing the first ${number(shown)} of ${number(total)}. The rest are in Expert.`
}

/** Rows in the order the server sent them, grouped by class. */
export function heldGroups(holds: FlowHeld[]): { component: string; rows: FlowHeld[] }[] {
  const groups: { component: string; rows: FlowHeld[] }[] = []
  for (const h of holds) {
    const g = groups.find(x => x.component === h.component)
    if (g) g.rows.push(h)
    else groups.push({ component: h.component, rows: [h] })
  }
  return groups
}

/** A group longer than this folds (UX Q2). */
export const FOLD_AFTER = 8
