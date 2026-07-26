import { describe, expect, it, vi } from 'vitest'
import { SIGNED_OUT_PATH, redirectAfterLogout } from './logoutRedirect'

describe('redirectAfterLogout', () => {
  it('performs a full document navigation to the static sign-in page', () => {
    const assign = vi.fn()
    redirectAfterLogout(assign)
    expect(assign).toHaveBeenCalledWith(SIGNED_OUT_PATH)
  })

  it('targets the gate-served root rather than the in-app auth route', () => {
    // A client-side push to /login keeps the booted SPA mounted, which is how
    // signed-out users saw the in-app login instead of the static one.
    expect(SIGNED_OUT_PATH).toBe('/')
  })
})
