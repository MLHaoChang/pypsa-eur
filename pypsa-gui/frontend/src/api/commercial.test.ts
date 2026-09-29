// The commercial api clients (IC P3 WP3.5): the Library, the value-flow config
// with If-Match, and the commercial results. The axios client is mocked so the
// tests pin the CONVENTIONS — paths, bodies, If-Match, 204 → null, and the
// typed 409 / 412 errors — not the transport.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
const put = vi.fn()

vi.mock('./client', () => ({
  default: { get, post, put, delete: vi.fn() },
  formatApiDetail: (d: unknown, fallback = 'Unknown error') =>
    (typeof d === 'string' ? d : d == null ? fallback : JSON.stringify(d)),
}))

const { libraryApi, commercialApi, StaleEditError, SolverInFlightError, NoCommercialConfigError } =
  await import('./commercial')

beforeEach(() => { get.mockReset(); post.mockReset(); put.mockReset() })

function axiosError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true, response: { status, data: { detail } },
  })
}

describe('libraryApi', () => {
  it('lists, reads and writes items per kind', async () => {
    get.mockResolvedValue({ status: 200, data: [{ kind: 'tariff', id: 't', version: 1, hash: 'h' }] })
    await expect(libraryApi.listItems('tariff')).resolves.toHaveLength(1)
    expect(get).toHaveBeenCalledWith('/library/items/tariff')
    get.mockResolvedValue({ status: 200, data: { ref: {}, payload: {}, meta: {} } })
    await libraryApi.getItem('contract', 'ppa one', 3)
    expect(get).toHaveBeenLastCalledWith('/library/items/contract/ppa%20one', { params: { version: 3 } })
    put.mockResolvedValue({ status: 200, data: { kind: 'tariff', id: 't', version: 2, hash: 'h2' } })
    await libraryApi.putItem('tariff', 't', { id: 't' }, { source: 'builder' })
    expect(put).toHaveBeenCalledWith('/library/items/tariff/t', { payload: { id: 't' }, meta: { source: 'builder' } })
  })

  it('imports a URDB rate with its options', async () => {
    post.mockResolvedValue({ status: 200, data: { ref: {}, notes: [], refusals: [], unsupported_fields: [] } })
    await libraryApi.importUrdb({ urdb_response: { name: 'r' }, name: 'r1', accept_partial: true })
    expect(post).toHaveBeenCalledWith('/library/items/tariff/import_urdb',
      { urdb_response: { name: 'r' }, name: 'r1', accept_partial: true })
  })

  it('uploads series and meter data as multipart forms', async () => {
    post.mockResolvedValue({ status: 200, data: {} })
    const file = new File(['t,v\n'], 'p.csv', { type: 'text/csv' })
    await libraryApi.uploadSeries(file, { name: 'px', timezone: 'Europe/Berlin' })
    const [url, form] = post.mock.calls[0]
    expect(url).toBe('/library/series/upload')
    expect((form as FormData).get('name')).toBe('px')
    expect((form as FormData).get('timezone')).toBe('Europe/Berlin')
    await libraryApi.uploadMeterData(file, { name: 'm', unit: 'kW' })
    const [url2, form2] = post.mock.calls[1]
    expect(url2).toBe('/library/meter_data')
    expect((form2 as FormData).get('unit')).toBe('kW')
    expect((form2 as FormData).get('timezone')).toBeNull()
  })
})

describe('commercialApi value flows', () => {
  it('reads the config and its digest', async () => {
    get.mockResolvedValue({ status: 200, data: { value_flows: null, digest: 'd0', status: 'not_set' } })
    await expect(commercialApi.getValueFlows()).resolves.toMatchObject({ digest: 'd0' })
    expect(get).toHaveBeenCalledWith('/simulation/commercial/value_flows')
  })

  it('always sends If-Match and wraps the body', async () => {
    put.mockResolvedValue({ status: 200, data: { value_flows: { template: 'custom' }, digest: 'd1', status: 'ok' } })
    await commercialApi.putValueFlows({ template: 'custom' }, 'd0')
    expect(put).toHaveBeenCalledWith('/simulation/commercial/value_flows',
      { value_flows: { template: 'custom' } }, { headers: { 'If-Match': 'd0' }, skipErrorToast: true })
  })

  it('clears with an explicit null', async () => {
    put.mockResolvedValue({ status: 200, data: { value_flows: null, digest: 'd2', status: 'not_set' } })
    await commercialApi.putValueFlows(null, 'd1')
    expect(put.mock.calls[0][1]).toEqual({ value_flows: null })
  })

  it('turns a 412 into a StaleEditError and a solve-in-flight 409 into its own error', async () => {
    put.mockRejectedValue(axiosError(412, { code: 'value_flows_changed', message: 'changed' }))
    await expect(commercialApi.putValueFlows({}, 'old')).rejects.toBeInstanceOf(StaleEditError)
    put.mockRejectedValue(axiosError(409, { code: 'solver_in_flight', message: 'busy' }))
    await expect(commercialApi.putValueFlows({}, 'd')).rejects.toBeInstanceOf(SolverInFlightError)
    const other = axiosError(422, { code: 'value_flows_invalid', problems: ['x'] })
    put.mockRejectedValue(other)
    await expect(commercialApi.putValueFlows({}, 'd')).rejects.toBe(other)
  })
})

