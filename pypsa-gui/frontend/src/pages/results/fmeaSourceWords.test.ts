// template-e2e 24 (UX 32 item 4): each study-source sentence shows only when
// the worksheet's rows are the last Energy Hub study's.
import { describe, expect, it } from 'vitest'
import {
  EXPERT_STUDY_SOURCE, GUIDED_STUDY_SOURCE, STUDY_REPRICE,
  modesSourceText, studyRepriceText, studyRowsOutOfDate,
} from './fmeaSourceWords'

const study = { per_mode: [], source: 'eh_study' as const, study_finished_at: 1_790_000_000 }
const live = { per_mode: [], source: 'live' as const }

describe('the study-source line', () => {
  it('shows the Guided and Expert sentences, then the date, only for study rows', () => {
    expect(modesSourceText(study, true)).toMatch(new RegExp(`^${GUIDED_STUDY_SOURCE} The study ran on .+\\.$`))
    expect(modesSourceText(study, false)!.startsWith(`${EXPERT_STUDY_SOURCE} The study ran on `)).toBe(true)
    expect(modesSourceText(live, true)).toBeNull()
    expect(modesSourceText(live, false)).toBeNull()
    expect(modesSourceText(null, false)).toBeNull()
  })
  it('leaves the date out when the study has none', () => {
    expect(modesSourceText({ per_mode: [], source: 'eh_study' }, true)).toBe(GUIDED_STUDY_SOURCE)
  })
  // UX rule 53: the line must stay true; /fmea_modes hands over to the live
  // rows when a sweep finishes after the study (backend test_fmea_modes_single_source).
  it('says a sweep run after the study replaces the rows', () => {
    expect(EXPERT_STUDY_SOURCE).toMatch(/A contingency sweep run after the study replaces them\./)
    expect(GUIDED_STUDY_SOURCE).toMatch(/last Energy Hub study/)
  })
})

describe('the re-run note and the out-of-date state', () => {
  it('shows the re-run note only for study rows', () => {
    expect(studyRepriceText(study)).toBe(STUDY_REPRICE)
    expect(studyRepriceText(live)).toBeNull()
  })
  it('is out of date only when the network is known to be edited since the study', () => {
    expect(studyRowsOutOfDate({ ...study, edited_since_study: true })).toBe(true)
    expect(studyRowsOutOfDate({ ...study, edited_since_study: false })).toBe(false)
    expect(studyRowsOutOfDate({ ...study, edited_since_study: null })).toBe(false)
    expect(studyRowsOutOfDate({ ...live, edited_since_study: true })).toBe(false)
  })
})
