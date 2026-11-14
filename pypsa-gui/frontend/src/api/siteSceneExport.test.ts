// SC-04: the export call names its format and media policy, takes the server's
// download name, and turns a refused blob response back into the scene error copy.
import { AxiosError, AxiosHeaders } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { client } from './client'
import { sceneErrorCopy, siteSceneApi } from './siteScene'

afterEach(() => { vi.restoreAllMocks() })

describe('siteSceneApi.exportScene', () => {
  it('posts glb_manifest with media none and returns the blob, its server name and revision', async () => {
    const blob = new Blob(['zip'])
    const post = vi.spyOn(client, 'post').mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': "attachment; filename=\"Cafe _Nord_-scene-r4.zip\"; filename*=UTF-8''Caf%C3%A9%20%22Nord%22-scene-r4.zip", 'x-scene-revision': '4' },
    })
    const out = await siteSceneApi.exportScene('p 1', 'campus')
    expect(post).toHaveBeenCalledWith('/projects/p%201/sites/campus/scene/exports',
      { format: 'glb_manifest', media_policy: 'none' }, expect.objectContaining({ responseType: 'blob', skipErrorToast: true }))
    expect(out).toEqual({ blob, filename: 'Café "Nord"-scene-r4.zip', revision: 4 })
  })

  it('reads a refusal sent as a Blob, so the toast says the limit in plain words', async () => {
    const detail = { detail: { error_kind: 'resource_limit', message: 'Too large to export: 300,000 triangles is over the 250,000 limit', limit: 'triangles' } }
    const err = new AxiosError('413', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 413, statusText: '', headers: {}, config: { headers: new AxiosHeaders() }, data: new Blob([JSON.stringify(detail)]),
    })
    vi.spyOn(client, 'post').mockRejectedValue(err)
    const caught = await siteSceneApi.exportScene('p', 'campus').catch(e => e)
    expect(sceneErrorCopy(caught)).toBe('Too large to export: 300,000 triangles is over the 250,000 limit')
  })
})
