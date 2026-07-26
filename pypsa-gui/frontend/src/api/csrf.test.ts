import { describe, expect, it } from 'vitest'

import {
  CSRF_COOKIE,
  CSRF_HEADER,
  needsCsrfHeader,
  readCsrfToken,
} from './csrf'

// Frontend half of backend QA case S0.8. The backend proves it REJECTS a
// state-changing request with no token; these prove the SPA actually sends one,
// which is the other half of "the app still works after the fix".

describe('needsCsrfHeader', () => {
  it('exempts the safe methods the backend exempts', () => {
    for (const method of ['get', 'GET', 'head', 'options', 'trace']) {
      expect(needsCsrfHeader(method)).toBe(false)
    }
  })

  it('requires a token on every state-changing method', () => {
    for (const method of ['post', 'PUT', 'patch', 'DELETE']) {
      expect(needsCsrfHeader(method)).toBe(true)
    }
  })

  it('treats a missing method as GET, matching axios defaults', () => {
    // axios omits `method` for `client.get(url)`; defaulting to POST here would
    // attach a header to every read — harmless, but it would also mask a
    // genuinely missing token until the first write.
    expect(needsCsrfHeader(undefined)).toBe(false)
  })
})

describe('readCsrfToken', () => {
  it('extracts the token from a cookie string with several entries', () => {
    const cookie = `theme=dark; ${CSRF_COOKIE}=abc123; pypsa_gui_session=opaque`
    expect(readCsrfToken(cookie)).toBe('abc123')
  })

  it('reads the cookie regardless of position or surrounding spaces', () => {
    expect(readCsrfToken(`${CSRF_COOKIE}=first; other=x`)).toBe('first')
    expect(readCsrfToken(`other=x;${CSRF_COOKIE}=nospace`)).toBe('nospace')
  })

  it('URL-decodes the value', () => {
    // `secrets.token_urlsafe` is already URL-safe, but a cookie value is
    // percent-encoded in transit and a stray `%3D` must not reach the header —
    // it would fail the backend's constant-time compare against the raw cookie.
    expect(readCsrfToken(`${CSRF_COOKIE}=a%2Bb%3D`)).toBe('a+b=')
  })

  it('returns null when the cookie is absent or empty', () => {
    expect(readCsrfToken('')).toBeNull()
    expect(readCsrfToken('other=1; another=2')).toBeNull()
    expect(readCsrfToken(`${CSRF_COOKIE}=`)).toBeNull()
  })

  it('does not confuse a cookie whose name merely ends with the token name', () => {
    expect(readCsrfToken(`not_${CSRF_COOKIE}=wrong`)).toBeNull()
  })
})

describe('header name', () => {
  it('matches the backend default `csrf_header_name`', () => {
    // Backend `settings.csrf_header_name`. A rename on either side silently
    // 403s every write, so pin the literal here rather than deriving it.
    expect(CSRF_HEADER).toBe('X-CSRF-Token')
  })
})
