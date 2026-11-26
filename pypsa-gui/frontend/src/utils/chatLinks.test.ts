import { describe, expect, it } from 'vitest'
import { approvedChatHref } from './chatLinks'

// Assistant-assurance 06 (A5.3, GK G1): the link seam is an allowlist that reads
// each href the way the browser's URL parser does.
const BLOB = '/api/projects/project-id/uploads/0123456789abcdef/blob'
const origin = window.location.origin

describe('approvedChatHref', () => {
  it.each([
    [BLOB, BLOB],
    [`${origin}${BLOB}`, BLOB],
    ['/api/chat/answers/a-1/export?format=zip', '/api/chat/answers/a-1/export?format=zip'],
    ['/api/chat/answers/a-1', '/api/chat/answers/a-1'],
    ['/api/chat/evidence/e-1', '/api/chat/evidence/e-1'],
  ])('admits the app route %s', (href, want) => {
    expect(approvedChatHref(href)).toBe(want)
  })

  it.each([
    // mixed case, whitespace or a tab inside the scheme
    'JaVaScRiPt:alert(1)', 'java\tscript:alert(1)', 'java\nscript:alert(1)', ' javascript:alert(1)',
    // entity- and percent-encoded schemes
    'javascript&#58;alert(1)', 'java&#x73;cript:alert(1)', 'javascript%3Aalert(1)', '%6Aavascript:alert(1)',
    // backslash and protocol-relative hosts
    '/\\evil.example/x', '\\\\evil.example/x', '//evil.example/x', `/\\evil.example${BLOB}`,
    // user@host: the host is the part after the @
    `https://${window.location.host}@evil.example${BLOB}`, `${origin.replace('//', '//user:pw@')}${BLOB}`,
    // another site, mail, data
    'https://evil.example/x', `https://evil.example${BLOB}`, 'mailto:a@example.com', 'data:text/html;base64,PHNjcmlwdD4=',
    // a same-origin path that is not an app download or evidence route
    '/results/prices', '/api/projects/p/settings', `${BLOB}/../../../admin`, '/api/chat/answers/a/b/c',
    '', 42, null, undefined,
  ])('refuses %s', (href) => {
    expect(approvedChatHref(href)).toBeNull()
  })

  it('a card download field admits only an upload blob', () => {
    expect(approvedChatHref('/api/chat/answers/a-1', ['upload'])).toBeNull()
    expect(approvedChatHref(BLOB, ['upload'])).toBe(BLOB)
  })
})
