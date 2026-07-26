import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Connect, Plugin } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const API_ORIGIN = process.env.PYPSA_GUI_API_ORIGIN ?? 'http://127.0.0.1:8000'

/** Marker that identifies the static login document (no React entry). */
export const LOGIN_PAGE_MARKER = 'data-pypsa-page="login"'

export type BackendState = 'auth-on' | 'auth-off' | 'unreachable'

export type GateDecision =
  | { kind: 'pass' }
  | { kind: 'serve-spa' }
  | { kind: 'serve-login' }
  | { kind: 'redirect'; location: string }

export function isStaticAsset(urlPath: string): boolean {
  if (urlPath.startsWith('/src/')) return true
  if (urlPath.startsWith('/@')) return true
  if (urlPath.startsWith('/node_modules/')) return true
  if (urlPath.startsWith('/api/')) return true
  if (urlPath.startsWith('/assets/')) return true
  if (urlPath.startsWith('/favicon')) return true
  const leaf = urlPath.split('/').pop() ?? ''
  if (leaf.includes('.') && !leaf.endsWith('.html')) return true
  return false
}

/**
 * Pure routing decision so the behaviour is unit-testable.
 *
 * Fail closed: when the backend is unreachable we cannot know whether auth is
 * required, so we serve the static login page rather than the workbench. This
 * is what previously dumped reviewers into the workbench whenever uvicorn was
 * not running.
 */
export function decideGateRoute(input: {
  urlPath: string
  backend: BackendState
  authed: boolean
}): GateDecision {
  const { urlPath, backend, authed } = input

  if (isStaticAsset(urlPath)) return { kind: 'pass' }
  if (urlPath === '/login.html') return { kind: 'pass' }

  if (backend === 'auth-off') return { kind: 'serve-spa' }

  if (backend === 'unreachable') {
    if (urlPath === '/spa.html') return { kind: 'redirect', location: '/' }
    return { kind: 'serve-login' }
  }

  if (authed) {
    if (urlPath === '/' || urlPath === '/index.html') {
      return { kind: 'redirect', location: '/projects' }
    }
    return { kind: 'serve-spa' }
  }

  if (urlPath === '/spa.html') return { kind: 'redirect', location: '/' }
  return { kind: 'serve-login' }
}

async function readBackendState(): Promise<BackendState> {
  try {
    const res = await fetch(`${API_ORIGIN}/api/health`)
    if (!res.ok) return 'unreachable'
    const body = (await res.json()) as { auth_enabled?: boolean }
    return body.auth_enabled ? 'auth-on' : 'auth-off'
  } catch {
    return 'unreachable'
  }
}

async function hasSession(cookieHeader: string | undefined): Promise<boolean> {
  try {
    const res = await fetch(`${API_ORIGIN}/api/auth/me`, {
      headers: cookieHeader ? { cookie: cookieHeader } : {},
    })
    return res.ok
  } catch {
    return false
  }
}

function createAuthGateMiddleware(): Connect.NextHandleFunction {
  return async (req, res, next) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      next()
      return
    }

    const urlPath = (req.url ?? '/').split('?')[0] || '/'
    if (isStaticAsset(urlPath) || urlPath === '/login.html') {
      next()
      return
    }

    const backend = await readBackendState()
    const authed = backend === 'auth-on' ? await hasSession(req.headers.cookie) : false
    const decision = decideGateRoute({ urlPath, backend, authed })

    switch (decision.kind) {
      case 'pass':
        next()
        return
      case 'redirect':
        res.statusCode = 302
        res.setHeader('Location', decision.location)
        res.setHeader('Cache-Control', 'no-store')
        res.end()
        return
      case 'serve-spa':
        req.url = '/spa.html'
        next()
        return
      case 'serve-login':
        req.url = '/index.html'
        next()
        return
    }
  }
}

/**
 * Route HTML documents to either the static login page (index.html) or the
 * React SPA (spa.html). index.html itself contains NO React entry, so even a
 * broken/missing plugin cannot boot the workbench for anonymous users.
 */
export function authHtmlGatePlugin(): Plugin {
  return {
    name: 'pypsa-auth-html-gate',
    configureServer(server) {
      server.middlewares.use(createAuthGateMiddleware())
    },
    configurePreviewServer(server) {
      server.middlewares.use(createAuthGateMiddleware())
    },
    transformIndexHtml: {
      order: 'pre',
      handler(html, ctx) {
        const file = ctx.filename ?? ''
        const isLoginIndex =
          file.endsWith(`${path.sep}index.html`)
          || ctx.path === '/index.html'
          || ctx.path === '/'
        if (!isLoginIndex) return html
        if (html.includes('/src/main.tsx') || html.includes('src/main.tsx')) {
          throw new Error(
            'index.html must remain the static login page and must not load /src/main.tsx',
          )
        }
        if (!html.includes(LOGIN_PAGE_MARKER)) {
          throw new Error(`index.html is missing the ${LOGIN_PAGE_MARKER} marker`)
        }
        return html
      },
    },
  }
}

/** Test helper: assert login HTML invariants without a running server. */
export function assertLoginHtml(html: string): void {
  if (!html.includes(LOGIN_PAGE_MARKER)) {
    throw new Error(`login HTML missing ${LOGIN_PAGE_MARKER} marker`)
  }
  if (!html.includes('Sign in')) {
    throw new Error('login HTML missing Sign in')
  }
  if (html.includes('/src/main.tsx') || html.includes('src/main.tsx')) {
    throw new Error('login HTML must not load the React entry')
  }
}

export function readLoginIndexHtml(): string {
  return fs.readFileSync(path.resolve(__dirname, 'index.html'), 'utf8')
}
