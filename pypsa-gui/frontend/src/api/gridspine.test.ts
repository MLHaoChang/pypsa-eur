// The gridspine API module maps one thunk to one `/api/gridspine` handler. The
// two things that go wrong in a module like this are the URL (a project name
// with a space or a slash must be encoded — study names are display names) and
// the request shape the backend's pydantic models expect. Both are pinned here
// against the axios client, with the network mocked out.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import client from './client'
import { gridspineApi, isNotAStudy } from './gridspine'

const get = vi.spyOn(client, 'get')
const post = vi.spyOn(client, 'post')
const put = vi.spyOn(client, 'put')

beforeEach(() => {
  get.mockResolvedValue({ data: {} } as never)
  post.mockResolvedValue({ data: {} } as never)
  put.mockResolvedValue({ data: {} } as never)
})
afterEach(() => vi.clearAllMocks())

describe('gridspineApi', () => {
  it('encodes the project name in every project-scoped URL', async () => {
    await gridspineApi.status('Winter 2030/a')
    expect(get).toHaveBeenCalledWith('/gridspine/Winter%202030%2Fa/status', expect.objectContaining({ skipErrorToast: true }))
    await gridspineApi.snapshots('Winter 2030/a')
    expect(get).toHaveBeenLastCalledWith('/gridspine/Winter%202030%2Fa/snapshots', expect.anything())
    await gridspineApi.ledger('Winter 2030/a')
    expect(get).toHaveBeenLastCalledWith('/gridspine/Winter%202030%2Fa/ledger', expect.anything())
  })

  it('creates a study with the name and config the backend model expects', async () => {
    await gridspineApi.createStudy('S', { hours: 24, k: 1 })
    expect(post).toHaveBeenCalledWith('/gridspine/projects', { name: 'S', config: { hours: 24, k: 1 } }, expect.anything())
  })

  it('sends one of the three dispatch sources in the shape the backend model expects', async () => {
    await gridspineApi.setDispatchSource('S', { kind: 'generate' })
    expect(post).toHaveBeenLastCalledWith('/gridspine/S/dispatch-source', { source: 'generate' }, expect.anything())
    await gridspineApi.setDispatchSource('S', { kind: 'from_dispatch', dir: '/runs/v3' })
    expect(post).toHaveBeenLastCalledWith(
      '/gridspine/S/dispatch-source', { source: 'from_dispatch', from_dispatch: '/runs/v3' }, expect.anything(),
    )
    await gridspineApi.setDispatchSource('S', { kind: 'from_project', project: 'Solved 39' })
    expect(post).toHaveBeenLastCalledWith(
      '/gridspine/S/dispatch-source', { source: 'from_project', from_project: 'Solved 39' }, expect.anything(),
    )
  })

  it('marks a UI template edit as edited_by user — the chat tool marks its own', async () => {
    await gridspineApi.editTemplateParam('S', 'G_BUS_32', 'h_s', 4.25, 'datasheet')
    expect(put).toHaveBeenCalledWith(
      '/gridspine/S/templates/G_BUS_32/h_s',
      { value: 4.25, source: 'datasheet', edited_by: 'user' },
      expect.anything(),
    )
  })

  it('reads the config from its own endpoint', async () => {
    await gridspineApi.config('Winter 2030/a')
    expect(get).toHaveBeenCalledWith('/gridspine/Winter%202030%2Fa/config', expect.objectContaining({ skipErrorToast: true }))
  })

  it('PUTs only the fields in the patch, and marks from_dispatch explicitly when it is present', async () => {
    await gridspineApi.updateConfig('S', { k: 3 })
    expect(put).toHaveBeenLastCalledWith('/gridspine/S/config', { k: 3 }, expect.anything())
    // `null` means "generate": without the marker the backend would read it as "leave as is"
    await gridspineApi.updateConfig('S', { from_dispatch: null })
    expect(put).toHaveBeenLastCalledWith(
      '/gridspine/S/config', { from_dispatch: null, set_from_dispatch: true }, expect.anything(),
    )
    await gridspineApi.updateConfig('S', { hours: 48, from_dispatch: '/runs/v3' })
    expect(put).toHaveBeenLastCalledWith(
      '/gridspine/S/config', { hours: 48, from_dispatch: '/runs/v3', set_from_dispatch: true }, expect.anything(),
    )
  })

  it('uploads the PowerFactory export as multipart with the bus file required and the branch file optional', async () => {
    const bus = new File(['bus_name,vm_pu,va_degree\n'], 'case39_h19.csv', { type: 'text/csv' })
    const br = new File(['from_bus,to_bus,ckt\n'], 'case39_h19_branches.csv', { type: 'text/csv' })
    await gridspineApi.uploadReadback('S', 19, bus)
    let [url, body] = post.mock.lastCall as [string, FormData]
    expect(url).toBe('/gridspine/S/readback/19')
    expect(body.get('bus')).toBe(bus)
    expect(body.has('branches')).toBe(false)
    await gridspineApi.uploadReadback('S', 19, bus, br)
    ;[url, body] = post.mock.lastCall as [string, FormData]
    expect(body.get('branches')).toBe(br)
  })

  it('uploads a client dispatch as multipart, with the loads file optional for a workbook', async () => {
    const dispatch = new File(['unit_id,hour,p_mw,q_mvar,status\n'], 'market_dispatch.csv', { type: 'text/csv' })
    const loads = new File(['bus,hour,p_mw,q_mvar\n'], 'market_loads.csv', { type: 'text/csv' })
    await gridspineApi.uploadExternalDispatch('S', dispatch, loads)
    let [url, body] = post.mock.lastCall as [string, FormData]
    expect(url).toBe('/gridspine/S/dispatch-source/external')
    expect(body.get('dispatch')).toBe(dispatch)
    expect(body.get('loads')).toBe(loads)

    // One workbook carrying both sheets: `loads` must be ABSENT, not an empty
    // part — the backend passes None through to the producer, which then looks
    // for the two sheets instead of refusing an unreadable second file.
    const book = new File(['PK'], 'both.xlsx')
    await gridspineApi.uploadExternalDispatch('S', book)
    ;[url, body] = post.mock.lastCall as [string, FormData]
    expect(body.get('dispatch')).toBe(book)
    expect(body.has('loads')).toBe(false)
  })

  it('reads the read-back summary and one figure from their own endpoints', async () => {
    await gridspineApi.readback('S')
    expect(get).toHaveBeenLastCalledWith('/gridspine/S/readback', expect.anything())
    await gridspineApi.figure('S', 19, 'branch_p')
    expect(get).toHaveBeenLastCalledWith('/gridspine/S/figures/19/branch_p', expect.anything())
  })

  it('asks for the bundle as a blob', async () => {
    await gridspineApi.bundle('S', 7)
    expect(get).toHaveBeenCalledWith('/gridspine/S/bundles/7', expect.objectContaining({ responseType: 'blob' }))
  })

  it('recognises the backend’s 409 as "not a study" and nothing else', () => {
    expect(isNotAStudy({ response: { status: 409 } })).toBe(true)
    expect(isNotAStudy({ response: { status: 404 } })).toBe(false)
    expect(isNotAStudy(new Error('network'))).toBe(false)
    expect(isNotAStudy(null)).toBe(false)
  })
})
