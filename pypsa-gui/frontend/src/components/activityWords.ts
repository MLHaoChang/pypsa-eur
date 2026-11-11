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
  // Issue 04 S3: a network import or export read back against its file.
  not_as_file: 'the network does not match the imported file',
  not_as_network: 'the exported file does not match the network',
  export_file_count: 'the exported file could not be identified',
  // Issue 04 S4a: a job or file read whose project is no longer the open one.
  other_context: 'another project was opened while it was being checked',
  // Issue 04 S4b: a write that raised or timed out is failed only when proven unchanged.
  changed_before_error: 'it stopped with an error after changing the network',
  not_as_requested: 'it changed the values, but not to what was asked',
  tool_timeout: 'no answer in time',
  tool_not_started: 'no answer in time',
}

/**
 * Why a chat tool line reads "denied" (issue 04 S4a, `_outcome.reason`): every
 * refusal harness/confirm.py `_deny` and harness/guards.py `denied` emit.
 * test_p3_denied_words.py fails when the backend has a kind with no words here.
 */
export const TOOL_OUTCOME_DENIED_REASON_WORDS: Record<string, string> = {
  confirmation_denied: 'you declined it',
  confirmation_expired: 'the approval expired',
  confirmation_revoked: 'the approval was withdrawn',
  aborted: 'the request was stopped before you answered',
  unknown_decision: 'your answer to the approval could not be read',
  approval_unavailable: 'the approval could not be recorded',
  user_restriction: 'a rule you set for this project blocks changes',
  round_change_cap: 'the assistant used up the changes allowed for this request',
  required_guard_failed: 'the request did not pass a required check',
  confirmation_args_changed: 'the request changed after you approved it',
  no_progress_stop: 'stopped after repeating the same failing request',
  // Issue 04 S4b: the edit lock, checked before anything ran.
  project_locked: 'another change to this project was in progress',
  project_lock_unavailable: "the project's edit lock could not be checked",
  // Issue 04 S5: a job call held while an earlier one's outcome is unknown.
  operation_pending: 'the same request may still be running',
  operation_conflict: 'an earlier request may still be running',
}

/** A refusal's Details text where the backend's own message would mislead or name internals (issue 04 S4a). */
export const TOOL_OUTCOME_DENIED_DETAIL_WORDS: Record<string, string> = {
  user_restriction: 'A rule you set for this project blocks changes.',
  approval_unavailable: 'The app could not save the approval: its data folder may be full or not writable.',
  // Issue 04 S5 (UX (12)): no tool ids; the backend message stays for the model.
  operation_pending: 'An earlier request with the same settings may still be running, so it was not started again. Ask the assistant to check it or stop it.',
  operation_conflict: 'An earlier request with other settings may still be running, so this one was not started. Ask the assistant to check it or stop it first.',
  // SC-07 S5a (UX R4): no tool or kind words; the backend message stays for the model.
  // LC-14 (UX #1328): the backend message names section ids for the model; the user reads no ids.
  invalid_navigation: 'The assistant asked to open a screen or section that does not exist, so nothing changed on screen.',
  // LC-47: the backend message tells the model not to propose the row again; the user reads why it was not made.
  change_unticked: 'You left this change unticked, so it was not made.',
  scene_apply_outcome_unknown: 'The 3D site change may not have landed. Check before trying again.',
}

/** A file tool's words where they differ from a job's (issue 04 S3: import/export). */
export const TOOL_OUTCOME_FILE_REASON_WORDS: Record<string, string> = {
  adapter_pending: 'this file format cannot be checked yet',
}

/** Why a write reads "partly done" when its count alone would mislead (issue 04 S3). */
export const TOOL_OUTCOME_PARTIAL_REASON_WORDS: Record<string, string> = {
  extra_rows: 'the network has rows the file does not have',
  // Issue 04 S4b: a row write that also changed what it did not name.
  extra_edits: 'it also changed things it was not asked to',
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
