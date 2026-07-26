/**
 * Dev-server gate: unauthenticated HTML navigations never get the workbench SPA.
 * Static /login.html always works even when a stale React bundle is stuck.
 */
export function authHtmlGatePlugin() {
  const AUTH_PAGES = new Set([
    '/login',
    '/login.html',
    '/set-password',
    '/reset-password',
  ])

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

  return {
    name: 'pypsa-auth-html-gate',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const accept = req.headers.accept ?? ''
        const urlPath = (req.url ?? '/').split('?')[0] || '/'
        const isDocument =
          req.method === 'GET'
          && (accept.includes('text/html') || urlPath === '/' || urlPath.endsWith('.html'))
        const isAsset =
          urlPath.startsWith('/src/')
          || urlPath.startsWith('/@')
          || urlPath.startsWith('/node_modules/')
          || urlPath.startsWith('/assets/')
          || urlPath.startsWith('/api/')
          || urlPath.includes('.')

        if (!isDocument || isAsset) {
          next()
          return
        }

        if (AUTH_PAGES.has(urlPath) || urlPath === '/login.html') {
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

        res.statusCode = 302
        res.setHeader('Location', '/login.html')
        res.setHeader('Cache-Control', 'no-store')
        res.end()
      })
    },
  }
}
