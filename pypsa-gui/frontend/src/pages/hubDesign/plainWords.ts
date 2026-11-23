// Plain words for the engine terms a review finding's title or effect may
// carry (P24-FE gate B3, spec §5.8). The raw prose stays available, verbatim,
// in the finding's "Why" evidence list; the card itself shows this version.
import { SECTION_LABEL } from './sectionLabels'
import { displayName } from '../../utils/displayName'
import { fmtCurrency } from '../results/shared'
import { kpiFigure, splitValueUnit } from '../../utils/format'
import { GROUP_TITLES } from './holdWords'

/** Links as the Guided assets list names them (UX: one word per concept). */
const LINKS = GROUP_TITLES.Link.toLowerCase()

const RULES: [RegExp, string][] = [
  // Whole review phrasings first (eh_review.py: fmea_dominant_mode).
  [/\bcarries (\d+(?:\.\d+)?%) of ranked Link risk\b/g, `accounts for $1 of the outage risk from ${LINKS}`],
  [/\bprice generic N\s*\/\s*N\+1\s*\/\s*storage scenarios \(indicative\)/g,
    'compare the cost with and without a spare unit or extra storage (rough estimate)'],
  [/\boutage-driven\b/g, 'caused by equipment outages'],
  [/\bLOLE\b/g, 'expected shortfall'],
  [/\bENS target\b/g, 'energy target'],
  [/\bENS\b/g, 'unserved energy'],
  [/\s*‱/g, ' parts per 10 000'],
  [/\bp_nom_min\b/g, 'minimum size'],
  [/\bp_nom_max\b/g, 'maximum size'],
  [/\bp_nom\w*/g, 'size'],
  [/\(?\bdtc_stress\s*\+\s*dtc_planning\b\)?/g, '(a grid-loss test and a plan for running without the grid)'],
  [/\bdtc_planning\b/g, 'a plan for running without the grid'],
  [/\bdtc_stress\b/g, 'a grid-loss test'],
  [/\ban N\+1 unit\b/g, 'a spare unit'],
  [/\bN\s*\/\s*N\+1\b/g, 'with and without a spare unit'],
  [/\bN\+1\b/g, 'one spare unit'],
  [/\bVOLL\s*>\s*0\b/g, 'a price above zero'],
  [/\bVOLL\b/g, 'the price of undelivered energy'],
  [/\b(?:the )?frontier\b/g, 'the cost-versus-reliability check'],
  [/\b(?:the )?fmea_top\b/g, 'the top-risk check'],
  [/\bSCR\b/g, 'grid-strength ratio'],
  [/\bMC\b/g, 'simulation'],
  // P26: the inconclusive re-run effect ("… with 1000 draws … confidence interval").
  [/\b(\d+) draws\b/g, '$1 runs'],
  [/\b1 draw\b/g, '1 run'],
  [/\bconfidence interval\b/g, 'range of the estimate'],
  [/\bstages?\b/g, 'step'],
  // P30 (C12): an amount of energy ("… is lost: 12.5 MWh"); a price per MWh
  // ("€/MWh") has no number before the unit and is left alone.
  [/\b(\d+(?:\.\d+)?) MWh\b/g, '$1 megawatt-hours'],
]

/** A finding title or action effect with the known engine terms replaced. */
export function plainWords(text: string): string {
  // "frontier: not established" → "Cost versus reliability could not be worked out"
  const ne = /^(\w+): not established$/.exec(text.trim())
  if (ne) return `${SECTION_LABEL[ne[1]] ?? 'A part of the study'} could not be worked out`
  // The dominant-risk line names its unit in plain words (UX audit M5).
  let out = text.replace(/\b([a-z0-9]+(?:_[a-z0-9]+)+) (carries \d+(?:\.\d+)?% of ranked Link risk)/g,
    (_m, id: string, rest: string) => `${displayName(id)} ${rest}`)
  // The review states the share of ALL ranked risk beside the Link share
  // (asset-investment walkthrough 2026-11-17, issue 04): that one leads.
  out = out.replace(/ carries (\d+(?:\.\d+)?%) of ranked Link risk \((\d+(?:\.\d+)?%) of all ranked risk\)/g,
    (_m, link: string, all: string) =>
      `: ${all} of the outage risk, and ${link === '100%' ? 'all' : link} of the risk from ${LINKS}`)
  for (const [re, plain] of RULES) out = out.replace(re, plain)
  // A pass held back by a feeder bound names the bus and the feeder by id
  // (GK A F6'): shown as the project names them.
  if (out.startsWith('Certification not decided: ')) out = plainIds(out)
  return out
}

