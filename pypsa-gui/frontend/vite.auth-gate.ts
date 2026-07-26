import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Connect, Plugin } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

async function hasSession(cookieHeader: string | undefined): Promise<boolean> {
  try {
    const res = await fetch('http://127.0.0.1:8000/api/auth/me', {
      headers: cookieHeader ? { cookie: cookieHeader } : {},
    })
    return res.ok
  } catch {
    return false
  }
}

async function backendAuthEnabled(): Promise<boolean> {
  try {
    const res = await fetch('http://127.0.0.1:8000/api/health')
    if (!res.ok) return false
    const body = (await res.json()) as { auth_enabled?: boolean }
    return Boolean(body.auth_enabled)
  } catch {
    return false
  }
}

function isStaticAsset(urlPath: string): boolean {
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

function createAuthGateMiddleware(): Connect.NextHandleFunction {
  return async (req, res, next) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      next()
      return
    }

    const urlPath = (req.url ?? '/').split('?')[0] || '/'
    if (isStaticAsset(urlPath)) {
      next()
      return
    }

    if (urlPath === '/login.html') {
      next()
      return
    }

    const authOn = await backendAuthEnabled()
    if (!authOn) {
      if (urlPath !== '/spa.html') {
        req.url = '/spa.html'
      }
      next()
      return
    }

    const authed = await hasSession(req.headers.cookie)
    if (authed) {
      if (urlPath === '/' || urlPath === '/index.html' || urlPath === '/login.html') {
        res.statusCode = 302
        res.setHeader('Location', '/projects')
        res.setHeader('Cache-Control', 'no-store')
        res.end()
        return
      }
      req.url = '/spa.html'
      next()
      return
    }

    if (urlPath === '/spa.html') {
      res.statusCode = 302
      res.setHeader('Location', '/')
      res.setHeader('Cache-Control', 'no-store')
      res.end()
      return
    }

    req.url = '/index.html'
    next()
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
        if (!html.includes('Auth gate')) {
          throw new Error('index.html is missing the Auth gate badge marker')
        }
        return html
      },
    },
  }
}

/** Test helper: assert login HTML invariants without a running server. */
export function assertLoginHtml(html: string): void {
  if (!html.includes('Auth gate')) {
    throw new Error('login HTML missing Auth gate badge')
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
