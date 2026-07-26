import { describe, expect, it } from 'vitest'
import { getPostLoginPath } from './resume'

describe('getPostLoginPath', () => {
  it('goes to projects home by default', () => {
    expect(getPostLoginPath(null)).toBe('/projects')
  })

  it('resumes last project when provided', () => {
    expect(getPostLoginPath('uuid-1')).toBe('/app?project=uuid-1')
  })
})
