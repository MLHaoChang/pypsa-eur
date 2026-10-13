// The grid-code thunks map one to one onto the routes of
// `backend/routers/campus_electrical.py`. Pinned here against the axios
// client, with the network mocked out: the URL (a project name or a profile id
// is encoded), the multipart field name the upload route reads (`file`), and
// the body each pydantic model expects.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import client from './client'
import { campusApi } from './campusElectrical'

const get = vi.spyOn(client, 'get')
const post = vi.spyOn(client, 'post')
const put = vi.spyOn(client, 'put')
const del = vi.spyOn(client, 'delete')
const quiet = expect.objectContaining({ skipErrorToast: true })
const BASE = '/campus-electrical/Hub%20A/grid-codes'

beforeEach(() => {
  for (const m of [get, post, put, del]) m.mockResolvedValue({ data: {} } as never)
})
afterEach(() => vi.clearAllMocks())

describe('campusApi grid codes', () => {
  it('uploads the PDF as multipart field "file"', async () => {
    const file = new File(['%PDF-1.7'], 'vde.pdf', { type: 'application/pdf' })
    await campusApi.uploadGridCodeDocument('Hub A', file)
    const [url, body, cfg] = post.mock.calls[0]
    expect(url).toBe(`${BASE}/documents`)
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('file')).toBe(file)
    expect(cfg).toEqual(quiet)
  })

  it('reads and deletes by encoded project and id', async () => {
    await campusApi.gridCodes('Hub A')
    expect(get).toHaveBeenLastCalledWith(BASE, quiet)
    await campusApi.getGridCodeDraft('Hub A', 'vde_4110')
    expect(get).toHaveBeenLastCalledWith(`${BASE}/drafts/vde_4110`, quiet)
    await campusApi.getPublishedGridCode('Hub A', 'vde_4110')
    expect(get).toHaveBeenLastCalledWith(`${BASE}/published/vde_4110`, quiet)
    await campusApi.deleteGridCodeDocument('Hub A', 'abc')
    expect(del).toHaveBeenLastCalledWith(`${BASE}/documents/abc`, quiet)
    await campusApi.deleteGridCodeDraft('Hub A', 'vde_4110')
    expect(del).toHaveBeenLastCalledWith(`${BASE}/drafts/vde_4110`, quiet)
    await campusApi.deletePublishedGridCode('Hub A', 'vde_4110')
    expect(del).toHaveBeenLastCalledWith(`${BASE}/published/vde_4110`, quiet)
  })

  it('sends the bodies the backend models expect', async () => {
    await campusApi.extractGridCode('Hub A', 'abc', { profile_id: 'p', overwrite: true })
    expect(post).toHaveBeenLastCalledWith(`${BASE}/documents/abc/extract`, { profile_id: 'p', overwrite: true }, quiet)
    await campusApi.newGridCodeDraft('Hub A', { profile_id: 'p', title: 'T', overwrite: false })
    expect(post).toHaveBeenLastCalledWith(`${BASE}/drafts`, { profile_id: 'p', title: 'T', overwrite: false }, quiet)
    await campusApi.saveGridCodeDraft('Hub A', 'p', 'title: x')
    expect(put).toHaveBeenLastCalledWith(`${BASE}/drafts/p`, { yaml: 'title: x' }, quiet)
    await campusApi.confirmGridCodeLimit('Hub A', 'p', 'voltage_bands[0]')
    expect(post).toHaveBeenLastCalledWith(`${BASE}/drafts/p/confirm`, { limit: 'voltage_bands[0]' }, quiet)
    await campusApi.publishGridCode('Hub A', 'p', true)
    expect(post).toHaveBeenLastCalledWith(`${BASE}/drafts/p/publish`, { allow_unconfirmed: true }, quiet)
  })
})
