import type { FlowHeld, FlowSectionId } from '../../api/flow'
import { FIELD_LABELS } from '../../utils/fieldLabels'
import { SERIES_WORDS } from './holdWords'

// One summary line above a Guided section's list (guided-results 03, spec §3),
// and the bars of section 5's chart. Display only: read from the flow's
// `holds`, never by a state, count, verdict, acknowledgement or fingerprint.
// "·" between fields, commas inside one. A sum adds only figures of the same
// field and unit; other units are listed beside it, never added.

/** The Load Profiles panel's name for a load's series (parity: holdSummary.test.ts). */
export const LOAD_PROFILES = 'load profiles'

const number = (v: number): string => v.toLocaleString('en-GB', { maximumFractionDigits: 2 })
const plural = (n: number, one: string, many = `${one}s`): string => `${number(n)} ${n === 1 ? one : many}`

/** A Generator the model adds (slack, lost production, buy-in). Only a real
 *  `true` is one: a missing or other value reads as an asset of the site. */
export const isHelper = (h: FlowHeld): boolean => (h as { helper?: unknown }).helper === true

/** A helper's kind by its name prefix, as the server names them
 *  (`adequacy/slack.py` SLACK_NAME_PREFIXES and LOST_SLACK_PREFIX,
 *  `process_products._NAME_PREFIX`; parity: holdSummary.test.ts). */
export const HELPER_WORDS: Record<string, string> = {
  __lost_: 'lost production',
  __buy_: 'buy-in',
  __voll_: 'unserved energy',
  voll_slack_: 'unserved energy',
  __dsr_: 'demand response',
  __dccurt_: 'curtailment',
}

/** A helper in plain words, never its engine id: "lost production (steel)".
 *  A name with no known prefix (flagged by its role) reads "added by the model (<name>)". */
export function helperName(name: string): string {
  const prefix = Object.keys(HELPER_WORDS).find(p => name.startsWith(p))
  return prefix ? `${HELPER_WORDS[prefix]} (${name.slice(prefix.length)})` : `added by the model (${name})`
}

/** A series in the owning panel's words, never the raw field. */
export function seriesWord(h: FlowHeld): string {
  if (h.field === 'p_set' && h.component === 'Load') return LOAD_PROFILES
  const word = h.field ? (SERIES_WORDS[h.field] ?? FIELD_LABELS[h.field]) : undefined
  return word ? word.toLowerCase() : 'other'
}

/** Rows with a fixed, known figure: not sized by the study, not a helper. */
const fixed = (holds: FlowHeld[]) =>
  holds.filter(h => !isHelper(h) && !h.extendable && h.value !== null && Number.isFinite(h.value))

/** "52 MW, 80 MWh": one figure per field and unit, in first-seen order. */
function perUnit(rows: FlowHeld[], combine: (a: number, b: number) => number): string[] {
  const sums = new Map<string, { unit: string; v: number }>()
  for (const h of rows) {
    if (!h.unit) continue
    const k = `${h.field ?? ''}|${h.unit}`
    const s = sums.get(k)
    sums.set(k, { unit: h.unit, v: s ? combine(s.v, h.value as number) : (h.value as number) })
  }
  return [...sums.values()].map(s => `${number(s.v)} ${s.unit}`)
}

function counted<T extends string>(items: T[]): string[] {
  const n = new Map<T, number>()
  for (const i of items) n.set(i, (n.get(i) ?? 0) + 1)
  return [...n].map(([w, c]) => `${number(c)} ${w === LOAD_PROFILES && c === 1 ? 'load profile' : w}`)
}

/** The section's summary line; null when it holds nothing (its own empty state shows). */
export function holdSummary(section: FlowSectionId, holds: FlowHeld[], total?: number): string | null {
  if (holds.length === 0) return null
  const all = Math.max(total ?? 0, holds.length)
  const fields: string[] = []
  if (section === 'carriers') {
    const names = holds.map(h => h.entity)
    const shown = names.slice(0, 6).join(', ')
    fields.push(`${plural(all, 'carrier')}: ${shown}${all > 6 ? `, and ${number(all - 6)} more` : ''}`)
  } else if (section === 'loads') {
    fields.push(plural(all, 'load'))
    const peaks = perUnit(fixed(holds), (a, b) => (Math.abs(b) > Math.abs(a) ? b : a))
    if (peaks.length) fields.push(`largest peak ${peaks.join(', ')}`)
  } else if (section === 'assets') {
    const helpers = holds.filter(isHelper).length
    fields.push(`${plural(all, 'asset')}${helpers ? ` incl. ${plural(helpers, 'helper')}` : ''}`)
    const own = holds.filter(h => !isHelper(h))
    const sized = own.filter(h => h.extendable).length
    const sums = perUnit(fixed(own), (a, b) => a + b)
    const noUnit = fixed(own).filter(h => !h.unit).length
    const size = [sums.length ? `${sums.join(', ')} fixed` : '',
      noUnit ? `${number(noUnit)} with no known unit` : '',
      sized ? `${number(sized)} sized by the study` : ''].filter(Boolean)
    if (size.length) fields.push(size.join(', '))
  } else if (section === 'profiles') {
    fields.push(plural(all, 'series', 'series'))
    fields.push(counted(holds.map(seriesWord)).join(', '))
  } else {
    return null
  }
  if (all > holds.length) fields.push(`from the first ${number(holds.length)} of ${number(all)}`)
  return fields.join(' · ')
}

export interface FixedChart { unit: string; bars: { carrier: string; value: number }[] }

/** Section 5's bars: fixed capacity per carrier, one chart per real unit (a
 *  tonne bus never shares the MW axis). A unit with one carrier has no chart. */
export function fixedCharts(holds: FlowHeld[]): FixedChart[] {
  const charts = new Map<string, Map<string, number>>()
  for (const h of fixed(holds)) {
    if (!h.unit) continue
    const bars = charts.get(h.unit) ?? new Map<string, number>()
    const c = h.carrier ?? 'no carrier'
    bars.set(c, (bars.get(c) ?? 0) + (h.value as number))
    charts.set(h.unit, bars)
  }
  return [...charts]
    .map(([unit, bars]) => ({ unit, bars: [...bars].map(([carrier, value]) => ({ carrier, value })) }))
    .filter(c => c.bars.length > 1)
}
