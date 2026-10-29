import { describe, expect, it } from 'vitest'
import { sectionWords } from './sectionWords'

describe('sectionWords', () => {
  it('names a results section in plain words', () => {
    expect(sectionWords('results_capacity')).toBe('Capacity')
    expect(sectionWords('results_unit_commitment')).toBe('Unit commitment')
  })
})
