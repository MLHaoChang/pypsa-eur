// Every preflight code the backend can raise has a plain title (UX
// assessment Q8). Reads the backend source, so a new code without a title
// fails here instead of shipping as a raw identifier.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ISSUE_TITLES, issueTitle } from './issueTitles'

const VALIDATION = resolve(__dirname, '../../../backend/services/validation_service.py')

function backendCodes(): string[] {
  const src = readFileSync(VALIDATION, 'utf8')
  return [...new Set([...src.matchAll(/_(?:err|warn|info)\(\s*"([a-z][a-z0-9_]+)"/g)].map(m => m[1]))]
}

describe('issue titles', () => {
  it('finds the backend codes (guards the regex itself)', () => {
    expect(backendCodes().length).toBeGreaterThan(50)
  })

  it('has a title for every code the backend raises', () => {
    const missing = backendCodes().filter(c => !(c in ISSUE_TITLES))
    expect(missing).toEqual([])
  })

  it('titles are plain words, not identifiers', () => {
    for (const [code, title] of Object.entries(ISSUE_TITLES)) {
      expect(title, code).not.toMatch(/_/)
      expect(title[0], code).toBe(title[0].toUpperCase())
    }
  })

  it('reads an unknown code as a sentence rather than failing', () => {
    expect(issueTitle('brand_new_check')).toBe('Brand new check')
  })
})
