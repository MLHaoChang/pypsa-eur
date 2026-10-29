import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  ACTIVITY_STATUS_WORDS, NATIVE_OUTCOME_WORDS, activityFamilyWord, activityModeWord, activityStatusWord,
  nativeOutcomeWord,
} from './activityWords'

const backend = (rel: string) => readFileSync(resolve(__dirname, '../../../backend', rel), 'utf8')
const quoted = (block: string) => [...block.matchAll(/'([a-z_]+)'/g)].map(m => m[1])

describe('activityWords', () => {
  it('uses plain words and falls back to spaced text', () => {
    expect(activityStatusWord('waiting_for_approval')).toBe('Waiting for your approval')
    expect(activityFamilyWord('foreground_simulation')).toBe('Simulation')
    expect(activityStatusWord('odd_new_state')).toBe('odd new state')
    expect(activityModeWord('foreground')).toBe('Foreground')
    expect(activityModeWord('odd_mode')).toBe('Odd mode')
  })
  it('names the source outcome only when it adds to the plain status', () => {
    expect(nativeOutcomeWord('met', 'completed')).toBe('Target met')
    expect(nativeOutcomeWord('revoked', 'unavailable')).toBe('Withdrawn')
    expect(nativeOutcomeWord('completed', 'completed')).toBeNull()
    expect(nativeOutcomeWord('failed', 'failed')).toBeNull()
    expect(nativeOutcomeWord('done', 'completed')).toBeNull()
    expect(nativeOutcomeWord('unknown', 'needs_review')).toBeNull()
    expect(nativeOutcomeWord('odd_new_state', 'completed')).toBe('Odd new state')
  })
  it('covers every backend end state that the plain status does not already say', () => {
    const history = backend('harness/activity_history.py')
    const attention = backend('harness/attention.py')
    const terminal = history.match(/_TERMINAL = \{([\s\S]*?)\n\}/)![1]
    const states = [
      ...quoted(terminal),
      ...quoted(attention.match(/_CONFIRM_STATES = \{([\s\S]*?)\}/)![1]),
      ...quoted(attention.match(/_QUESTION_STATES = \{([\s\S]*?)\}/)![1]),
    ].filter(v => !['foreground_simulation', 'adequacy_study', 'report_job'].includes(v))
    expect(states.length).toBeGreaterThan(10)
    // 'done' and 'approved' read the same as their plain status ("Done", "Approved").
    const selfSaid = new Set(['done', 'approved'])
    const missing = states.filter(v => !(v in NATIVE_OUTCOME_WORDS) && !(v in ACTIVITY_STATUS_WORDS) && !selfSaid.has(v))
    expect(missing).toEqual([])
  })
})
