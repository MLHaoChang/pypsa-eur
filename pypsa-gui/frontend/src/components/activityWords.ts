/** Plain words for the status and family an Activity row reports. */
export const ACTIVITY_STATUS_WORDS: Record<string, string> = {
  running: 'Running', queued: 'Queued', paused: 'Paused', completed: 'Done', failed: 'Failed',
  cancelled: 'Cancelled', partial: 'Partly done', needs_review: 'Needs your review',
  waiting_for_approval: 'Waiting for your approval', waiting_for_input: 'Waiting for your answer',
  decision_recorded: 'Decision recorded', unavailable: 'Status unavailable',
  // Chat tool-line outcomes (issue 04, `_outcome`): the backend's own states.
  submitted: 'Queued', cancel_requested: 'Stopping', uncertain: 'Outcome unknown', denied: 'Denied',
}

/** Why a chat tool line reads "outcome unknown", where the reason helps (issue 04, `_outcome.reason`). */
export const TOOL_OUTCOME_REASON_WORDS: Record<string, string> = {
  adapter_pending: 'this kind of job cannot be checked yet',
  no_submission: 'no job from this call was found',
  unreadable: 'the result could not be read',
  // Issue 04 S2: a write's read-back.
  revision_moved: 'the network changed while it was being checked',
  readback_failed: 'the network could not be read back',
  not_comparable: 'some values cannot be checked',
  // Issue 04 S3: undo_last's read-back against the change it undid.
  entry_moved: 'a different change was undone',
  not_restored: 'the network does not match its state before that change',
}

export const ACTIVITY_FAMILY_WORDS: Record<string, string> = {
  run: 'Study run', foreground_simulation: 'Simulation', solve_queue: 'Queued solve', gridspine: 'Grid study',
  assistant_task: 'Assistant task', confirmation: 'Confirmation', source_gap: 'Source gap',
  question: 'Question', adequacy_study: 'Adequacy study', report_job: 'Report',
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
