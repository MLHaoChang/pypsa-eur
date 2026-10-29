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