/** The plain advice for a dominant-risk finding on the only feeder to a
 *  must-stay-on bus (the review drops the generic re-run for it); null for
 *  any other finding. Buses are named as the project shows them. */
export function feederAdvice(f: { id: string; evidence?: Record<string, unknown> | null }): string | null {
  if (f.id !== 'fmea_dominant_mode') return null
  const buses = f.evidence?.feeds_critical_buses
  const mode = f.evidence?.mode
  if (!Array.isArray(buses) || buses.length === 0 || typeof mode !== 'string') return null
  const at = buses.map(b => displayName(String(b))).join(', ')
  return `Add a second feeder or backup supply at ${at}. A spare unit or storage elsewhere cannot supply ${at} while ${displayName(mode)} is out.`
}

/** A template note with its raw component ids (`it_bus`) in plain words
 *  (UX audit M5): any snake_case word; other words are left as written. */
export function plainIds(text: string): string {
  return text.replace(/\b[a-z0-9]+(?:_[a-z0-9]+)+\b/g, id => displayName(id))
}

/** Money in a sentence at the KPI tile's rounding (UX audit M8):
 *  "€657 k", never "€657.00 k". */
export function kpiMoney(eur: number): string {
  const raw = splitValueUnit(fmtCurrency(eur))
  const p = kpiFigure(raw.value, raw.unit)
  return p.unit ? `${p.value} ${p.unit}` : p.value
}

/** Evidence values for reading: long decimals rounded to four significant
 *  digits (lists too); everything else as JSON / text. */
export function evidenceValue(v: unknown): string {
  const n = (x: number) => (Number.isInteger(x) ? String(x) : String(Number(x.toPrecision(4))))
  if (typeof v === 'number' && Number.isFinite(v)) return n(v)
  if (Array.isArray(v) && v.every(x => typeof x === 'number' && Number.isFinite(x))) {
    return `[${(v as number[]).map(n).join(', ')}]`
  }
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

/** The cost tile's label. A multi-period study's cost is the discounted total over every investment
 *  period, not a year's cost (UX review N3, owner decision B): say so, with the plan's years when
 *  known. The engine reports the basis (`period_basis`); no yearly figure is derived from it. */
export function costTileLabel(periodBasis: string | null | undefined, periods: number[] | null | undefined): string {
  if (periodBasis !== 'npv_multi_period') return 'Yearly cost of this design (before any shortfall costs)'
  const years = periods && periods.length > 1 ? `, ${Math.min(...periods)}–${Math.max(...periods)}` : ''
  return `Cost over the plan${years} (discounted, before any shortfall costs)`
}

/** A not-run reason or study note in plain words (issue 19, RAW-NAMES-1): the study's run limit, the import
 *  model and the Class-B sweep, then any snake_case id. The raw text stays in the report's evidence. */
const REASON_RULES: [RegExp, string][] = [
  [/\bbudget_solves exhausted \((\d+)\/(\d+)\)/g, 'the study\'s run limit was reached ($1 of $2 runs used)'],
  [/\bbudget_solves exhausted\b/g, 'the study\'s run limit was reached'],
  [/\(import_model=\w+,\s*import_firmness=\w+\)/g, ''],
  [/\(?Class-B(?: Link)? sweep\)?/g, 'link-by-link risk sweep'],
  [/\bClass-B(?:-eligible)? Links?\b/g, 'links that carry the outage risk'],
  [/\bdsr_opt_in=True\b/g, 'demand response switched on'],
]
export function plainReason(text: string): string {
  let out = text
  for (const [re, plain] of REASON_RULES) out = out.replace(re, plain)
  // An id inside backticks or double quotes is a name the reader may have to type: left as written.
  out = out.split(/(`[^`]*`|"[^"]*")/).map((seg, i) => (i % 2 ? seg : plainIds(seg))).join('')
  return out.replace(/\s{2,}/g, ' ').trim()
}
