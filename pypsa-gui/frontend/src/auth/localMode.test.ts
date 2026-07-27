/**
 * Local mode: stop the login UI from re-arming itself (spec B5).
 *
 * Three independent mechanisms put the login gate back after the backend has
 * said auth is off, and each needs its own answer:
 *
 *   1. client.ts's ratchet — setAuthEnabled(true) on a 401 "Authentication
 *      required" OR the 503 "Auth database unavailable" branch.
 *   2. shouldRedirectToLogin — a SEPARATE path that fired on the message alone,
 *      regardless of the flag, and bounced to a /login page local mode has not
 *      got.
 *   3. AuthProvider's auth-off branch, which set `user = null` and thereby made
 *      hasAdminConsoleAccess(null) false, redirecting /admin/* away.
 *
 * The third argument of shouldRearmAuth is LOCAL-MODE-DETECTED, not
 * auth-enabled. The call site passes `!getAuthEnabled()`, and getAuthEnabled()
 * is false in local mode precisely because /api/health said so.
 */
import { describe, expect, it } from 'vitest'
import { localAdminUser, shouldRearmAuth, shouldRedirectWhenAuthDisabled } from './localMode'

describe('shouldRearmAuth', () => {
  it('re-arms on an auth 401 outside local mode', () => {
    expect(shouldRearmAuth(401, 'Authentication required', false)).toBe(true)
  })
  it('re-arms on the 503 auth-database branch outside local mode', () => {
    expect(shouldRearmAuth(503, 'Auth database unavailable', false)).toBe(true)
  })
  it('never re-arms in local mode', () => {
    expect(shouldRearmAuth(401, 'Authentication required', true)).toBe(false)
    expect(shouldRearmAuth(503, 'Auth database unavailable', true)).toBe(false)
  })
  it('ignores unrelated messages', () => {
    expect(shouldRearmAuth(401, 'Bad token', false)).toBe(false)
  })
  it('ignores an undefined status', () => {
    expect(shouldRearmAuth(undefined, 'Authentication required', false)).toBe(false)
  })
})

describe('shouldRedirectWhenAuthDisabled', () => {
  it('never redirects to login while auth is disabled', () => {
    expect(shouldRedirectWhenAuthDisabled(false)).toBe(false)
  })
  it('leaves web mode alone', () => {
    expect(shouldRedirectWhenAuthDisabled(true)).toBe(true)
  })
})

describe('localAdminUser', () => {
  it('is an admin so the console stays reachable', () => {
    expect(localAdminUser().is_super_admin).toBe(true)
    expect(localAdminUser().role).toBe('admin')
  })
  it('is active, since auth_service rejects any other status', () => {
    expect(localAdminUser().status).toBe('active')
  })
})
