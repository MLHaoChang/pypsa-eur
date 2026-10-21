import { describe, expect, it } from 'vitest'
import { nextSectionMeta } from './flowSections'

describe('nextSectionMeta', () => {
  it('follows the flow order and ends after Results', () => {
    expect(nextSectionMeta('project')?.id).toBe('carriers')
    expect(nextSectionMeta('assets')?.id).toBe('profiles')
    expect(nextSectionMeta('readiness')?.id).toBe('results')
    expect(nextSectionMeta('results')).toBeNull()
  })
})
