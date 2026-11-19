import { describe, expect, it } from 'vitest'
import { nameClashFrom, numberedCopyName } from './nameClashToast'

describe('nameClashFrom', () => {
  it('reads the name out of the backend sentence', () => {
    expect(nameClashFrom("Project 'Hospital Energy Hub (generic site)' already exists"))
      .toBe('Hospital Energy Hub (generic site)')
  })
  it('ignores other failures', () => {
    expect(nameClashFrom('Template not found')).toBeNull()
  })
})

describe('numberedCopyName', () => {
  it('numbers a first copy and counts up from a numbered one', () => {
    expect(numberedCopyName('Industrial Hydrogen Hub')).toBe('Industrial Hydrogen Hub 2')
    expect(numberedCopyName('Industrial Hydrogen Hub 2')).toBe('Industrial Hydrogen Hub 3')
  })
})
