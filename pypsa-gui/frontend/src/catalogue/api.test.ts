import { afterEach, describe, expect, it, vi } from 'vitest'
import { client } from '../api/client'
import { fetchCatalogue, resetCatalogueCache, type Catalogue } from './api'

// GK B3 C1: the ETag client.
const BODY = { version: 'v1', groups: [], functions: [], asset_types: [], entries: [] } as Catalogue
const ok = (data: unknown, etag?: string) => ({ status: 200, data, headers: etag ? { etag } : {} })
const notModified = { status: 304, data: '', headers: {} }

afterEach(() => { vi.restoreAllMocks(); resetCatalogueCache() })

describe('fetchCatalogue', () => {
  it('200 then 304: one body read, the second request revalidates and reuses it', async () => {
    const get = vi.spyOn(client, 'get').mockResolvedValueOnce(ok(BODY, '"abc"')).mockResolvedValueOnce(notModified)
    expect(await fetchCatalogue()).toBe(BODY)
    expect(get.mock.calls[0][1]?.headers).toEqual({})
    expect(await fetchCatalogue()).toBe(BODY)
    expect(get.mock.calls[1][1]?.headers).toEqual({ 'If-None-Match': '"abc"' })
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('a 304 with no body held refetches without the header, never returning undefined', async () => {
    const get = vi.spyOn(client, 'get').mockResolvedValueOnce(notModified).mockResolvedValueOnce(ok(BODY, '"abc"'))
    expect(await fetchCatalogue()).toBe(BODY)
    expect(get).toHaveBeenCalledTimes(2)
    expect(get.mock.calls[1][1]?.headers).toEqual({})
  })

  it('a changed ETag (200) replaces the held body and tag', async () => {
    const next = { ...BODY, version: 'v2' }
    const get = vi.spyOn(client, 'get')
      .mockResolvedValueOnce(ok(BODY, '"a"')).mockResolvedValueOnce(ok(next, '"b"')).mockResolvedValueOnce(notModified)
    await fetchCatalogue()
    expect(await fetchCatalogue()).toBe(next)
    expect(await fetchCatalogue()).toBe(next)
    expect(get.mock.calls[2][1]?.headers).toEqual({ 'If-None-Match': '"b"' })
  })

  it('a body without an ETag is not held, so the next request is unconditional', async () => {
    const get = vi.spyOn(client, 'get').mockResolvedValueOnce(ok(BODY)).mockResolvedValueOnce(ok(BODY))
    await fetchCatalogue()
    await fetchCatalogue()
    expect(get.mock.calls[1][1]?.headers).toEqual({})
  })
})
