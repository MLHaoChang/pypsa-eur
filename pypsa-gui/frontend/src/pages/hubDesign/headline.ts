// The Results card's one-sentence verdict (guided-mode spec §5.5). The
// numbers are the review's — `summary` / `findings[*].evidence`, read by
// `review_report` from the report — and the report's own `mc_lole_h`; nothing
// is re-computed here, only rounded for reading.
import type { EhReferenceDesignReport, EhReview } from '../../api/simulation'
import { certificationHeadline, fmeaTopRows } from '../results/EhReferenceDesignPanel'

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

function topRisk(report: EhReferenceDesignReport | null): string {
  const row = report ? fmeaTopRows(report)[0] : undefined
  return row ? String(row.name || row.mode_id) : "the plan's energy limit"
}

export function headline(review: OkReview, report: EhReferenceDesignReport | null): string {
  const s = review.summary ?? {}
  const verdict = typeof s.verdict === 'string' ? s.verdict : null
  const lole = num(s.mc_lole_h_per_year) ?? num(report?.mc_lole_h)
  const target = num(s.target_lole_h)

  if (verdict === 'fail' && lole != null && target != null) {
    return `Not certified: about ${lole.toFixed(0)} h/yr of shortfall vs a ${g(target)} h/yr goal — driven by ${topRisk(report)}`
  }
  if (verdict === 'inconclusive' && target != null) {
    const ci = interval(review, report)
    if (ci) {
      return `Not decided: the shortfall estimate (${ci[0].toFixed(0)}–${ci[1].toFixed(0)} h/yr) straddles the ${g(target)} h/yr goal — more simulation runs would settle it.`
    }
  }
  if (verdict === 'pass' && lole != null && target != null) {
    return `Certified: about ${lole.toFixed(1)} h/yr of shortfall, under the ${g(target)} h/yr goal.`
  }
  if (lole != null && target == null) {
    return `No reliability goal was set — the study reports ${lole.toFixed(1)} h/yr of shortfall. Set a goal to certify.`
  }
  // No goal and no shortfall number (decided at P24-FE, spec §5.5): the
  // engine's note ("no LOLE target …") would be jargon; ask for a goal.
  if (lole == null && target == null) {
    return 'No reliability goal is set for this site, so the study did not certify it — set an allowed shortfall in step 3 (Goal) to get a verdict.'
  }
  const note = report?.sections?.certification?.note
  return `The study could not certify reliability: ${
    note ? String(note) : 'the reliability check was not part of this run.'}`
}
