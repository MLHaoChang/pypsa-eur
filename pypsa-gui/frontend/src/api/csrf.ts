/**
 * CSRF double-submit helpers (backend Step 0a).
 *
 * The backend refuses any state-changing `/api` request that carries a session
 * cookie without a matching `X-CSRF-Token` header. The token arrives in a
 * cookie that is deliberately NOT httponly — that is the entire mechanism: an
 * attacker page can make the browser *send* a `SameSite=None` session cookie
 * cross-site, but it cannot *read* the token cookie to copy into a header.
 *
 * Kept in its own module (rather than inline in `client.ts`) so it is a pure
 * function the node-environment test suite can exercise without constructing
 * an axios instance and pulling in toast/store side effects.
 */

export const CSRF_COOKIE = 'pypsa_gui_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'

/** Methods the backend exempts — gating reads would break every page load. */
export const CSRF_SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE'])

export function needsCsrfHeader(method: string | undefined): boolean {
  return !CSRF_SAFE_METHODS.has((method ?? 'get').toUpperCase())
}

/**
 * Read the CSRF token from `document.cookie`, or null when absent.
 *
 * Read fresh on every request rather than cached: `/api/auth/login` and
 * `/api/auth/me` each mint a new token, so a cached value goes stale the moment
 * a user signs out and back in without a full reload.
 */
export function readCsrfToken(cookieSource?: string): string | null {
  const raw = cookieSource
    ?? (typeof document === 'undefined' ? '' : document.cookie)
  if (!raw) return null
  for (const part of raw.split(';')) {
    const eq = part.indexOf('=')
    if (eq === -1) continue
    if (part.slice(0, eq).trim() !== CSRF_COOKIE) continue
    const value = decodeURIComponent(part.slice(eq + 1).trim())
    return value || null
  }
  return null
}