describe('commercialApi commercial sub-tree writes', () => {
  it('refetches, replaces only its sub-tree and strips value_flows', async () => {
    get.mockResolvedValue({ status: 200, data: { commercial: {
      poc_link: 'import', site_party: 'site', contracts: [{ type: 'lease', id: 'old' }],
      value_flows: { template: 'btm_ppa' } } } })
    put.mockResolvedValue({ status: 200, data: {} })
    await commercialApi.saveCommercial({ contracts: [] })
    expect(get).toHaveBeenCalledWith('/simulation/solver_config')
    const [url, body] = put.mock.calls[0]
    expect(url).toBe('/simulation/solver_config')
    expect(body.commercial).toEqual({ poc_link: 'import', site_party: 'site', contracts: [] })
    expect('value_flows' in body.commercial).toBe(false)
  })

  it('refuses a sub-tree write when there is no commercial config and no poc_link', async () => {
    get.mockResolvedValue({ status: 200, data: { commercial: null } })
    await expect(commercialApi.saveCommercial({ contracts: [] }))
      .rejects.toBeInstanceOf(NoCommercialConfigError)
    expect(put).not.toHaveBeenCalled()
  })

  it('two editors saving different sub-trees in sequence keep both', async () => {
    let stored: Record<string, unknown> = { poc_link: 'import', contracts: [],
                                            value_flows: { template: 'custom' } }
    get.mockImplementation(async () => ({ status: 200, data: { commercial: stored } }))
    put.mockImplementation(async (_url: string, body: { commercial: Record<string, unknown> }) => {
      stored = { ...body.commercial, value_flows: stored.value_flows }   // the server keeps it
      return { status: 200, data: { commercial: stored } }
    })
    await commercialApi.saveCommercial({ contracts: [{ type: 'lease', id: 'l1' } as never] })
    await commercialApi.saveCommercial({ timezone: 'Europe/Berlin' })
    const last = put.mock.calls[1][1].commercial
    expect(last.contracts).toEqual([{ type: 'lease', id: 'l1' }])
    expect(last.timezone).toBe('Europe/Berlin')
    expect(stored.value_flows).toEqual({ template: 'custom' })
  })

  it('maps the value-flows route\'s no-commercial-config 409 to its error', async () => {
    put.mockRejectedValue(axiosError(409, { code: 'no_commercial_config', message: 'no' }))
    await expect(commercialApi.putValueFlows({}, 'd')).rejects.toBeInstanceOf(NoCommercialConfigError)
  })
})

describe('commercialApi results', () => {
  it.each([
    ['getBilling', '/results/billing'],
    ['getCfeScore', '/results/cfe_score'],
    ['getValueFlowsResult', '/results/value_flows'],
  ] as const)('%s maps 204 to null, quietly', async (fn, path) => {
    get.mockResolvedValue({ status: 204, data: '' })
    await expect(commercialApi[fn]()).resolves.toBeNull()
    expect(get).toHaveBeenCalledWith(path, { skipErrorToast: true })
  })

  it.each(['getBilling', 'getCfeScore', 'getValueFlowsResult'] as const)(
    '%s turns a solve-in-flight 409 into its error', async (fn) => {
      get.mockRejectedValue(axiosError(409, { code: 'solver_in_flight', message: 'busy' }))
      await expect(commercialApi[fn]()).rejects.toBeInstanceOf(SolverInFlightError)
    })

  it('a 404 reads as no result only for the routes not deployed yet', async () => {
    const notFound = axiosError(404, 'Not Found')
    get.mockRejectedValue(notFound)
    await expect(commercialApi.getValueFlowsResult()).resolves.toBeNull()
    await expect(commercialApi.getBilling()).rejects.toBe(notFound)       // a real fault
    await expect(commercialApi.getCfeScore()).rejects.toBe(notFound)
  })

  it('previewBilling types its errors too', async () => {
    post.mockRejectedValue(axiosError(409, { code: 'solver_in_flight', message: 'busy' }))
    await expect(commercialApi.previewBilling({ id: 't', name: 't', jurisdiction: 'US',
      valid_from: '2030-01-01', items: [] })).rejects.toBeInstanceOf(SolverInFlightError)
  })

  it('previews a draft tariff\'s bill', async () => {
    post.mockResolvedValue({ status: 200, data: { summary: {} } })
    await commercialApi.previewBilling({ id: 't', name: 't', jurisdiction: 'US', valid_from: '2030-01-01', items: [] })
    expect(post).toHaveBeenCalledWith('/results/billing/preview',
      { tariff: { id: 't', name: 't', jurisdiction: 'US', valid_from: '2030-01-01', items: [] } },
      { skipErrorToast: true })
  })

  it('builds a template without saving it', async () => {
    post.mockResolvedValue({ status: 200, data: { config: {}, draft_contracts: [], notes: [] } })
    await commercialApi.buildTemplate('btm_ppa')
    expect(post).toHaveBeenCalledWith('/simulation/value_flows/template', { template: 'btm_ppa' })
  })
})
