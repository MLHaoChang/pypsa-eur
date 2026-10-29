/** Plain words for the status and family an Activity row reports. */
export const ACTIVITY_STATUS_WORDS: Record<string, string> = {
  running: 'Running', queued: 'Queued', paused: 'Paused', completed: 'Done', failed: 'Failed',
  cancelled: 'Cancelled', partial: 'Partly done', needs_review: 'Needs your review',
  waiting_for_approval: 'Waiting for your approval', waiting_for_input: 'Waiting for your answer',
  decision_recorded: 'Decision recorded', unavailable: 'Status unavailable',
}

export const ACTIVITY_FAMILY_WORDS: Record<string, string> = {
  run: 'Study run', foreground_simulation: 'Simulation', solve_queue: 'Queued solve', gridspine: 'Grid study',
  assistant_task: 'Assistant task', confirmation: 'Confirmation', source_gap: 'Source gap',
  question: 'Question',
}

const spaced = (v: string): string => v.replace(/_/g, ' ')
export const activityStatusWord = (s: string): string => ACTIVITY_STATUS_WORDS[s] ?? spaced(s)
export const activityFamilyWord = (f: string): string => ACTIVITY_FAMILY_WORDS[f] ?? spaced(f)

export const ACTIVITY_MODE_WORDS: Record<string, string> = { foreground: 'Foreground', background: 'Background' }
export const activityModeWord = (m: string): string => ACTIVITY_MODE_WORDS[m] ?? (spaced(m).charAt(0).toUpperCase() + spaced(m).slice(1))

/** Plain words for the source's own end state where it says more than the plain status (met, refused, expired…). */
export const NATIVE_OUTCOME_WORDS: Record<string, string> = {
  met: 'Target met', budget_exhausted: 'Budget used up', unreachable: 'Could not be reached', refused: 'Refused',
  aborted: 'Stopped', rejected: 'Denied', expired: 'Expired', revoked: 'Withdrawn',
  followed_up: 'Followed up', superseded: 'Replaced',
}

/** The source's own state in plain words, or null when it adds nothing to the plain status word. */
export function nativeOutcomeWord(native: string | undefined, status: string): string | null {
  if (!native || native === 'unknown' || native === status) return null
  const word = NATIVE_OUTCOME_WORDS[native] ?? (spaced(native).charAt(0).toUpperCase() + spaced(native).slice(1))
  return word.toLowerCase() === activityStatusWord(status).toLowerCase() ? null : word
}
