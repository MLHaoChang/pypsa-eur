// Guided-mode spec §5.4: the derived flow state, the step a project opens on,
// and the rail state of each step. Stale comes from the review's boolean
// (§4.1, gate N8); failed / aborted from the study record (gate N2).
import { describe, expect, it } from 'vitest'
import type { EhStudyPayload } from '../../api/simulation'
import {
  BLOCKED_NO_PROJECT, BLOCKED_NO_STUDY, blockedReason, effectiveStep, flowState,
  initialStep, railState,
} from './flow'
import { HUB_STEPS } from './hubDesignStore'
import { REPORT, review } from './testFixtures'

const S = (status: string, report: EhStudyPayload['report'] = null): EhStudyPayload =>
  ({ status, report })

describe('flowState', () => {
  it('no project first', () => {
    expect(flowState(null, S('running'), review())).toBe('no_project')
  })
  it('no study record, failed or aborted without a report → no_study', () => {
    expect(flowState('p', null, null)).toBe('no_study')
    expect(flowState('p', S('failed'), null)).toBe('no_study')
    expect(flowState('p', S('aborted'), null)).toBe('no_study')
    // the review is not consulted for failed / aborted (N2)
    expect(flowState('p', S('failed'), review())).toBe('no_study')
  })
  it('running from the study record', () => {
    expect(flowState('p', S('running'), review())).toBe('running')
  })
  it('done, and aborted with a partial report', () => {
    expect(flowState('p', S('done', REPORT), review())).toBe('done')
    expect(flowState('p', S('aborted', REPORT), review())).toBe('done')
    expect(flowState('p', S('done', REPORT), null)).toBe('done')
  })
  it('stale only from review.stale === true, never from the source text', () => {
    expect(flowState('p', S('done', REPORT), review({ stale: true }))).toBe('stale')
    expect(flowState('p', S('done', REPORT), review({ stale: false,
      source: 'study record (the stored report was cleared by a later solve)' }))).toBe('done')
  })
})

describe('initialStep', () => {
  it('per §5.4', () => {
    expect(initialStep('no_project', true)).toBe('start')
    expect(initialStep('no_study', true)).toBe('site')
    expect(initialStep('no_study', false)).toBe('start')
    expect(initialStep('running', false)).toBe('goal')
    expect(initialStep('done', false)).toBe('results')
    expect(initialStep('stale', true)).toBe('results')
  })
})

describe('rail', () => {
  it('no project: every step but Start is blocked', () => {
    expect(HUB_STEPS.map(s => blockedReason('no_project', s))).toEqual(
      [null, BLOCKED_NO_PROJECT, BLOCKED_NO_PROJECT, BLOCKED_NO_PROJECT, BLOCKED_NO_PROJECT])
  })
  it('no study / running: Results and Improve are blocked', () => {
    for (const f of ['no_study', 'running'] as const) {
      expect(HUB_STEPS.map(s => blockedReason(f, s))).toEqual(
        [null, null, null, BLOCKED_NO_STUDY, BLOCKED_NO_STUDY])
    }
    expect(HUB_STEPS.map(s => blockedReason('done', s))).toEqual([null, null, null, null, null])
  })
  it('states on a finished template study, Improve showing', () => {
    expect(HUB_STEPS.map(s => railState('done', s, 'improve', true)))
      .toEqual(['done', 'done', 'done', 'done', 'current'])
  })
  it('own network before a study: Start is not ticked', () => {
    expect(HUB_STEPS.map(s => railState('no_study', s, 'site', false)))
      .toEqual(['todo', 'current', 'todo', 'blocked', 'blocked'])
  })
  it('a blocked step is never shown: it falls back to the flow\'s own step', () => {
    expect(effectiveStep('no_study', 'results', true)).toBe('site')
    expect(effectiveStep('no_project', 'goal', false)).toBe('start')
    expect(effectiveStep('done', 'improve', false)).toBe('improve')
  })
})
