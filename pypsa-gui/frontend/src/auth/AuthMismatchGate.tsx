import { useEffect, useState, type ReactNode } from 'react'
import { authEnabled } from './config'

type GateState = 'checking' | 'ok' | 'mismatch' | 'db_unavailable'

/**
 * When the SPA is still in classic single-user mode (`authEnabled=false`) but
 * the backend has multi-user auth on, the workbench polls `/api/*` and gets
 * 401/503 toasts ("Request failed with status code 500" before the 503 fix).
 * Block the workbench and tell the reviewer how to enable the login UI.
 */
export default function AuthMismatchGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>(authEnabled ? 'ok' : 'checking')

  useEffect(() => {
    if (authEnabled) {
      setState('ok')
      return
    }

    let cancelled = false
    void (async () => {
      try {
        const res = await fetch('/api/health', { credentials: 'same-origin' })
        if (cancelled) return
        if (!res.ok) {
          setState('ok')
          return
        }
        const body = (await res.json()) as { auth_enabled?: boolean }
        setState(body.auth_enabled ? 'mismatch' : 'ok')
      } catch {
        if (!cancelled) setState('ok')
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (authEnabled || state !== 'ok') return

    function onAuthRequiredToast(event: Event) {
      const detail = (event as CustomEvent<{ status?: number }>).detail
      if (detail?.status === 503) {
        setState('db_unavailable')
        return
      }
      if (detail?.status === 401) {
        setState('mismatch')
      }
    }

    window.addEventListener('pypsa-auth-backend-required', onAuthRequiredToast)
    return () => window.removeEventListener('pypsa-auth-backend-required', onAuthRequiredToast)
  }, [state])

  if (authEnabled || state === 'ok' || state === 'checking') {
    return <>{children}</>
  }

  const title = state === 'db_unavailable'
    ? 'Auth database unavailable'
    : 'Login UI is not enabled in this frontend'

  const body = state === 'db_unavailable'
    ? (
        <>
          The backend has multi-user auth on, but it cannot reach the auth database.
          Set <code className="font-mono text-xs">DATABASE_URL</code> to Postgres or
          {' '}<code className="font-mono text-xs">sqlite+pysqlite:///./auth_dev.db</code>,
          run migrations / bootstrap the super-admin, then restart the backend.
        </>
      )
    : (
        <>
          This browser session is running the classic workbench
          (<code className="font-mono text-xs">VITE_AUTH_ENABLED</code> is not
          {' '}<code className="font-mono text-xs">true</code>), while the API requires
          a session. Fully restart the Vite dev server so it loads
          {' '}<code className="font-mono text-xs">frontend/.env.development</code>
          (a hard refresh alone is not enough), then open <code className="font-mono text-xs">/login</code>.
        </>
      )

  return (
    <div className="flex h-screen items-center justify-center bg-bg px-6 text-text">
      <div className="w-full max-w-lg space-y-4 rounded-2xl border border-border bg-panel p-8 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="text-sm leading-6 text-muted">{body}</p>
        <ol className="list-decimal space-y-2 pl-5 text-sm leading-6 text-text">
          <li>Stop <code className="font-mono text-xs">npm run dev</code> and start it again.</li>
          <li>Confirm the URL shows <code className="font-mono text-xs">/login</code>.</li>
          <li>
            Sign in with the bootstrapped super-admin
            (default review account: <code className="font-mono text-xs">admin@example.com</code>).
          </li>
        </ol>
        <button
          className="rounded-lg border border-border px-3 py-2 text-sm hover:border-accent hover:text-accent"
          onClick={() => window.location.assign('/login')}
          type="button"
        >
          Try /login anyway
        </button>
      </div>
    </div>
  )
}
