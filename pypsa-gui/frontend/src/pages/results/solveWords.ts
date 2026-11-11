// The solve's outcome in the words of a chart's source line (chart theme,
// ui-modern 02): plain words, no solver terms (#1194).
import { REFERENCE_CONDITION_WORDS } from './OperationalGap'

/** The solve conditions that mean success (backend `failure_taxonomy._SUCCESS_CONDITIONS`, plus the AC power flow's own). */
// A run whose AC power flow also converged reports its own success condition.
const SOLVED_CONDITIONS = new Set(['optimal', 'ok', 'lopf+ac_pf_ok', 'ac_pf_ok'])

/**
 * The optimisation's own condition is the part before ";": a later stage's
 * failure ("optimal; ac_pf_failed: …") does not unsolve the dispatch drawn.
 */
export function solveWords(condition: string | null | undefined): string {
  const own = condition?.split(';')[0].trim()
  if (own && SOLVED_CONDITIONS.has(own)) return 'solved'
  return (own && REFERENCE_CONDITION_WORDS[own]) || 'did not solve'
}
