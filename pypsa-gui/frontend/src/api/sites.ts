import axios from 'axios'
import { client } from './client'
import type { SiteContext, SitesDocument } from '../site3d/types'

// The 3D site view's sidecar (`sites.json`), a project sub-resource beside
// `/layout`. GET degrades to the empty document; PUT validates (422), caps
// (413) and is lock-checked (409 `project_locked`, quiet-toasted).
//
// The site context (`sites/<id>/context.json`, WP5) is a per-site cache: GET
// never fetches upstream (404 on a miss → null here), POST spends the
// Overpass + terrain call and caches (502 carries the upstream's message),
// DELETE drops the cache. POST and DELETE are lock-checked like PUT.
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
  fetchContext: (project: string, siteId: string) =>
    client.post<SiteContext>(site(project, siteId)).then(r => r.data),
  clearContext: (project: string, siteId: string) =>
    client.delete<{ cleared: boolean }>(site(project, siteId)).then(r => r.data),
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
