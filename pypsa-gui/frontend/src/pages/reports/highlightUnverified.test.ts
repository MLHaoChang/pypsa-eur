/**
 * `highlightUnverified` — the pure splitter behind the viewer's `<mark>`
 * highlighting of numbers the audit could not find in the evidence
 * (`section.audit.unverified`, WP3). A number is matched as a whole token:
 * "12" must not light up inside "120" or "12.5", and the longest listed
 * token wins where two overlap.
 */
import { describe, expect, it } from 'vitest'
import { highlightUnverified, UNVERIFIED_TITLE } from './highlightUnverified'

describe('highlightUnverified', () => {
  it('returns the text untouched when nothing is unverified', () => {
    expect(highlightUnverified('LOLE is 2.4 h/yr.', [])).toEqual([
      { text: 'LOLE is 2.4 h/yr.', unverified: false },
    ])
    expect(highlightUnverified('', ['2.4'])).toEqual([])
  })

  it('splits around a matching number', () => {
    expect(highlightUnverified('LOLE is 2.4 h/yr.', ['2.4'])).toEqual([
      { text: 'LOLE is ', unverified: false },
      { text: '2.4', unverified: true },
      { text: ' h/yr.', unverified: false },
    ])
  })

  it('matches whole tokens only — not inside a longer number', () => {
    expect(highlightUnverified('120 MW and 12.5 MW and 12 MW', ['12'])).toEqual([
      { text: '120 MW and 12.5 MW and ', unverified: false },
      { text: '12', unverified: true },
      { text: ' MW', unverified: false },
    ])
  })

  it('does not match inside a word or an identifier', () => {
    expect(highlightUnverified('bus B12 carries 12 MW', ['12'])).toEqual([
      { text: 'bus B12 carries ', unverified: false },
      { text: '12', unverified: true },
      { text: ' MW', unverified: false },
    ])
  })

  it('prefers the longest listed token where two overlap', () => {
    expect(highlightUnverified('cost 1,250 EUR', ['250', '1,250'])).toEqual([
      { text: 'cost ', unverified: false },
      { text: '1,250', unverified: true },
      { text: ' EUR', unverified: false },
    ])
  })

  it('marks every occurrence and handles a token with a percent sign', () => {
    expect(highlightUnverified('8.5 % now, 8.5 % later', ['8.5 %'])).toEqual([
      { text: '8.5 %', unverified: true },
      { text: ' now, ', unverified: false },
      { text: '8.5 %', unverified: true },
      { text: ' later', unverified: false },
    ])
  })

  it('ignores blank tokens and escapes regex characters', () => {
    expect(highlightUnverified('a (b) c', ['', '  ', '(b)'])).toEqual([
      { text: 'a ', unverified: false },
      { text: '(b)', unverified: true },
      { text: ' c', unverified: false },
    ])
  })

  it('exports the tooltip text the viewer puts on every mark', () => {
    expect(UNVERIFIED_TITLE).toBe('not found in the evidence')
  })
})
