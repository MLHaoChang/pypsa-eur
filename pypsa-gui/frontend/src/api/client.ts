import axios from 'axios'
import toast from 'react-hot-toast'
import { appLog } from '../store/simulationStore'
import { getAuthEnabled, setAuthEnabled } from '../auth/config'

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

// Mutating endpoints whose 2xx responses we DON'T want spamming the Log tab.
// Preflight is fired by the Validate button + solver-settings panels on every
// open and produces nothing the user can act on — exclude it.
const QUIET_MUTATION_URLS = ['/simulation/preflight']

const AUTH_API_PREFIX = '/auth/'
const AUTH_PAGES = new Set(['/login', '/set-password', '/reset-password'])

function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return null
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

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

  const detail = formatErrorDetail(error.response?.data?.detail, '')
  // Backend auth middleware uses this exact detail. Treat it as authoritative
  // even when the Vite env flag was stale/false — otherwise reviewers stay on
  // the workbench toasting "Authentication required" forever.
  if (detail.includes('Authentication required') || getAuthEnabled()) {
    return true
  }
  return false
}

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
    const status = err.response?.status as number | undefined
    const fallback = err.message ?? 'Unknown error'
    const msg = formatErrorDetail(err.response?.data?.detail, fallback)
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
      appLog('ERROR', `${method} ${url} — ${String(msg)}`)
      toast.error(String(msg))
    }
    return Promise.reject(err)
  },
)

export default client
