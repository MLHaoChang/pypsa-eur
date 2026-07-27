import type { AuthUser } from '../api/auth'

/**
 * Whether a response should turn the login UI back on.
 *
 * client.ts did this unconditionally, which made one stray response a one-way
 * ratchet: local mode boots with auth off and re-arms the gate on the first
 * unrelated 401 — or on the 503 the auth-database branch emits.
 *
 * `localModeDetected` means "the backend reported auth_enabled: false", which
 * is the one case where re-arming is wrong. The ratchet's purpose is to force
 * the flag back on when the compile-time VITE_AUTH_ENABLED is stale, so it must
 * stay live everywhere else.
 */
export function shouldRearmAuth(
  status: number | undefined,
  message: string,
  localModeDetected: boolean,
): boolean {
  if (localModeDetected) return false
  if (status === 401 && message.includes('Authentication required')) return true
  if (status === 503 && message.includes('Auth database unavailable')) return true
  return false
}

/**
 * Whether a 401 should bounce to the login page.
 *
 * `shouldRedirectToLogin` is a SEPARATE path from the ratchet above and fired
 * on the message alone, regardless of the flag — so in local mode a stray 401
 * still redirected to a login page that does not exist. With auth off there is
 * nowhere to redirect to, so the flag alone is the answer.
 */
export function shouldRedirectWhenAuthDisabled(authEnabled: boolean): boolean {
  return authEnabled
}

/**
 * The synthetic user rendered when auth is off.
 *
 * AuthProvider previously used `null` here, which made hasAdminConsoleAccess(null)
 * false and bounced /admin/* to /projects. A local user owns their machine.
 */
export function localAdminUser(): AuthUser {
  // Every field AuthUser (src/api/auth.ts:3-10) declares — no `as` cast. The
  // cast would compile while leaving status/org_id/role undefined at runtime,
  // and the admin pages read org_id.
  return {
    id: 'local',
    email: 'local@pypsa-gui.localhost',
    status: 'active',
    is_super_admin: true,
    org_id: null,
    role: 'admin',
  }
}
