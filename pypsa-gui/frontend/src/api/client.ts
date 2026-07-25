import axios from 'axios'
import toast from 'react-hot-toast'
import { appLog } from '../store/simulationStore'

export const client = axios.create({
  baseURL: '/api',
  timeout: 30000,
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
    const code = typeof data?.code === 'string' ? data.code : undefined
    const msg = formatApiDetail(data?.detail ?? err.message)
    const method = (err.config?.method ?? '').toUpperCase()
    const url = err.config?.url ?? ''

    // Quiet poll endpoints (undo/info, changelog) silently fail during the
    // brief uvicorn --reload window. They'll succeed on the next interval; no
    // need to log an error or pop a toast.
    if (!isQuietPoll(url, method)) {
      appLog('ERROR', `${method} ${url} — ${msg}${code ? ` [${code}]` : ''}`)
      if (!code || !QUIET_TOAST_CODES.has(code)) {
        toast.error(msg)
      }
    }
    return Promise.reject(err)
  },
)

export default client
