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

    const msg = err.response?.data?.detail ?? err.message ?? 'Unknown error'
    const method = (err.config?.method ?? '').toUpperCase()
    const url = err.config?.url ?? ''

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
