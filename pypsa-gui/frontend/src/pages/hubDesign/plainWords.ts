// Plain words for the engine terms a review finding's title or effect may
// carry (P24-FE gate B3, spec §5.8). The raw prose stays available, verbatim,
// in the finding's "Why" evidence list; the card itself shows this version.
import { SECTION_LABEL } from './sectionLabels'

const RULES: [RegExp, string][] = [
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
  [/\bN\s*\/\s*N\+1\b/g, 'with and without a spare unit'],
  [/\bN\+1\b/g, 'one spare unit'],
  [/\bSCR\b/g, 'grid-strength ratio'],
  [/\bMC\b/g, 'simulation'],
  [/\bstages?\b/g, 'step'],
]

/** A finding title or action effect with the known engine terms replaced. */
export function plainWords(text: string): string {
  // "frontier: not established" → "Cost versus reliability could not be worked out"
  const ne = /^(\w+): not established$/.exec(text.trim())
  if (ne) return `${SECTION_LABEL[ne[1]] ?? 'A part of the study'} could not be worked out`
  let out = text
  for (const [re, plain] of RULES) out = out.replace(re, plain)
  return out
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
