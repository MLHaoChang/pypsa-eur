import axios from 'axios'
import toast from 'react-hot-toast'
import { appLog } from '../store/simulationStore'
import { authEnabled } from '../auth/config'

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

function shouldRedirectToLogin(error: unknown): boolean {
  if (!authEnabled) return false
  if (!axios.isAxiosError(error)) return false
  if (error.response?.status !== 401) return false
  if (error.config?.skipAuthRedirect) return false
  const url = error.config?.url ?? ''
  if (url.startsWith(AUTH_API_PREFIX)) return false
  if (typeof window === 'undefined') return false
  if (AUTH_PAGES.has(window.location.pathname)) return false
  return true
}

function redirectToLogin(): void {
  if (typeof window === 'undefined') return
  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
  const nextQuery = currentPath && !AUTH_PAGES.has(window.location.pathname)
    ? `?next=${encodeURIComponent(currentPath)}`
    : ''
  window.location.assign(`/login${nextQuery}`)
}

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

let authBackendRequiredNotified = false

function notifyAuthBackendRequired(status: number): void {
  if (authEnabled || typeof window === 'undefined') return
  if (authBackendRequiredNotified) return
  authBackendRequiredNotified = true
  window.dispatchEvent(
    new CustomEvent('pypsa-auth-backend-required', { detail: { status } }),
  )
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
    if (shouldRedirectToLogin(err)) {
      redirectToLogin()
      return Promise.reject(err)
    }

    const status = err.response?.status
    const fallback = err.message ?? 'Unknown error'
    const msg = formatErrorDetail(err.response?.data?.detail, fallback)
    const method = (err.config?.method ?? '').toUpperCase()
    const url = err.config?.url ?? ''

    // Classic workbench + auth-enabled API: surface a setup gate once instead
    // of toasting every background poll as "Request failed with status code N".
    if (
      !authEnabled
      && (status === 401 || status === 503)
      && typeof msg === 'string'
      && (msg.includes('Authentication required') || msg.includes('Auth database unavailable'))
    ) {
      notifyAuthBackendRequired(status)
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
