import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import {
  authEnabled as authEnabledInitial,
  getAuthEnabled,
  setAuthEnabled,
} from './config'

interface AuthModeContextValue {
  /** True once we have checked `/api/health` (or timed out). */
  ready: boolean
  /** Effective auth UI flag (env + runtime upgrade from backend). */
  authEnabled: boolean
  /** Force auth UI on (e.g. after a 401 from an auth-enabled API). */
  enableAuth: () => void
}

const AuthModeContext = createContext<AuthModeContextValue | null>(null)

export function AuthModeProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState<boolean>(() => getAuthEnabled())
  const [ready, setReady] = useState(false)

  const enableAuth = useCallback(() => {
    setAuthEnabled(true)
    setEnabled(true)
  }, [])

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        const res = await fetch('/api/health', { credentials: 'same-origin' })
        if (!cancelled && res.ok) {
          const body = (await res.json()) as { auth_enabled?: boolean }
          // The backend is AUTHORITATIVE about its own mode, in BOTH
          // directions. This used to be a one-way ratchet (only ever turning
          // auth ON), which dead-ended every single-user install: the env flag
          // defaults to enabled, so the SPA gated every route behind a login
          // that a backend without an auth DB cannot serve — /api/auth/me 401s
          // and /api/auth/login 500s. The env flag is only the pre-flight
          // default for the window before health answers.
          if (typeof body.auth_enabled === 'boolean') {
            setAuthEnabled(body.auth_enabled)
            setEnabled(body.auth_enabled)
          }
        }
      } catch {
        // Keep env default if health is unreachable. Failing closed here is
        // deliberate — see vite.auth-gate.ts's `unreachable` branch.
      } finally {
        if (!cancelled) setReady(true)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    function onBackendRequiresAuth() {
      enableAuth()
    }
    window.addEventListener('pypsa-auth-backend-required', onBackendRequiresAuth)
    return () => {
      window.removeEventListener('pypsa-auth-backend-required', onBackendRequiresAuth)
    }
  }, [enableAuth])

  const value = useMemo<AuthModeContextValue>(
    () => ({ ready, authEnabled: enabled, enableAuth }),
    [enableAuth, enabled, ready],
  )

  return <AuthModeContext.Provider value={value}>{children}</AuthModeContext.Provider>
}

export function useAuthMode(): AuthModeContextValue {
  const ctx = useContext(AuthModeContext)
  if (!ctx) {
    // Fallback for tests / early calls — mirror the module flag.
    return {
      ready: true,
      authEnabled: authEnabledInitial,
      enableAuth: () => setAuthEnabled(true),
    }
  }
  return ctx
}
