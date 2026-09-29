/**
 * `useReportJob` (WP7b) — the one place the frontend watches WP3's
 * generation job: it polls `GET …/generate/status` every 1.5 s while the
 * record says `running` (and not otherwise), exposes start / abort /
 * regenerate as mutations over the job routes, derives `isRunning` and
 * `progressPct`, and — on the poll where the record leaves `running` —
 * invalidates the reports list and the finished report's document queries
 * so the panel and the viewer refresh without a reload.
 *
 * .tsx, not .ts: the QueryClientProvider wrapper needs JSX (see
 * `useJobTerminalInvalidation.test.tsx` for the same rule).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { ReportJobRecord } from '../../api/reports'
import { REPORT_EVIDENCE_HASH_KEY, REPORT_JOB_KEY, REPORT_TEMPLATE_KEY, useReportJob } from './useReportJob'

const api = vi.hoisted(() => ({
  getGenerateStatus: vi.fn(),
  generateReport: vi.fn(),
  abortGenerate: vi.fn(),
  regenerateSection: vi.fn(),
  proposeMappingPlan: vi.fn(),
}))
vi.mock('../../api/reports', async () => {
  const real = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
  return { ...real, ...api }
})

const ID = 'a1b2c3d4e5f60718'

function record(over: Partial<ReportJobRecord> = {}): ReportJobRecord {
  return {
    status: 'running',
    report_id: ID,
    version: null,
    mode: 'generate',
    section: null,
    progress: { done: 1, total: 4, current: 'cost' },
    repairs: 0,
    prose_failures: [],
    error: null,
    started_at: 1_790_000_000,
    finished_at: null,
    profile_id: 'anthropic-default',
    model: 'claude-sonnet',
    ...over,
  }
}

function makeClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  api.getGenerateStatus.mockReset().mockResolvedValue(null)
  api.generateReport.mockReset().mockResolvedValue({ status: 'running', report_id: ID })
  api.abortGenerate.mockReset().mockResolvedValue({ status: 'running', aborting: true })
  api.regenerateSection.mockReset().mockResolvedValue({ status: 'running', report_id: ID, version: 1 })
  api.proposeMappingPlan.mockReset().mockResolvedValue({ status: 'running', report_id: ID })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useReportJob', () => {
  it('reads the record once and does not poll when the job is not running (204 → null)', async () => {
    const client = makeClient()
    const { result } = renderHook(() => useReportJob('Demo'), { wrapper: wrapper(client) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(1))
    expect(api.getGenerateStatus).toHaveBeenCalledWith('Demo')
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000) })
    expect(api.getGenerateStatus).toHaveBeenCalledTimes(1)
    expect(result.current.record).toBeNull()
    expect(result.current.isRunning).toBe(false)
    expect(result.current.progressPct).toBeNull()
  })

  it('does nothing without a project', async () => {
    const client = makeClient()
    renderHook(() => useReportJob(null), { wrapper: wrapper(client) })
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
    expect(api.getGenerateStatus).not.toHaveBeenCalled()
  })

  it('polls every 1.5 s while running, stops when the record is done, and invalidates the list and the document', async () => {
    const client = makeClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    api.getGenerateStatus
      .mockResolvedValueOnce(record({ progress: { done: 0, total: 4, current: 'target' } }))
      .mockResolvedValueOnce(record({ progress: { done: 2, total: 4, current: 'frontier' } }))
      .mockResolvedValue(record({
        status: 'done', version: 1, progress: { done: 4, total: 4, current: null }, finished_at: 1_790_000_060,
      }))
    const onFinished = vi.fn()
    const { result } = renderHook(() => useReportJob('Demo', { onFinished }), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    expect(result.current.progressPct).toBe(0)
    expect(api.getGenerateStatus).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.progressPct).toBe(50))
    expect(spy).not.toHaveBeenCalledWith({ queryKey: ['reports', 'list', 'Demo'] })

    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.record?.status).toBe('done'))
    expect(result.current.isRunning).toBe(false)
    expect(result.current.progressPct).toBe(100)
    expect(spy).toHaveBeenCalledWith({ queryKey: ['reports', 'list', 'Demo'] })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['reports', 'doc', 'Demo', ID] })
    // The staleness badge compares against the live evidence hash; a finished
    // job may have been written from evidence that changed since the panel read it.
    expect(spy).toHaveBeenCalledWith({ queryKey: REPORT_EVIDENCE_HASH_KEY('Demo') })
    expect(REPORT_EVIDENCE_HASH_KEY('Demo')).toEqual(['reports', 'evidence_hash', 'Demo'])
    expect(onFinished).toHaveBeenCalledTimes(1)
    expect(onFinished.mock.calls[0][0].status).toBe('done')

    // Polling stopped: no further status reads.
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(api.getGenerateStatus).toHaveBeenCalledTimes(3)
    expect(onFinished).toHaveBeenCalledTimes(1)
  })

  it('invalidates on failed and aborted too, but not on a record that was already terminal at first read', async () => {
    const client = makeClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    api.getGenerateStatus.mockResolvedValue(record({ status: 'failed', error: 'boom' }))
    const onFinished = vi.fn()
    const { result } = renderHook(() => useReportJob('Demo', { onFinished }), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.record?.status).toBe('failed'))
    expect(spy).not.toHaveBeenCalledWith({ queryKey: ['reports', 'list', 'Demo'] })
    expect(onFinished).not.toHaveBeenCalled()

    // A fresh run that then aborts.
    api.getGenerateStatus
      .mockReset()
      .mockResolvedValueOnce(record())
      .mockResolvedValue(record({ status: 'aborted', version: 1 }))
    await act(async () => { await result.current.start({ title: 'X' }) })
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    await waitFor(() => expect(result.current.record?.status).toBe('aborted'))
    expect(spy).toHaveBeenCalledWith({ queryKey: ['reports', 'list', 'Demo'] })
    expect(onFinished).toHaveBeenCalledTimes(1)
  })

  it('start posts the options, then re-reads the status so polling begins', async () => {
    const client = makeClient()
    api.getGenerateStatus.mockResolvedValueOnce(null).mockResolvedValue(record())
    const { result } = renderHook(() => useReportJob('Demo'), { wrapper: wrapper(client) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(1))
    let out: unknown
    await act(async () => { out = await result.current.start({ title: 'Client', sections: ['cost'] }) })
    expect(out).toEqual({ status: 'running', report_id: ID })
    expect(api.generateReport).toHaveBeenCalledWith('Demo', { title: 'Client', sections: ['cost'] })
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    expect(client.getQueryData(REPORT_JOB_KEY('Demo'))).toEqual(record())
  })

  it('abort posts to the abort route and reports `aborting` until the record leaves running', async () => {
    const client = makeClient()
    api.getGenerateStatus
      .mockResolvedValueOnce(record())
      .mockResolvedValueOnce(record())
      .mockResolvedValue(record({ status: 'aborted' }))
    const { result } = renderHook(() => useReportJob('Demo'), { wrapper: wrapper(client) })
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    expect(result.current.aborting).toBe(false)
    await act(async () => { await result.current.abort() })
    expect(api.abortGenerate).toHaveBeenCalledWith('Demo')
    expect(result.current.aborting).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    await waitFor(() => expect(result.current.record?.status).toBe('aborted'))
    expect(result.current.aborting).toBe(false)
  })

  it('regenerate posts to the section route and starts polling', async () => {
    const client = makeClient()
    api.getGenerateStatus.mockResolvedValueOnce(null).mockResolvedValue(record({ mode: 'regenerate', section: 'cost' }))
    const { result } = renderHook(() => useReportJob('Demo'), { wrapper: wrapper(client) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(1))
    await act(async () => { await result.current.regenerate(ID, 'cost', { instruction: 'Shorter.' }) })
    expect(api.regenerateSection).toHaveBeenCalledWith('Demo', ID, 'cost', { instruction: 'Shorter.' })
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    expect(result.current.record?.section).toBe('cost')
  })

  it('proposeMapping posts to the plan route, polls the mapping job and invalidates the template state when it finishes (WP11)', async () => {
    const client = makeClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    api.getGenerateStatus
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(record({ mode: 'mapping', progress: { done: 0, total: 1, current: null } }))
      .mockResolvedValue(record({ mode: 'mapping', status: 'done', progress: { done: 1, total: 1, current: null } }))
    const onFinished = vi.fn()
    const { result } = renderHook(() => useReportJob('Demo', { onFinished }), { wrapper: wrapper(client) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(1))
    let out: unknown
    await act(async () => { out = await result.current.proposeMapping(ID, { language: 'de' }) })
    expect(out).toEqual({ status: 'running', report_id: ID })
    expect(api.proposeMappingPlan).toHaveBeenCalledWith('Demo', ID, { language: 'de' })
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    expect(result.current.record?.mode).toBe('mapping')
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    await waitFor(() => expect(result.current.record?.status).toBe('done'))
    expect(spy).toHaveBeenCalledWith({ queryKey: REPORT_TEMPLATE_KEY('Demo', ID) })
    expect(onFinished).toHaveBeenCalledTimes(1)
    expect(onFinished.mock.calls[0][0].mode).toBe('mapping')
  })

  it('start rejects with the backend error and leaves the record alone', async () => {
    const { ReportsError } = await vi.importActual<typeof import('../../api/reports')>('../../api/reports')
    const client = makeClient()
    api.generateReport.mockRejectedValue(new ReportsError(
      { error_kind: 'report_job_in_flight', message: 'A report is already being generated.' }, 409,
    ))
    const { result } = renderHook(() => useReportJob('Demo'), { wrapper: wrapper(client) })
    await waitFor(() => expect(api.getGenerateStatus).toHaveBeenCalledTimes(1))
    let err: unknown
    await act(async () => { err = await result.current.start({}).catch(e => e) })
    expect(err).toBeInstanceOf(ReportsError)
    expect(result.current.isRunning).toBe(false)
  })
})
