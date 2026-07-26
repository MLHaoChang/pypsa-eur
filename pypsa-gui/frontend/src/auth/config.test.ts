import { afterEach, describe, expect, it, vi } from 'vitest'

describe('authEnabled', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('is true by default on this branch (unset / empty)', async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', '')
    vi.resetModules()
    const { authEnabled } = await import('./config')
    expect(authEnabled).toBe(true)
  })

  it('is false only when VITE_AUTH_ENABLED is explicitly false/0', async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', 'false')
    vi.resetModules()
    let { authEnabled } = await import('./config')
    expect(authEnabled).toBe(false)

    vi.stubEnv('VITE_AUTH_ENABLED', '0')
    vi.resetModules()
    ;({ authEnabled } = await import('./config'))
    expect(authEnabled).toBe(false)
  })

  it('is true when VITE_AUTH_ENABLED === "true"', async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', 'true')
    vi.resetModules()
    const { authEnabled } = await import('./config')
    expect(authEnabled).toBe(true)
  })

  it('setAuthEnabled upgrades the live binding', async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', 'false')
    vi.resetModules()
    const mod = await import('./config')
    expect(mod.authEnabled).toBe(false)
    mod.setAuthEnabled(true)
    expect(mod.getAuthEnabled()).toBe(true)
    expect(mod.authEnabled).toBe(true)
  })
})
