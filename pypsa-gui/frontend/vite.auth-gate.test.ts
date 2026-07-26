import { describe, expect, it } from 'vitest'
import {
  assertLoginHtml,
  decideGateRoute,
  isStaticAsset,
  readLoginIndexHtml,
  type BackendState,
} from './vite.auth-gate'

const DOCUMENT_ROUTES = ['/', '/index.html', '/app', '/projects', '/admin/users']

function decide(urlPath: string, backend: BackendState, authed = false) {
  return decideGateRoute({ urlPath, backend, authed })
}

describe('auth HTML gate', () => {
  it('keeps index.html as a static login page with no React entry', () => {
    const html = readLoginIndexHtml()
    expect(() => assertLoginHtml(html)).not.toThrow()
    expect(html).toContain('Auth gate')
    expect(html).toContain('Sign in')
    expect(html).not.toMatch(/src\/main\.tsx/)
  })

  it('serves the login page for anonymous document routes when auth is on', () => {
    for (const route of DOCUMENT_ROUTES) {
      expect(decide(route, 'auth-on')).toEqual({ kind: 'serve-login' })
    }
  })

  it('fails closed to the login page when the backend is unreachable', () => {
    // Regression: a stopped uvicorn used to fall through to the workbench.
    for (const route of DOCUMENT_ROUTES) {
      expect(decide(route, 'unreachable')).toEqual({ kind: 'serve-login' })
    }
    expect(decide('/spa.html', 'unreachable')).toEqual({
      kind: 'redirect',
      location: '/',
    })
  })

  it('redirects anonymous requests for the SPA entry back to the login page', () => {
    expect(decide('/spa.html', 'auth-on')).toEqual({ kind: 'redirect', location: '/' })
  })

  it('sends authenticated users from the login page to projects', () => {
    expect(decide('/', 'auth-on', true)).toEqual({ kind: 'redirect', location: '/projects' })
    expect(decide('/index.html', 'auth-on', true)).toEqual({
      kind: 'redirect',
      location: '/projects',
    })
  })

  it('serves the SPA to authenticated users on app routes', () => {
    expect(decide('/projects', 'auth-on', true)).toEqual({ kind: 'serve-spa' })
    expect(decide('/app', 'auth-on', true)).toEqual({ kind: 'serve-spa' })
  })

  it('serves the SPA when backend auth is disabled (classic single-user)', () => {
    for (const route of DOCUMENT_ROUTES) {
      expect(decide(route, 'auth-off')).toEqual({ kind: 'serve-spa' })
    }
  })

  it('always passes through static assets and the explicit login file', () => {
    for (const backend of ['auth-on', 'auth-off', 'unreachable'] as BackendState[]) {
      expect(decide('/src/main.tsx', backend)).toEqual({ kind: 'pass' })
      expect(decide('/api/health', backend)).toEqual({ kind: 'pass' })
      expect(decide('/assets/index-abc.js', backend)).toEqual({ kind: 'pass' })
      expect(decide('/login.html', backend)).toEqual({ kind: 'pass' })
    }
  })

  it('classifies asset vs document paths', () => {
    expect(isStaticAsset('/src/App.tsx')).toBe(true)
    expect(isStaticAsset('/@vite/client')).toBe(true)
    expect(isStaticAsset('/logo.svg')).toBe(true)
    expect(isStaticAsset('/projects')).toBe(false)
    expect(isStaticAsset('/')).toBe(false)
    expect(isStaticAsset('/spa.html')).toBe(false)
  })
})
