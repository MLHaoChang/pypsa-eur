// The hub-design state machine (guided-mode spec §5.4). `flowState` is
// derived from the study record and the review, never stored.
//
// Failed and aborted come from the STUDY record, not from the review
// (P24-BE gate N2): the review route answers 204 for a study that failed and
// 200 `ok` for an aborted one that kept a partial report.
import type { EhReview, EhStudyPayload } from '../../api/simulation'
import type { HubStep } from './hubDesignStore'

export type FlowState = 'no_project' | 'no_study' | 'running' | 'done' | 'stale'
export type RailState = 'todo' | 'current' | 'done' | 'blocked'

/** A study whose report the Results / Improve cards can read. */
export function studyHasResults(study: EhStudyPayload | null | undefined): boolean {
  if (!study) return false
  return study.status === 'done' || (study.status === 'aborted' && !!study.report)
}

export function flowState(
  project: string | null,
  study: EhStudyPayload | null | undefined,
  review: EhReview | null | undefined,
): FlowState {
  if (!project) return 'no_project'
  if (study?.status === 'running') return 'running'
  if (!studyHasResults(study)) return 'no_study'
  // The boolean, never the `source` prose (spec §4.1, gate note N8).
  if (review?.status === 'ok' && review.stale === true) return 'stale'
  return 'done'
}

/** The step a project opens on (`resetFor`, §5.4). */
export function initialStep(flow: FlowState, isTemplate: boolean): HubStep {
  if (flow === 'no_project') return 'start'
  if (flow === 'no_study') return isTemplate ? 'site' : 'start'
  if (flow === 'running') return 'goal'
  return 'results'
}

export const BLOCKED_NO_PROJECT = 'Open or create a project first'
export const BLOCKED_NO_STUDY = 'Run the study first'

/** Why a step cannot be opened, or null when it can. */
export function blockedReason(flow: FlowState, s: HubStep): string | null {
  if (flow === 'no_project') return s === 'start' ? null : BLOCKED_NO_PROJECT
  if ((flow === 'no_study' || flow === 'running') && (s === 'results' || s === 'improve')) {
    return BLOCKED_NO_STUDY
  }
  return null
}

/** The step actually shown: a step the flow blocks falls back to the one
 *  the flow would open on. */
export function effectiveStep(flow: FlowState, step: HubStep, isTemplate: boolean): HubStep {
  return blockedReason(flow, step) ? initialStep(flow, isTemplate) : step
}

export function railState(
  flow: FlowState, s: HubStep, current: HubStep, isTemplate: boolean,
): RailState {
  if (blockedReason(flow, s)) return 'blocked'
  if (s === current) return 'current'
  const studied = flow === 'running' || flow === 'done' || flow === 'stale'
  const finished = flow === 'done' || flow === 'stale'
  if (s === 'start' && (isTemplate || studied)) return 'done'
  if (s === 'site' && studied) return 'done'
  if (s === 'goal' && finished) return 'done'
  if (s === 'results' && finished && current === 'improve') return 'done'
  return 'todo'
}
