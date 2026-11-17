// "Offer the cheaper battery duration the study already found" (walkthrough
// asset-investment improvement 1, owner decision WALK-IMPROVE-1). A read of
// data the study already wrote: the review's `levers_best_option` finding,
// the levers table and the report's sizing rows. Nothing is computed beyond
// picking and formatting. The Results card's Verdict and the Improve card
// show the same sentence.
import type {
  EhLeverTable, EhReferenceDesignReport, EhReviewFinding,
} from '../../api/simulation'
import { money } from '../../utils/money'
import { notBuilt, sizedAssets } from '../results/SizedDesignTable'

/** The review finding that names the cheapest lever option. */
export const LEVERS_FINDING = 'levers_best_option'

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const same = (a: number, b: number) => Math.abs(a - b) < 1e-6
const hours = (h: number) => h.toLocaleString('en-GB', { maximumFractionDigits: 2 })
// "An 8 h", "An 11 h", "An 18 h"; "A 4 h", "A 24 h".
const article = (h: string) => (/^(8|1[18](?!\d))/.test(h) ? 'An' : 'A')

/** The review's best-lever finding, if any. */
export function leversFinding(findings: EhReviewFinding[] | null | undefined): EhReviewFinding | null {
  return findings?.find(f => f.id === LEVERS_FINDING) ?? null
}

/** The design battery's duration in hours: the one `max_hours` its built
 *  StorageUnits share. Null when none is built or their durations differ. */
export function designDurationHours(report: EhReferenceDesignReport | null | undefined): number | null {
  const values = sizedAssets(report)
    .filter(a => a.component === 'StorageUnit' && !notBuilt(a) && isNum(a.max_hours))
    .map(a => a.max_hours as number)
  if (values.length === 0) return null
  return values.every(v => same(v, values[0])) ? values[0] : null
}

/** "A 24 h battery also meets the energy-not-served cap for € 3.1 m (design:
 *  4 h, € 3.5 m)", or null. Shown only when the best option is a storage
 *  duration other than the design's, the levers table says it meets the
 *  energy-not-served cap, and it costs less than the design. The design's
 *  cost is its own lever option's, else the report's cost at target. */
export function cheaperDuration(
  findings: EhReviewFinding[] | null | undefined,
  levers: EhLeverTable | null | undefined,
  report: EhReferenceDesignReport | null | undefined,
): string | null {
  const ev = leversFinding(findings)?.evidence
  if (!ev || ev.kind !== 'storage_duration') return null
  const best = ev.value
  const bestCost = ev.cost_at_target_eur
  if (!isNum(best) || !isNum(bestCost)) return null
  const design = designDurationHours(report)
  if (design == null || same(best, design)) return null
  const options = (levers?.options ?? [])
    .filter(o => o.kind === 'storage_duration' && isNum(o.value))
  // `meets_target` is against the energy-not-served cap; null is not a yes.
  if (options.find(o => same(o.value, best))?.meets_target !== true) return null
  const own = options.find(o => same(o.value, design))?.cost_at_target_eur
  const designCost = isNum(own) ? own : report?.cost_at_target_eur
  if (!isNum(designCost) || !(bestCost < designCost)) return null
  const bestText = money(bestCost, 'EUR')
  const designText = money(designCost, 'EUR')
  // Two figures that round the same would read as no saving.
  if (bestText === designText) return null
  const h = hours(best)
  return `${article(h)} ${h} h battery also meets the energy-not-served cap for ${bestText}`
    + ` (design: ${hours(design)} h, ${designText})`
}
