/**
 * CSRF for the call sites that bypass axios.
 *
 * The axios instance gets its token from a request interceptor
 * (`client.ts:114-122`), so every mutation through it is already covered. Seven
 * call sites use raw `fetch` instead — uploads, the chat SSE POST, and the two
 * TopologyCanvas keepalive teardowns — and those 403 on any mutation once a
 * session cookie exists.
 *
 * `cookieSource` is passed explicitly here so these stay pure functions in the
 * node environment, with no document.cookie to stub.
 */
import { describe, expect, it } from 'vitest'
import { CSRF_HEADER, rawFetchHeaders } from './csrf'

describe('rawFetchHeaders', () => {
  it('adds the header when a token cookie is present', () => {
    expect(rawFetchHeaders('POST', 'pypsa_gui_csrf=tok')).toEqual({ [CSRF_HEADER]: 'tok' })
  })
  it('adds nothing for a safe method', () => {
    expect(rawFetchHeaders('GET', 'pypsa_gui_csrf=tok')).toEqual({})
  })
  it('adds nothing when the cookie is absent', () => {
    expect(rawFetchHeaders('POST', 'other=1')).toEqual({})
  })
  it('handles DELETE, used by the keepalive teardown path', () => {
    expect(rawFetchHeaders('DELETE', 'pypsa_gui_csrf=t')).toEqual({ [CSRF_HEADER]: 't' })
  })
  it('is case-insensitive about the method, like needsCsrfHeader', () => {
    expect(rawFetchHeaders('post', 'pypsa_gui_csrf=tok')).toEqual({ [CSRF_HEADER]: 'tok' })
  })
  it('returns a spreadable empty object, never undefined', () => {
    // Every call site spreads the result into a headers literal; returning
    // undefined would be a runtime TypeError rather than a missing header.
    expect({ 'Content-Type': 'application/json', ...rawFetchHeaders('POST', '') })
      .toEqual({ 'Content-Type': 'application/json' })
  })
})
