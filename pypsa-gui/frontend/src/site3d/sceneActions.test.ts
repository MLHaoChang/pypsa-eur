// SC-01b adoption order: the pending sites PUT lands first, then the digest is
// read from the server (never computed here), then adopt sends it back.
import { AxiosError } from 'axios'
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn() } }))
vi.mock('../api/siteScene', async (orig) => ({
  ...(await orig<typeof import('../api/siteScene')>()),
  siteSceneApi: { getScene: vi.fn(), adopt: vi.fn(), propose: vi.fn(), apply: vi.fn(), cancel: vi.fn() },
}))

import { sitesApi } from '../api/sites'
import { siteSceneApi, type SiteScene } from '../api/siteScene'
import { useUIStore } from '../store/uiStore'
import { SitesNotSaved, adoptSite, createSite, propose } from './sceneActions'
import { useSitesStore } from './sitesStore'
import type { Site } from './types'

const calls: string[] = []
const site: Site = { id: 'campus', name: 'Campus', buses: [], origin: { lng: 11, lat: 48 }, placements: {},
  boundary: [[10.999, 47.999], [11.001, 47.999], [11.001, 48.001]] }
const enabled: SiteScene = { state: 'enabled', site_id: 'campus', revision: 1, generation: 'g1', site: {}, buildings: [],
  asset_footprints: {}, last_receipt: null, unbound_keys: [] }

beforeEach(() => {
  calls.length = 0
  vi.clearAllMocks()
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
  vi.mocked(sitesApi.putSites).mockImplementation(async () => { calls.push('put'); return { saved: 'p', sites: 1 } })
  vi.mocked(sitesApi.getSites).mockImplementation(async () => { calls.push('get-sites'); return { version: 1, sites: [site] } })
  vi.mocked(siteSceneApi.getScene).mockImplementation(async () => { calls.push('get-scene'); return { state: 'unadopted', site_id: 'campus', digest: 'd'.repeat(64) } })
  vi.mocked(siteSceneApi.adopt).mockImplementation(async (_p, _s, digest) => { calls.push(`adopt:${digest.slice(0, 2)}`); return enabled })
})

describe('adoption', () => {
  it('saves the Site, reads the server digest, adopts with it, then reloads and marks it managed', async () => {
    const qc = new QueryClient()
    await createSite(qc, 'p', site)
    expect(calls).toEqual(['put', 'get-scene', 'adopt:dd', 'get-sites'])
    expect(useSitesStore.getState().isSceneManaged('p', 'campus')).toBe(true)
  })

  it('refuses to adopt when the Site did not reach the server', async () => {
    vi.mocked(sitesApi.putSites).mockRejectedValue(new AxiosError('down', 'ERR_NETWORK'))
    await expect(createSite(new QueryClient(), 'p', site)).rejects.toBeInstanceOf(SitesNotSaved)
    expect(siteSceneApi.adopt).not.toHaveBeenCalled()
  })

  it('an enabled Site is not adopted twice', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue(enabled)
    await adoptSite(new QueryClient(), 'p', 'campus')
    expect(siteSceneApi.adopt).not.toHaveBeenCalled()
    expect(useSitesStore.getState().isSceneManaged('p', 'campus')).toBe(true)
  })

  it('a proposal uses the scene revision it adopted at', async () => {
    vi.mocked(siteSceneApi.getScene).mockResolvedValue({ ...enabled, revision: 7 })
    await propose(new QueryClient(), 'p', 'campus', 'generate_native', { greenfield: [] })
    expect(siteSceneApi.propose).toHaveBeenCalledWith('p', 'campus', 'generate_native', 7, { greenfield: [] })
  })
})
