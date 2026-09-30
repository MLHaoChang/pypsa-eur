// The finance api clients (IC P4 WP4.7): the finance inputs with If-Match, the
// investment-case study and its report. The axios client is mocked so the tests
// pin the CONVENTIONS — paths, bodies, If-Match, 204 → null, and the typed
// 412 / 409 errors — not the transport.
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()
const put = vi.fn()

vi.mock('./client', () => ({
  default: { get, post, put, delete: vi.fn(), defaults: { baseURL: '/api' } },
  formatApiDetail: (d: unknown, fallback = 'Unknown error') =>
    (typeof d === 'string' ? d : d == null ? fallback : JSON.stringify(d)),
}))

const { financeApi, StaleEditError, SolverInFlightError, StudyBusyError } = await import('./finance')

beforeEach(() => { get.mockReset(); post.mockReset(); put.mockReset() })

function axiosError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true, response: { status, data: { detail } },
  })
}

describe('financeApi', () => {
  it('reads and writes the finance inputs with If-Match', async () => {
    get.mockResolvedValue({ status: 200, data: { finance: null, digest: 'd0', status: 'not_set' } })
    await expect(financeApi.getFinance()).resolves.toEqual({ finance: null, digest: 'd0', status: 'not_set' })
    expect(get).toHaveBeenCalledWith('/simulation/finance', { skipErrorToast: true })
    put.mockResolvedValue({ status: 200, data: { finance: null, digest: 'd1', status: 'not_set' } })
    await financeApi.putFinance(null, 'd0')
    expect(put).toHaveBeenCalledWith('/simulation/finance', { finance: null },
      { headers: { 'If-Match': 'd0' }, skipErrorToast: true })
  })

  it('a 412 is a stale edit; a 409 names the solve or the study; a 422 passes through', async () => {
    put.mockRejectedValueOnce(axiosError(412, { code: 'finance_changed', message: 'changed' }))
    await expect(financeApi.putFinance(null, 'd')).rejects.toBeInstanceOf(StaleEditError)
    put.mockRejectedValueOnce(axiosError(409, { code: 'solver_in_flight', message: 'busy' }))
    await expect(financeApi.putFinance(null, 'd')).rejects.toBeInstanceOf(SolverInFlightError)
    post.mockRejectedValueOnce(axiosError(409, { code: 'study_in_flight', message: 'a frontier study is running' }))
    await expect(financeApi.startInvestmentCase()).rejects.toThrow(StudyBusyError)
    const e422 = axiosError(422, { code: 'finance_inputs_invalid', errors: [] })
    put.mockRejectedValueOnce(e422)
    await expect(financeApi.putFinance(null, 'd')).rejects.toBe(e422)
  })

  it('runs, polls, aborts; 204 is null; the xlsx is a link', async () => {
    post.mockResolvedValue({ status: 200, data: { status: 'running' } })
    await financeApi.startInvestmentCase()
    expect(post).toHaveBeenLastCalledWith('/results/investment_case', {}, { skipErrorToast: true })
    await financeApi.abortInvestmentCase()
    expect(post).toHaveBeenLastCalledWith('/results/investment_case/abort', {}, { skipErrorToast: true })
    get.mockResolvedValue({ status: 204, data: '' })
    await expect(financeApi.getInvestmentCase()).resolves.toBeNull()
    await expect(financeApi.getReport()).resolves.toBeNull()
    expect(get).toHaveBeenLastCalledWith('/results/investment_case/report', { skipErrorToast: true })
    expect(financeApi.exportXlsxUrl()).toBe('/api/results/investment_case/export.xlsx')
  })
})
