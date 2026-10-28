// SC-01b: the scene writes the UI makes, in the order the backend's contract
// needs. Adoption reads the server's digest (GET scene, unadopted) only after
// the pending sitesStore PUT has landed, so the digest covers what the user
// sees; afterwards the cached sites document is replaced by the server's
// projection and the Site is marked scene-managed (no legacy edits).
import type { QueryClient } from '@tanstack/react-query'
import { siteSceneApi, type BuildingsPreview, type Proposal, type ProposalAction, type ProposalInputs, type SiteScene } from '../api/siteScene'
import { nk } from '../utils/queryKeys'
import { adoptServerDocument, flushPendingSitesToServer, useSitesStore } from './sitesStore'
import type { Site } from './types'

export const SITE_SCENE_ROOT = 'site-scene'
export const sceneKey = (project: string | null, siteId: string | null) => nk(project, SITE_SCENE_ROOT, siteId)

/** Thrown when the pending sites write did not reach the server, so adoption would snapshot a different Site. */
export class SitesNotSaved extends Error {
  constructor() { super('Site changes are not saved yet: check the connection and try again') }
}

async function refreshAfterWrite(qc: QueryClient, project: string, siteId: string, scene?: SiteScene): Promise<void> {
  if (scene) qc.setQueryData(sceneKey(project, siteId), scene)
  else await qc.invalidateQueries({ queryKey: sceneKey(project, siteId) })
  await adoptServerDocument(project)
  useSitesStore.getState().markSceneManaged(project, siteId)
  void qc.invalidateQueries({ queryKey: nk(project, 'flow') })
}

/** Adopt a saved legacy Site as its 3D scene (generation 1). Idempotent for an already enabled Site. */
export async function adoptSite(qc: QueryClient, project: string, siteId: string): Promise<SiteScene> {
  const flushed = await flushPendingSitesToServer(project)
  if (flushed.status === 'local' || flushed.status === 'refused') throw new SitesNotSaved()
  const current = await siteSceneApi.getScene(project, siteId)
  if (current.state === 'enabled') {
    useSitesStore.getState().markSceneManaged(project, siteId)
    return current
  }
  const scene = await siteSceneApi.adopt(project, siteId, current.digest)
  await refreshAfterWrite(qc, project, siteId, scene)
  return scene
}

/** "Save site" in the New 3D site panel: save the empty Site, then adopt it. */
export async function createSite(qc: QueryClient, project: string, site: Site): Promise<SiteScene> {
  useSitesStore.getState().upsertSite(project, site)
  return adoptSite(qc, project, site.id)
}

/** Generate, edit, attach or place: the proposal against the scene's current revision (adopting a legacy Site first). */
export async function propose<P = BuildingsPreview>(qc: QueryClient, project: string, siteId: string, action: ProposalAction,
  inputs: ProposalInputs): Promise<Proposal<P>> {
  const scene = await adoptSite(qc, project, siteId)
  if (scene.state !== 'enabled') throw new Error('unreachable: adoption returns an enabled scene')
  return siteSceneApi.propose<P>(project, siteId, action, scene.revision, inputs)
}

export async function applyProposal(qc: QueryClient, project: string, proposal: Proposal<unknown>): Promise<void> {
  await siteSceneApi.apply(project, proposal.site_id, proposal)
  await refreshAfterWrite(qc, project, proposal.site_id)
}

export async function discardProposal(project: string, proposal: Proposal<unknown>): Promise<void> {
  await siteSceneApi.cancel(project, proposal.site_id, proposal.id)
}
