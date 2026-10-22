// Plain words for the shipped cost references. Internal tokens (capacity-basis codes,
// technology ids, parameter keys) never reach the screen; these helpers are the one
// place they are turned into words.
import type { CapacityBasisCode, Confirmation, GridConnection } from '../../../api/libraryReferences'

const TECHNOLOGY: Record<string, string> = {
  'onwind': 'Onshore wind',
  'solar-utility': 'Utility-scale PV, fixed tilt',
  'solar-rooftop-commercial': 'Commercial and industrial rooftop PV',
  'electrolysis-aec-100mw': 'Alkaline electrolyser (AEC), 100 MW',
  'electrolysis-pem-100mw': 'PEM electrolyser, 100 MW',
}

export function technologyWords(technology: string): string {
  return TECHNOLOGY[technology] ?? technology.replace(/[-_]/g, ' ')
}

const BASIS: Record<CapacityBasisCode, string> = {
  output_electric: 'per MW electrical output',
  output_electric_ac: 'per MW AC output',
  output_electric_dc: 'per MW DC capacity',
  input_electric: 'per MW electrical input',
  output_product: 'per MW product output',
  storage_energy: 'per MWh stored',
  not_applicable: 'per unit',
}

export function basisWords(code: CapacityBasisCode): string {
  return BASIS[code] ?? 'per MW'
}

/** The class a reference applies to: output-priced to a generator, input-priced to a link. */
export function targetClass(code: CapacityBasisCode): 'Generator' | 'Link' {
  return code === 'input_electric' ? 'Link' : 'Generator'
}

export function gridWords(g: GridConnection): string {
  return g === true ? 'Includes grid connection' : g === false ? 'Excludes grid connection'
    : 'Grid connection not stated'
}

const PARAMETER: Record<string, string> = {
  overnight: 'Investment cost',
  overnight_cost: 'Investment cost',
  fom_share: 'Fixed O&M, share of investment per year',
  fom_cost: 'Fixed O&M per year',
  lifetime: 'Lifetime',
  efficiency: 'Efficiency (lower heating value)',
}

export function parameterWords(key: string): string {
  return PARAMETER[key] ?? key.replace(/_/g, ' ')
}

/** "CC-BY-4.0" → "CC BY 4.0". */
export function licenceWords(licence: string): string {
  return licence.replace(/-/g, ' ')
}

/** The short credit badge, e.g. "DEA · CC BY 4.0". */
export function creditBadge(publisher: string, licence: string): string {
  const short = /danish energy agency/i.test(publisher) ? 'DEA' : publisher
  return `${short} · ${licenceWords(licence)}`
}

const ABBREVIATIONS: Array<[RegExp, string]> = [
  [/\bFOM\b/, 'fixed O&M (FOM)'],
  [/\bVOM\b/, 'variable O&M (VOM)'],
  [/\bLHV\b/, 'lower heating value (LHV)'],
]

/** Source notes with each abbreviation spelled out the first time it appears in the list. */
export function spellOutNotes(notes: string[]): string[] {
  const seen = new Set<string>()
  return notes.map(note => ABBREVIATIONS.reduce((text, [re, words]) => {
    if (seen.has(re.source) || !re.test(text)) return text
    seen.add(re.source)
    return text.replace(re, words)
  }, note))
}

export const CONFIRM_LABELS: Record<Confirmation, string> = {
  dc_ac: 'Use the AC-output cost for this generator',
  grid_connection: 'Use it, knowing the grid connection may be counted twice',
  money_year: 'Use it as it is, without adjusting for inflation',
}

export function fmtNumber(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  const digits = abs >= 1000 ? 0 : abs >= 1 ? 2 : 4
  return v.toLocaleString(undefined, { maximumFractionDigits: digits })
}

/** A value with its unit and, for money, its money year: "556 221 EUR/MW (2025 EUR)". */
export function valueWords(value: number, unit: string, currencyYear: number | null | undefined,
  currency = 'EUR'): string {
  const money = currencyYear != null ? ` (${currencyYear} ${currency})` : ''
  return `${fmtNumber(value)} ${unit}${money}`
}

/** A source in words: a workbook cell "Publisher; Sheet!D17" → `Publisher, sheet "Sheet", cell D17`;
 *  a file path after the publisher ("…, inputs/technology_data_for_el_and_dh.xlsx") → "… technology data". */
export function sourceWords(source: string): string {
  const cell = /^(.*?);\s*(.+)!([A-Z]{1,3}\d{1,5})$/.exec(source)
  if (cell) return `${cell[1]}, sheet "${cell[2]}", cell ${cell[3]}`
  const [publisher, rest] = source.split(/,\s*/, 2)
  if (rest && /[\\/]|\.xlsx?$|\.csv$/i.test(rest)) return `${publisher} technology data`
  return source
}
