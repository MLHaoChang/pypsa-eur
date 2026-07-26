import { afterEach, describe, expect, it, vi } from 'vitest'

describe('authEnabled', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('defaults to false when VITE_AUTH_ENABLED is unset', async () => {
    vi.resetModules()
    const { authEnabled } = await import('./config')
    expect(authEnabled).toBe(false)
  })

  it('is false unless VITE_AUTH_ENABLED is exactly "true"', async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', 'false')
    vi.resetModules()
    let { authEnabled } = await import('./config')
    expect(authEnabled).toBe(false)

    vi.stubEnv('VITE_AUTH_ENABLED', '1')
    vi.resetModules()
    ;({ authEnabled } = await import('./config'))
    expect(authEnabled).toBe(false)
  })

  it('is true only when VITE_AUTH_ENABLED === "true"', async () => {
    vi.stubEnv('VITE_AUTH_ENABLED', 'true')
    vi.resetModules()
    const { authEnabled } = await import('./config')
    expect(authEnabled).toBe(true)
  })
})
