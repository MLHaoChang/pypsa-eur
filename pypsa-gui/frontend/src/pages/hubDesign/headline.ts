// The Results card's one-sentence verdict (guided-mode spec §5.5). The
// numbers are the review's — `summary` / `findings[*].evidence`, read by
// `review_report` from the report — and the report's own `mc_lole_h`; nothing
// is re-computed here, only rounded for reading.
import type { EhReferenceDesignReport, EhReview } from '../../api/simulation'
import { certificationHeadline, certificationPayload, fmeaTopRows } from '../results/EhReferenceDesignPanel'
import { displayName } from '../../utils/displayName'
import { verdictWord } from './verdictWords'
import { sectionMeta } from '../../layout/flowSections'
import { MAX_GOAL_DRAWS } from '../../hooks/useReliabilityGoal'

type OkReview = Extract<EhReview, { status: 'ok' }>

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null

/** Python's `{:g}`: up to six significant digits, no trailing zeros. */
const g = (v: number): string => String(Number(v.toPrecision(6)))

/** The per-year interval: the report's certification block converts the
 *  horizon interval exactly as the Expert panel does; without a horizon the
 *  finding's evidence is read as it is. */
function interval(review: OkReview, report: EhReferenceDesignReport | null): [number, number] | null {
  const ci = report ? certificationHeadline(report)?.ci ?? null : null
  if (ci) return ci
  for (const f of review.findings ?? []) {
    const raw = f.evidence?.lole_ci_per_horizon
    if (Array.isArray(raw) && num(raw[0]) != null && num(raw[1]) != null) {
      return [raw[0] as number, raw[1] as number]
    }
  }
  return null
}

/** A ranked risk's name in plain words (UX S6, G1): a component id through
 *  displayName; a common-mode row (`common_mode:a,b`) as its links failing
 *  together. */
export function riskName(name: string): string {
  const cm = /^common_mode:(.+)$/.exec(name)
  if (cm) return `${cm[1].split(',').map(displayName).join(' and ')} failing together`
  return displayName(name)
}

function topRisk(report: EhReferenceDesignReport | null): string {
  const row = report ? fmeaTopRows(report)[0] : undefined
  return row ? riskName(String(row.name || row.mode_id)) : "the plan's energy limit"
}

/** Three significant digits, as the study start's refusal words the step. */
const g3 = (v: number): string => String(Number(v.toPrecision(3)))

const runs = (k: number): string => `${k} simulation run${k === 1 ? '' : 's'}`

/** The below-floor sentence (template-e2e 21), or null when the report does
 *  not say the goal is under the resolution floor. */
function belowFloor(report: EhReferenceDesignReport | null, target: number): string | null {
  const p = report ? certificationPayload(report) : null
  if (p?.below_floor !== true) return null
  const floor = num(p.resolution_floor_h)
  const n = num(p.n_samples)
  const years = num(p.horizon_years) ?? 1
  if (floor == null || n == null || !(years > 0)) return null
  const goal = sectionMeta('scenarios')
  const where = `section ${goal.number}, ${goal.label}`
  const seen = `with ${runs(n)}, the smallest shortfall it can show is ${g3(floor / years)} h/yr`
  const need = num(p.min_draws)
  if (need != null && need > MAX_GOAL_DRAWS)
    return `${verdictWord('inconclusive')}: ${seen}, more than your ${g(target)} h/yr goal, and even ${MAX_GOAL_DRAWS} runs (the most allowed) could not decide it. Allow more shortfall (${where}) to decide it.`
  return need != null
    ? `${verdictWord('inconclusive')}: ${seen}, more than your ${g(target)} h/yr goal. Set at least ${need} runs per check (${where}) to decide it.`
    : `${verdictWord('inconclusive')}: ${seen}, and a ${g(target)} h/yr goal allows none. Allow some shortfall (${where}) to decide it.`
}

