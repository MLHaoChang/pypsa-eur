import { describe, expect, it } from 'vitest'
import { activityFamilyWord, activityStatusWord } from './activityWords'

describe('activityWords', () => {
  it('uses plain words and falls back to spaced text', () => {
    expect(activityStatusWord('waiting_for_approval')).toBe('Waiting for your approval')
    expect(activityFamilyWord('foreground_simulation')).toBe('Simulation')
    expect(activityStatusWord('odd_new_state')).toBe('odd new state')
  })
})
