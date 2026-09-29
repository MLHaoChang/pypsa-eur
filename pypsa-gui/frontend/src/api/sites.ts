import { client } from './client'
import type { SitesDocument } from '../site3d/types'

// The 3D site view's sidecar (`sites.json`), a project sub-resource beside
// `/layout`. GET degrades to the empty document; PUT validates (422), caps
// (413) and is lock-checked (409 `project_locked`, quiet-toasted).
export const sitesApi = {
  getSites: (project: string) =>
    client.get<SitesDocument>(`/projects/${encodeURIComponent(project)}/sites`).then(r => r.data),
  putSites: (project: string, doc: SitesDocument) =>
    client.put<{ saved: string; sites: number }>(`/projects/${encodeURIComponent(project)}/sites`, doc).then(r => r.data),
}
