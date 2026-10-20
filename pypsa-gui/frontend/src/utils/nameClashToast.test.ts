import { describe, expect, it } from 'vitest'
import { nameClashFrom } from './nameClashToast'

describe('nameClashFrom', () => {
  it('reads the name out of the backend sentence', () => {
    expect(nameClashFrom("Project 'Hospital Energy Hub (generic site)' already exists"))
      .toBe('Hospital Energy Hub (generic site)')
  })
  it('ignores other failures', () => {
    expect(nameClashFrom('Template not found')).toBeNull()
  })
})
