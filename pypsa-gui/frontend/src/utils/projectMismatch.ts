// A2 — the tab / backend project mismatch (deferred spec 2026-09-28 §2.1).
// After a backend restart (or another tab / client swapping the binding) this
// tab can hold `currentProject = X` while the backend is bound to Y: every
// edit from the tab would land in Y's live network. The pieces that react to
// the mismatch share this module: the sentence, the axios allowlist and the
// client-side refusal.

export interface ProjectMismatch {
  /** The project this tab shows (`currentProject`). */
  tab: string
  /** The project the backend is bound to (`/network/meta.loaded_project`). */
  backend: string
}

export const PROJECT_MISMATCH = 'project_mismatch'

/** The banner's sentence (also the chat gate's and the Save button's). */
export function mismatchSentence(m: ProjectMismatch): string {
  return `This tab shows ${m.tab}, but the app is now on ${m.backend}. Changes from this tab are paused.`
}

const WRITE_METHODS = new Set(['post', 'put', 'delete', 'patch'])

/** `config.url` relative to the client's `/api` base, without a query. */
function apiPath(url: string | undefined): string {
  let u = url ?? ''
  try {
    if (/^https?:\/\//.test(u)) u = new URL(u).pathname
  } catch { /* keep as is */ }
  u = u.split('?')[0]
  if (u.startsWith('/api/')) u = u.slice(4)
  return u.startsWith('/') ? u : `/${u}`
}

/**
 * Whether a request may leave while the tab is mismatched. Reads always may.
 * Writes only on the way out (`POST /projects/<x>/activate`: the Switch
 * button and `switchToProject`), the edit-lock routes (acquire / release /
 * heartbeat — lease metadata), to the chat / local-settings / auth routes
 * (the chat's own Send is gated in ChatPanel — its stream is a raw fetch),
 * and the tab's own layout sidecar (`PUT /projects/<tab>/layout` targets the
 * tab's project by name, not the backend's live network).
 */
export function mismatchAllows(method: string | undefined, url: string | undefined,
  m: ProjectMismatch): boolean {
  const verb = (method ?? 'get').toLowerCase()
  if (!WRITE_METHODS.has(verb)) return true
  const p = apiPath(url)
  if (verb === 'post' && /^\/projects\/[^/]+\/activate\/?$/.test(p)) return true
  if (p.startsWith('/chat/') || p === '/chat') return true
  if (p.startsWith('/local-settings/') || p === '/local-settings') return true
  if (p.startsWith('/auth/')) return true
  // P27b gate B1 — the multi-user edit lock (auth mode, the default build):
  // acquire / release / heartbeat move lease metadata, not the live network.
  // Refusing them left Switch read-only and cost a mismatched tab its lock.
  if ((verb === 'post' || verb === 'delete') && /^\/projects\/[^/]+\/lock\/?$/.test(p)) return true
  if (verb === 'post' && /^\/projects\/[^/]+\/lock\/heartbeat\/?$/.test(p)) return true
  if (verb === 'put' && p === `/projects/${encodeURIComponent(m.tab)}/layout`) return true
  return false
}
