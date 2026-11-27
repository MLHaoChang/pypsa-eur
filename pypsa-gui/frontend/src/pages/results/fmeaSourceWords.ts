// template-e2e 24: the FMEA worksheet's words for rows that come from the last
// Energy Hub study (`/results/fmea_modes` with `source: "eh_study"`), in one
// module so the tab and its tests read the same sentences (UX 32, items 1, 2, 4).
import type { ModesPayload } from './fmea'
import { runStamp } from '../../components/SourceRunLine'

export const GUIDED_STUDY_SOURCE =
  'These rows come from the last Energy Hub study: the same risks its results and the report list.'
export const EXPERT_STUDY_SOURCE =
  "Computed rows are the last Energy Hub study's ranking, as in the reference design and the report. "
  + 'They are not a fresh screening. A contingency sweep run after the study replaces them.'
/** With study rows a new VoLL prices nothing until the study runs again (GK C4). */
export const STUDY_REPRICE =
  "These are the study's rows: after setting a price, run the study again to price them."

/** "The study ran on 2026-11-27 16:00." — GK C1: the rows say when they were
 *  made, in the app's ISO date form (runStamp). */
export function studyRanOn(at: unknown): string | null {
  const when = typeof at === 'number' && Number.isFinite(at) ? runStamp(at) : null
  return when ? `The study ran on ${when}.` : null
}

/** True when the worksheet's computed rows are the last study's. */
export function isStudySourced(modes: ModesPayload | null | undefined): boolean {
  return modes?.source === 'eh_study'
}

/** The line naming where the computed rows come from, or null for live rows. */
export function modesSourceText(
  modes: ModesPayload | null | undefined, guided: boolean,
): string | null {
  if (!isStudySourced(modes)) return null
  const ran = studyRanOn(modes?.study_finished_at)
  const base = guided ? GUIDED_STUDY_SOURCE : EXPERT_STUDY_SOURCE
  return ran ? `${base} ${ran}` : base
}

/** The re-run note for unpriced study rows, or null for live rows. */
export function studyRepriceText(modes: ModesPayload | null | undefined): string | null {
  return isStudySourced(modes) ? STUDY_REPRICE : null
}

/** True only when the backend knows the network was edited after the study
 *  (`edited_since_study`); null (cannot tell) and false are not stale. */
export function studyRowsOutOfDate(modes: ModesPayload | null | undefined): boolean {
  return isStudySourced(modes) && modes?.edited_since_study === true
}
