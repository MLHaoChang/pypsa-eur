import axios from 'axios'
import { client } from './client'
import type { SiteContext, SitesDocument } from '../site3d/types'

// The 3D site view's sidecar (`sites.json`), a project sub-resource beside
// `/layout`. GET degrades to the empty document; PUT validates (422), caps
// (413) and is lock-checked (409 `project_locked`, quiet-toasted).
//
// The site context (`sites/<id>/context.json`, WP5) is a per-site cache: GET
// never fetches upstream (404 on a miss → null here), POST spends the
// Overpass + terrain call and caches (502 carries the upstream's message,
// 422 a boundary too large to fetch). POST is lock-checked like PUT. The
// backend's DELETE (drop the cache) has no caller in the UI: Refresh
// re-POSTs, which overwrites the cache only on success.
const site = (project: string, siteId: string) =>
  `/projects/${encodeURIComponent(project)}/sites/${encodeURIComponent(siteId)}/context`

export const sitesApi = {
  getSites: (project: string) =>
    client.get<SitesDocument>(`/projects/${encodeURIComponent(project)}/sites`).then(r => r.data),
  putSites: (project: string, doc: SitesDocument) =>
    client.put<{ saved: string; sites: number }>(`/projects/${encodeURIComponent(project)}/sites`, doc).then(r => r.data),
  getContext: (project: string, siteId: string): Promise<SiteContext | null> =>
    client.get<SiteContext>(site(project, siteId)).then(r => r.data).catch((err: unknown) => {
      if (axios.isAxiosError(err) && err.response?.status === 404) return null
      throw err
    }),
  /** Fetch upstream and (re)write the cache; also what Refresh calls, so a failed refresh keeps the old cache. */
  fetchContext: (project: string, siteId: string) =>
    client.post<SiteContext>(site(project, siteId)).then(r => r.data),
}

/**
 * Whether a failed context fetch should hold off retrying for a while: only
 * when the UPSTREAM said no (502 from the backend, a 429, or no answer at
 * all). A 409 (lock / solve in flight) or a 422 (boundary too large) is
 * about this project's state, costs Overpass nothing, and must not block
 * the next open once the solve ends or the boundary shrinks.
 */
export function contextFailureBacksOff(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false
  const status = err.response?.status
  return status === undefined || status === 502 || status === 429 || status === 504
}

/** The message a failed context fetch should show: the 502 detail (actionable, from the backend) or the generic error. */
export function contextErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail) return detail
    if (err.response?.status === 409) return 'the project is locked or a solve is running'
  }
  return err instanceof Error ? err.message : String(err)
}
