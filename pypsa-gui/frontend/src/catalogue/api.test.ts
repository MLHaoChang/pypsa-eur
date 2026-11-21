import { afterEach, describe, expect, it, vi } from 'vitest'
import { client } from '../api/client'
import { fetchCatalogue, resetCatalogueCache, type Catalogue } from './api'

const BODY = { version: 'v1', groups: [], functions: [], asset_types: [], entries: [] } as Catalogue

afterEach(() => { vi.restoreAllMocks(); resetCatalogueCache() })

describe('fetchCatalogue', () => {
  it('revalidates with the held ETag and reuses the body on a 304', async () => {
    const get = vi.spyOn(client, 'get')
      .mockResolvedValueOnce({ status: 200, data: BODY, headers: { etag: '"abc"' } })
      .mockResolvedValueOnce({ status: 304, data: '', headers: { etag: '"abc"' } })
    expect(await fetchCatalogue()).toBe(BODY)
    expect(get.mock.calls[0][1]?.headers).toEqual({})
    expect(await fetchCatalogue()).toBe(BODY)
    expect(get.mock.calls[1][1]?.headers).toEqual({ 'If-None-Match': '"abc"' })
  })

  it('a 304 counts as success only while a body is held', async () => {
    const get = vi.spyOn(client, 'get').mockResolvedValueOnce({ status: 200, data: BODY, headers: {} })
    await fetchCatalogue()
    const validate = get.mock.calls[0][1]!.validateStatus!
    expect(validate(304)).toBe(false)
    expect(validate(200)).toBe(true)
  })

  it('a new body replaces the held one', async () => {
    const next = { ...BODY, version: 'v2' }
    vi.spyOn(client, 'get')
      .mockResolvedValueOnce({ status: 200, data: BODY, headers: { etag: '"a"' } })
      .mockResolvedValueOnce({ status: 200, data: next, headers: { etag: '"b"' } })
    await fetchCatalogue()
    expect(await fetchCatalogue()).toBe(next)
  })
})
