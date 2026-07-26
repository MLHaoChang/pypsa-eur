import { describe, expect, it } from 'vitest'
import { assertLoginHtml, readLoginIndexHtml } from './vite.auth-gate'

describe('auth HTML gate', () => {
  it('keeps index.html as a static login page with no React entry', () => {
    const html = readLoginIndexHtml()
    expect(() => assertLoginHtml(html)).not.toThrow()
    expect(html).toContain('Auth gate')
    expect(html).toContain('Sign in')
    expect(html).not.toMatch(/src\/main\.tsx/)
  })
})
