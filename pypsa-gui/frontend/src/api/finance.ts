// Edge Investment Case finance clients (IC P4 WP4.6b / WP4.7): the finance
// inputs (their own route, If-Match always sent — the value-flows pattern)
// and the investment-case study (start / poll / abort, the report, the xlsx).
import client from './client'
import { SolverInFlightError, StaleEditError } from './commercial'
import type {
  FinanceInputs, FinanceState, InvestmentCaseReportPayload, InvestmentCaseStudy,
} from './types'

export { SolverInFlightError, StaleEditError }

/** A 409 on the finance PUT or the study start that is not a solve: another
 *  study runs (the detail names it). */
export class StudyBusyError extends Error {
  constructor(message: string) { super(message); this.name = 'StudyBusyError' }
}

const QUIET = { skipErrorToast: true } as const

function detailOf(e: unknown): { status?: number; code?: string; message?: string } {
  const r = (e as { response?: { status?: number; data?: { detail?: unknown; code?: unknown } } })?.response
  const d = r?.data?.detail
  const obj = (d && typeof d === 'object' && !Array.isArray(d)) ? d as Record<string, unknown> : {}
  const code = (obj.code ?? obj.error_kind ?? r?.data?.code) as string | undefined
  return { status: r?.status, code,
           message: (obj.message as string | undefined) ?? (typeof d === 'string' ? d : undefined) }
}

/** 412 → StaleEditError (`finance_changed`); 409 → the solve or the study
 *  that blocks it. A 422 is rethrown as is: the form maps its `detail`. */
function typed(e: unknown): never {
  const { status, code, message } = detailOf(e)
  if (status === 412) throw new StaleEditError(message ?? 'the finance inputs changed since they were read')
  if (status === 409) {
    if (code === 'solver_in_flight') throw new SolverInFlightError(message ?? 'a solve is running')
    throw new StudyBusyError(message ?? 'a solve or another study is running')
  }
  throw e
}

const orNull = <T,>(r: { status: number; data: T }) => (r.status === 204 ? null : r.data)

export const financeApi = {
  getFinance: () =>
    client.get<FinanceState>('/simulation/finance', QUIET).then(r => r.data, typed),
  /** `ifMatch` is the digest of the GET this edit started from — always sent. */
  putFinance: (finance: FinanceInputs | null, ifMatch: string) =>
    client.put<FinanceState>('/simulation/finance', { finance },
      { headers: { 'If-Match': ifMatch }, skipErrorToast: true })
      .then(r => r.data, typed),
  startInvestmentCase: () =>
    client.post<InvestmentCaseStudy>('/results/investment_case', {}, QUIET)
      .then(r => r.data, typed),
  /** The study record; 204 (never run) → null. */
  getInvestmentCase: () =>
    client.get<InvestmentCaseStudy>('/results/investment_case', QUIET).then(orNull),
  abortInvestmentCase: () =>
    client.post<{ status?: string; aborting?: boolean }>('/results/investment_case/abort', {}, QUIET)
      .then(r => r.data),
  /** The stored report with its sections and cashflow lines (`detail=full`);
   *  204 before a run → null. */
  getReport: () =>
    client.get<InvestmentCaseReportPayload>('/results/investment_case/report',
      { ...QUIET, params: { detail: 'full' } }).then(orNull),
  /** Absolute URL of the workbook — an <a download> href, streamed by the browser. */
  exportXlsxUrl: () => `${client.defaults.baseURL ?? ''}/results/investment_case/export.xlsx`,
}
