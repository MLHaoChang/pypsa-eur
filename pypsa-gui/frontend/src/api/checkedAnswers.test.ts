// AA issue 28: the checked answers of the /2 profiles (ruled figure names) are accepted beside /1.
import { describe, expect, it } from 'vitest'
import { CHECKED_PROFILES } from './checkedAnswers'

describe('the accepted checked-answer profiles', () => {
  it.each(['investment-case/2', 'investment-lcoe/2', 'copt-screening/2'])('%s renders', id => {
    expect(CHECKED_PROFILES.has(id)).toBe(true)
  })

  it.each(['investment-case/1', 'investment-lcoe/1', 'copt-screening/1'])('%s still renders (stored answers)', id => {
    expect(CHECKED_PROFILES.has(id)).toBe(true)
  })

  it('refuses an unknown version', () => {
    for (const id of ['investment-case/3', 'copt-screening/0', 'investment-lcoe/22']) {
      expect(CHECKED_PROFILES.has(id)).toBe(false)
    }
  })
})
