import axios from 'axios'
import toast from 'react-hot-toast'
import { appLog } from '../store/simulationStore'
import { getAuthEnabled, setAuthEnabled } from '../auth/config'
import { CSRF_HEADER, needsCsrfHeader, readCsrfToken } from './csrf'

declare module 'axios' {
  interface AxiosRequestConfig {
    skipAuthRedirect?: boolean
    skipErrorToast?: boolean
  }
}

export const client = axios.create({
  baseURL: '/api',
  timeout: 30000,
  withCredentials: true,
})

// URLs that the UI polls in the background — failures during a backend reload
// window must NOT pop a toast or tear down the React Query cache.
const QUIET_POLL_URLS = ['/network/undo/info', '/changelog']

function isQuietPoll(url: string | undefined, method: string): boolean {
  if (method !== 'GET') return false
  if (!url) return false
  return QUIET_POLL_URLS.some(p => url.startsWith(p))
}

/** Format FastAPI `detail` (string | validation array | object) for toasts/logs. */
export function formatApiDetail(detail: unknown, fallback = 'Unknown error'): string {
  if (detail == null) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const loc = Array.isArray((item as { loc?: unknown }).loc)
          ? (item as { loc: unknown[] }).loc.filter((x) => x !== 'body').join('.')
          : ''
        const msg = (item as { msg?: unknown }).msg
        if (typeof msg === 'string') return loc ? `${loc}: ${msg}` : msg
      }
      try {
        return JSON.stringify(item)
      } catch {
        return String(item)
      }
    })
    return parts.filter(Boolean).join('; ') || fallback
  }
  if (typeof detail === 'object') {
    const d = detail as { message?: unknown; msg?: unknown }
    if (typeof d.message === 'string') return d.message
    if (typeof d.msg === 'string') return d.msg
    try {
      return JSON.stringify(detail)
    } catch {
      return fallback
    }
  }
  return String(detail)
}

// Mutating endpoints whose 2xx responses we DON'T want spamming the Log tab.
// Preflight is fired by the Validate button + solver-settings panels on every
// open and produces nothing the user can act on — exclude it.
const QUIET_MUTATION_URLS = ['/simulation/preflight']

// Expected conflict codes — still log, but avoid toast spam during a solve.
const QUIET_TOAST_CODES = new Set(['solver_in_flight'])

const AUTH_API_PREFIX = '/auth/'
const AUTH_PAGES = new Set(['/login', '/set-password', '/reset-password'])

function notifyAuthBackendRequired(status: number): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(
    new CustomEvent('pypsa-auth-backend-required', { detail: { status } }),
  )
}

function forceLoginRedirect(): void {
  if (typeof window === 'undefined') return
  if (AUTH_PAGES.has(window.location.pathname)) return
  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
  const nextQuery = currentPath && currentPath !== '/'
    ? `?next=${encodeURIComponent(currentPath)}`
    : ''
  // Full navigation remounts the SPA so a stale authEnabled=false HMR/session
  // cannot keep rendering the classic workbench.
  window.location.replace(`/login${nextQuery}`)
}

function shouldRedirectToLogin(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false
  if (error.response?.status !== 401) return false
  if (error.config?.skipAuthRedirect) return false
  const url = error.config?.url ?? ''
  if (url.startsWith(AUTH_API_PREFIX)) return false
  if (typeof window === 'undefined') return false
  if (AUTH_PAGES.has(window.location.pathname)) return false

  const detail = formatApiDetail(error.response?.data?.detail, '')
  // Backend auth middleware uses this exact detail. Treat it as authoritative
  // even when the Vite env flag was stale/false — otherwise reviewers stay on
  // the workbench toasting "Authentication required" forever.
  if (detail.includes('Authentication required') || getAuthEnabled()) {
    return true
  }
  return false
}

// ── CSRF double-submit (backend Step 0a) ────────────────────────────────────
// Every state-changing /api request from an authenticated session must echo the
// CSRF cookie back in a header, or the backend refuses it with 403. See
// `./csrf` for why the cookie is readable by script — that is the mechanism,
// not an oversight.
client.interceptors.request.use((config) => {
  if (!needsCsrfHeader(config.method)) return config
  const token = readCsrfToken()
  if (token) config.headers.set(CSRF_HEADER, token)
  return config
})

client.interceptors.response.use(
  (res) => {
    const method = (res.config.method ?? '').toUpperCase()
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
      const url = res.config.url ?? ''
      const quiet = QUIET_MUTATION_URLS.some(p => url.startsWith(p))
      if (!quiet && (!url.includes('/timeseries') || url.includes('/upload'))) {
        appLog('INFO', `${method} ${url} → ${res.status}`)
      }
    }
    return res
  },
  (err) => {
    const data = err.response?.data
    const status = err.response?.status as number | undefined
    const code = typeof data?.code === 'string' ? data.code : undefined
    const msg = formatApiDetail(data?.detail ?? err.message)
    const method = (err.config?.method ?? '').toUpperCase()
    const url = err.config?.url ?? ''

    if (
      (status === 401 && String(msg).includes('Authentication required'))
      || (status === 503 && String(msg).includes('Auth database unavailable'))
    ) {
      setAuthEnabled(true)
      notifyAuthBackendRequired(status ?? 401)
    }

    if (shouldRedirectToLogin(err)) {
      forceLoginRedirect()
      return Promise.reject(err)
    }

    // A missing/stale CSRF token is recoverable without signing out: `GET
    // /auth/me` re-mints the cookie whenever it is absent. Fire it once and
    // tell the user to retry, rather than surfacing a bare 403 that reads like
    // a permissions problem — which is what a session predating the CSRF
    // change, or one whose token cookie was cleared, would otherwise look like.
    if (status === 403 && code === 'csrf_token_invalid') {
      void client.get('/auth/me', { skipErrorToast: true, skipAuthRedirect: true })
        .catch(() => undefined)
      appLog('ERROR', `${method} ${url} — CSRF token missing; refreshed, retry the action`)
      toast.error('Your session token expired. Please retry that action.')
      return Promise.reject(err)
    }

    if (
      status === 401
      && String(msg).includes('Authentication required')
    ) {
      // Already on an auth page, or redirect skipped — don't toast-spam.
      return Promise.reject(err)
    }

    // Quiet poll endpoints (undo/info, changelog) silently fail during the
    // brief uvicorn --reload window. They'll succeed on the next interval; no
    // need to log an error or pop a toast.
    if (!isQuietPoll(url, method) && !err.config?.skipErrorToast) {
      appLog('ERROR', `${method} ${url} — ${msg}${code ? ` [${code}]` : ''}`)
      if (!code || !QUIET_TOAST_CODES.has(code)) {
        toast.error(msg)
      }
    }
    return Promise.reject(err)
  },
)

export default client
