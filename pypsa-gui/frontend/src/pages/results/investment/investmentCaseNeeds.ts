// What the investment case compares and what it needs to run, in plain words
// (template-e2e issue 22, slice S2). Guided's Investment tile and Expert's
// Investment case show the same words through InvestmentCaseRunPanel. Raw
// codes, asset ids and the server's 422 messages are never shown here.
import type { InvestmentCaseReportPayload, InvestmentCaseStudy } from '../../../api/types'
import { SolverInFlightError, StudyBusyError } from '../../../api/finance'
import table from './investmentCaseNeeds.json'

export type Need = 'solve' | 'connection' | 'cod' | 'finance'

/** The checklist's order: the order the inputs are set in. */
export const NEED_ORDER: readonly Need[] = ['solve', 'connection', 'cod', 'finance']

export const NEED_TEXT: Record<Need, { item: string; link: string }> = {
  solve: { item: 'A solved project', link: 'Run the solve' },
  connection: { item: "The site's connection, set for billing and priced by the comparison",
                link: 'Check the site connection' },
  cod: { item: 'A start date for each piece of new equipment', link: 'Set the start dates' },
  finance: { item: 'The finance inputs: tax rule and financing', link: 'Set the finance inputs' },
}

const NEED_OF: ReadonlyMap<string, Need> = new Map(
  NEED_ORDER.flatMap(need => (table[need] as string[]).map(code => [code, need] as const)))

/** The input a refusal code needs; null for a code with no plain fix (or none).
 *  A code's family is the part before its first ":" (`stream_unmapped:<stream>`). */
export function needOf(code: string | null | undefined): Need | null {
  if (!code) return null
  return NEED_OF.get(code.split(':')[0]) ?? null
}

export const BEFORE_RUN = 'Not run yet. The investment case compares the site with this design against '
  + "the same site without the owner's new equipment, paying for its energy as it does today."
export const RUNNING = 'Running the investment case…'
export const NEEDS_LEAD = 'The investment case needs:'
export const COULD_NOT_FINISH = 'The investment case could not finish.'
export const OPEN_IN_EXPERT = 'Open it in Expert for the details.'

/** The "could not finish" line: Guided points to Expert; Expert adds the run's own reason. */
export function couldNotFinish(mode: 'guided' | 'expert', detail: string | null): string {
  if (mode === 'guided') return `${COULD_NOT_FINISH} ${OPEN_IN_EXPERT}`
  return detail ? `The investment case could not finish: ${detail.replace(/\.$/, '')}.` : COULD_NOT_FINISH
}

const COMPARED = "Compared with: the same site's energy bill and connection costs without the owner's new equipment."
const NOT_RE_PLANNED = 'Existing equipment runs as it did; it is not re-planned.'
const NOT_COMPARED = "Not compared with anything. This is the design's own cash flow, not a saving against today."

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/** What a finished run compared, in our own words, keyed on the report's
 *  counterfactual basis (backend: services/finance/case.py, the
 *  `counterfactual` supply cost — the same site's bill, commodity and
 *  connection lines without the owner's investable assets; report.py names
 *  the basis `analytic` or `site_today`). The engine's sentence is never shown. */
export function comparedLine(report: InvestmentCaseReportPayload): string {
  const payload = report.sections?.project?.payload
  const p = isObj(payload) ? payload : {}
  const cf = isObj(p.counterfactual) ? p.counterfactual : null
  if (p.has_counterfactual === false || cf?.present === false) return NOT_COMPARED
  const basis = typeof cf?.counterfactual_basis === 'string' ? cf.counterfactual_basis : null
  return basis === 'analytic' ? `${COMPARED} ${NOT_RE_PLANNED}` : COMPARED
}

/** Why a run did not start or finish, in the words the panel shows. */
export type Blocker =
  | { kind: 'needs'; needs: Need[] }
  | { kind: 'wait'; text: string }
  /** No plain fix; `detail` is a stored run's own reason (Expert shows it). */
  | { kind: 'unknown'; detail: string | null }

const errorCode = (e: unknown): string | null => {
  if (e instanceof StudyBusyError) return e.code ?? null
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return isObj(d) && typeof d.code === 'string' ? d.code : null
}

/** What the inputs already show is missing (null: not known yet). Added to the
 *  refusal's own need, so the checklist names every input the user can see is
 *  missing, not only the first the server stopped at. */
export interface KnownGaps { solved?: boolean | null; financeSet?: boolean | null }

function withGaps(needs: Need[], gaps: KnownGaps): Need[] {
  const all = new Set(needs)
  if (gaps.solved === false) all.add('solve')
  if (gaps.financeSet === false) all.add('finance')
  return NEED_ORDER.filter(n => all.has(n))
}

/** The blocker a start refusal (`err`) or a refused / failed run (`study`)
 *  shows; null when neither blocks. A busy 409 (another study or a solve) is
 *  "Wait: <the server's sentence>"; a code with no plain fix is "unknown". */
export function blockerOf(err: unknown, study: InvestmentCaseStudy | null | undefined,
                          gaps: KnownGaps = {}): Blocker | null {
  if (err) {
    if (err instanceof SolverInFlightError) return { kind: 'wait', text: 'a solve is running; run the investment case after it.' }
    const need = needOf(errorCode(err))
    if (need) return { kind: 'needs', needs: withGaps([need], gaps) }
    if (err instanceof StudyBusyError) return { kind: 'wait', text: err.message }
    return { kind: 'unknown', detail: null }
  }
  if (study && ['refused', 'error', 'failed'].includes(String(study.status))) {
    const need = needOf(study.error_code ?? study.code)
    return need ? { kind: 'needs', needs: withGaps([need], gaps) }
      : { kind: 'unknown', detail: study.message || study.error || null }
  }
  return null
}
