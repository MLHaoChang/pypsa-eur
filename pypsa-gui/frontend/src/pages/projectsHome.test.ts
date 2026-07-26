import { describe, expect, it } from 'vitest'
import { shouldShowResume } from './projectsHome'

describe('shouldShowResume', () => {
  it('shows resume when the last project is still accessible', () => {
    expect(shouldShowResume({ lastId: 'a', accessibleIds: ['a', 'b'] })).toBe(true)
  })

  it('hides resume when the last project is no longer accessible', () => {
    expect(shouldShowResume({ lastId: 'z', accessibleIds: ['a'] })).toBe(false)
  })

  it('hides resume when there is no remembered project', () => {
    expect(shouldShowResume({ lastId: null, accessibleIds: ['a'] })).toBe(false)
  })
})
