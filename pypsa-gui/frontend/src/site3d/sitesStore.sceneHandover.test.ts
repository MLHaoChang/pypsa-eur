// SC-01b sitesStore handover: a Site whose 3D scene owns it is never edited
// through the whole-document PUT. A 409 `scene_managed_site` is not a failed
// PUT to keep in localStorage: the out-of-date copy is dropped and the
// server's document read back.
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sites', () => ({ sitesApi: { getSites: vi.fn(), putSites: vi.fn() } }))

import { sitesApi } from '../api/sites'
import { useUIStore } from '../store/uiStore'
import { MANAGED_SITE_COPY, SITES_SAVE_DEBOUNCE_MS, flushPendingSitesToServer, localSitesKey, persistSitesOnUnload, useSitesStore } from './sitesStore'
import type { Site, SitesDocument } from './types'

const getSites = vi.mocked(sitesApi.getSites)
const putSites = vi.mocked(sitesApi.putSites)
const site = (over: Partial<Site> = {}): Site => ({
  id: 'site_a', name: 'Campus', buses: [], origin: { lng: 6.835, lat: 53.435 }, placements: {},
  boundary: [[6.83, 53.44], [6.84, 53.44], [6.84, 53.43]], ...over,
})
const doc = (sites: Site[]): SitesDocument => ({ version: 1, sites })
const managed409 = () => {
  const headers = new AxiosHeaders()
  return new AxiosError('conflict', 'ERR_BAD_REQUEST', { headers }, null, {
    status: 409, statusText: '', headers: {}, config: { headers },
    data: { detail: { error_kind: 'scene_managed_site', message: 'x', site_id: 'site_a' } },
  })
}

beforeEach(async () => {
  vi.useFakeTimers()
  useSitesStore.getState().resetForTests()
  useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
  getSites.mockReset(); putSites.mockReset()
  localStorage.clear()
  getSites.mockResolvedValue(doc([site()]))
  await useSitesStore.getState().ensureLoaded('p')
})

describe('scene-managed Sites', () => {
  it('refuses a local edit to a managed Site with visible copy and arms no PUT', async () => {
    const s = useSitesStore.getState()
    s.markSceneManaged('p', 'site_a')
    s.setPlacement('p', 'site_a', 'Generator:gas', { x: 1, y: 2, heading: 0 })
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS * 2)
    expect(putSites).not.toHaveBeenCalled()
    expect(useSitesStore.getState().dirty.p).toBeUndefined()
    expect(useSitesStore.getState().sceneRefusal.p).toBe(MANAGED_SITE_COPY)
    expect(useSitesStore.getState().siteById('p', 'site_a')?.placements).toEqual({})
  })

  it('refuses a footprint edit for equipment placed on a managed Site', () => {
    getSites.mockResolvedValue(doc([site({ placements: { 'Generator:gas': { x: 0, y: 0, heading: 0 } } })]))
    return useSitesStore.getState().ensureLoaded('p', true).then(() => {
      const s = useSitesStore.getState()
      s.markSceneManaged('p', 'site_a')
      s.setAssetFootprint('p', 'Generator:gas', { width_m: 2, depth_m: 2 })
      expect(useSitesStore.getState().docFor('p').asset_footprints).toBeUndefined()
      expect(useSitesStore.getState().sceneRefusal.p).toBe(MANAGED_SITE_COPY)
    })
  })

  it('still edits a Site the scene does not own', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:gas', { x: 1, y: 2, heading: 0 })
    putSites.mockResolvedValue({ saved: 'p', sites: 1 })
    await vi.advanceTimersByTimeAsync(SITES_SAVE_DEBOUNCE_MS + 1)
    expect(putSites).toHaveBeenCalledTimes(1)
  })

  it('on a 409 scene_managed_site: no localStorage, the server document back, the Site marked managed', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:gas', { x: 1, y: 2, heading: 0 })
    putSites.mockRejectedValue(managed409())
    getSites.mockResolvedValue(doc([site({ name: 'Server copy' })]))
    const r = await flushPendingSitesToServer('p')
    expect(r.status).toBe('refused')
    expect(localStorage.getItem(localSitesKey('p'))).toBeNull()
    const st = useSitesStore.getState()
    expect(st.dirty.p).toBeUndefined()
    expect(st.siteById('p', 'site_a')?.name).toBe('Server copy')
    expect(st.isSceneManaged('p', 'site_a')).toBe(true)
    expect(st.sceneRefusal.p).toBe(MANAGED_SITE_COPY)
  })

  it('leaves nothing for the unload keepalive after a refusal', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:gas', { x: 1, y: 2, heading: 0 })
    putSites.mockRejectedValue(managed409())
    await flushPendingSitesToServer('p')
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response())
    persistSitesOnUnload('p')
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(localStorage.getItem(localSitesKey('p'))).toBeNull()
    fetchSpy.mockRestore()
  })

  it('an unreachable server still falls back to localStorage', async () => {
    useSitesStore.getState().setPlacement('p', 'site_a', 'Generator:gas', { x: 1, y: 2, heading: 0 })
    putSites.mockRejectedValue(new AxiosError('down', 'ERR_NETWORK'))
    expect((await flushPendingSitesToServer('p')).status).toBe('local')
    expect(localStorage.getItem(localSitesKey('p'))).not.toBeNull()
  })
})