export function headline(review: OkReview, report: EhReferenceDesignReport | null): string {
  const s = review.summary ?? {}
  const verdict = typeof s.verdict === 'string' ? s.verdict : null
  const lole = num(s.mc_lole_h_per_year) ?? num(report?.mc_lole_h)
  const target = num(s.target_lole_h)

  if (verdict === 'fail' && lole != null && target != null) {
    return `${verdictWord('fail')}: about ${lole.toFixed(1)} h/yr of shortfall vs a ${g(target)} h/yr goal, driven by ${topRisk(report)}`
  }
  // GK A F6': a pass held back by a must-stay-on bus behind one feeder is
  // not a sampling question; more runs would not settle it.
  const fb = s.feeder_bound
  if (verdict === 'inconclusive' && fb && target != null) {
    const names = (xs: string[]) => xs.map(displayName).join(' and ')
    const pk = fb.pockets ?? []
    if (pk.length) {
      const buses = names(pk.flatMap((p) => p.buses))
      const feeders = pk.flatMap((p) => p.feeders)
      const bound = pk.reduce((a, p) => a + (num(p.bound_h_per_year) ?? 0), 0)
      const cut = `${verdictWord('inconclusive')}: ${buses} could be cut off up to ${bound.toFixed(1)} h/yr while ${names(feeders)} ${feeders.length > 1 ? 'are' : 'is'} out`
      // GK R2: a feeder without outage data is taken as always out; its data
      // is the first thing that would settle it.
      const unread = [...new Set(pk.flatMap((p) => p.no_data ?? []))]
      if (unread.length) {
        return `${cut}, against the ${g(target)} h/yr goal. ${names(unread)}'s outage rate${unread.length > 1 ? 's are' : ' is'} not known, so ${unread.length > 1 ? 'they are' : 'it is'} taken as always out. Outage data for ${names(unread)}, a second feeder or a more reliable one would settle it.`
      }
      return `${cut} (${pk.length > 1 ? 'their' : 'its'} own backup not counted), over the ${g(target)} h/yr goal: a second feeder or a more reliable one would settle it.`
    }
    return `${verdictWord('inconclusive')}: ${names(fb.chained ?? [])} reaches a must-stay-on bus only through a bus with no supply of its own, which this check cannot bound: a feeder straight from a supplied bus would settle it.`
  }
  // template-e2e 21: a goal under the smallest shortfall the run can show is
  // not a straddle; more runs settle it only up to the stated count.
  const floorLine = verdict === 'inconclusive' && target != null ? belowFloor(report, target) : null
  if (floorLine) return floorLine
  if (verdict === 'inconclusive' && target != null) {
    const ci = interval(review, report)
    if (ci) {
      return `${verdictWord('inconclusive')}: the shortfall estimate (${ci[0].toFixed(1)}–${ci[1].toFixed(1)} h/yr) straddles the ${g(target)} h/yr goal: more simulation runs would settle it.`
    }
  }
  if (verdict === 'pass' && lole != null && target != null) {
    // A computed zero (below the one-decimal rounding) says so (UX audit M7).
    return lole < 0.05 ? `${verdictWord('pass')}: no shortfall expected, under the ${g(target)} h/yr goal.`
      : `${verdictWord('pass')}: about ${lole.toFixed(1)} h/yr of shortfall, under the ${g(target)} h/yr goal.`
  }
  if (lole != null && target == null) {
    return lole < 0.05 ? 'No reliability goal was set: the study expects no shortfall. Set a goal to certify.'
      : `No reliability goal was set: the study reports ${lole.toFixed(1)} h/yr of shortfall. Set a goal to certify.`
  }
  // No goal and no shortfall number (decided at P24-FE, spec §5.5): the
  // engine's note ("no LOLE target …") would be jargon; ask for a goal.
  if (lole == null && target == null) {
    // The goal lives in the Scenarios section; name it from the flow's own
    // numbering so the sentence never points at a stale step (walkthrough 05a).
    const goal = sectionMeta('scenarios')
    return `No reliability goal is set for this site, so the study did not certify it: set an allowed shortfall in section ${goal.number}, ${goal.label}, to get a verdict.`
  }
  const note = report?.sections?.certification?.note
  return `The study could not certify reliability: ${
    note ? String(note) : 'the reliability check was not part of this run.'}`
}
