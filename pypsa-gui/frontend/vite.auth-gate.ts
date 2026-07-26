import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Dev-server gate: unauthenticated navigations never receive the React SPA.
 *
 * Important: do NOT key off Accept text/html. Cursor's in-app preview often
 * requests document routes with Accept star/star, which previously skipped the
 * gate and served the workbench bundle for /app and /projects.
 */
export function authHtmlGatePlugin(): Plugin {
  const loginHtmlPath = path.resolve(__dirname, 'public/login.html')

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
    // Real assets have non-html extensions (.js, .css, .map, images, ...)
    if (leaf.includes('.') && !leaf.endsWith('.html')) return true
    return false
  }

  return {
    name: 'pypsa-auth-html-gate',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (req.method !== 'GET' && req.method !== 'HEAD') {
          next()
          return
        }

        const urlPath = (req.url ?? '/').split('?')[0] || '/'
        if (isStaticAsset(urlPath)) {
          next()
          return
        }

        if (!(await backendAuthEnabled())) {
          next()
          return
        }

        if (await hasSession(req.headers.cookie)) {
          next()
          return
        }

        // Rewrite (200) — do not 302. Some Cursor preview sessions ignore
        // redirects and keep the old SPA document alive.
        try {
          const html = fs.readFileSync(loginHtmlPath, 'utf8')
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/html; charset=utf-8')
          res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate')
          res.setHeader('Pragma', 'no-cache')
          res.end(html)
        } catch (err) {
          console.error('[pypsa-auth-html-gate] failed to read login.html', err)
          res.statusCode = 503
          res.end('Login page unavailable. Restart Vite.')
        }
      })
    },
  }
}
