// LC-12: the panel asks what is due, holds it while the user is busy, sends
// each job's continuation once, and "Stop waiting" tells the server.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { CONTINUATION_MESSAGE, continuationMarker, usePlanContinuation } from './usePlanContinuation'
import { dismissContinuation, listContinuations } from '../api/chat'

vi.mock('../api/chat', () => ({ listContinuations: vi.fn(), dismissContinuation: vi.fn() }))

const item = { job_id: 'fg:0123abcd', status: 'completed', task_id: 't-1',
  message: 'job fg:0123abcd finished: completed (plan: Raise L1)' }

beforeEach(() => {
  vi.mocked(listContinuations).mockResolvedValue([item])
  vi.mocked(dismissContinuation).mockResolvedValue(true)
})
afterEach(() => vi.clearAllMocks())

it('sends a due continuation once when nothing holds it', async () => {
  const onContinue = vi.fn()
  const { result } = renderHook(() => usePlanContinuation({ sessionId: 's', watching: true, hold: false, onContinue }))
  await waitFor(() => expect(onContinue).toHaveBeenCalledWith(item))
  expect(result.current.handled.get(item.job_id)).toBe('continued')
  // The next check lists the same job again until the server sees the claim; it is not resent.
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
  expect(onContinue).toHaveBeenCalledTimes(1)
})

it('holds while the user is busy, and Continue now sends it', async () => {
  const onContinue = vi.fn()
  const { result, rerender } = renderHook(({ hold }) =>
    usePlanContinuation({ sessionId: 's', watching: true, hold, onContinue }), { initialProps: { hold: true } })
  await waitFor(() => expect(result.current.held).toBe(true))
  expect(onContinue).not.toHaveBeenCalled()
  act(() => result.current.continueNow())
  expect(onContinue).toHaveBeenCalledWith(item)
  rerender({ hold: false })
  expect(onContinue).toHaveBeenCalledTimes(1)
})

it('releases a held continuation when the user is done', async () => {
  const onContinue = vi.fn()
  const { result, rerender } = renderHook(({ hold }) =>
    usePlanContinuation({ sessionId: 's', watching: true, hold, onContinue }), { initialProps: { hold: true } })
  await waitFor(() => expect(result.current.held).toBe(true))
  rerender({ hold: false })
  await waitFor(() => expect(onContinue).toHaveBeenCalledTimes(1))
})

it('Stop waiting tells the server and never sends', async () => {
  vi.mocked(listContinuations).mockResolvedValue([])
  const onContinue = vi.fn()
  const { result } = renderHook(() => usePlanContinuation({ sessionId: 's', watching: true, hold: false, onContinue }))
  act(() => result.current.stopWaiting(item.job_id))
  expect(dismissContinuation).toHaveBeenCalledWith('s', item.job_id)
  expect(result.current.handled.get(item.job_id)).toBe('stopped')
  vi.mocked(listContinuations).mockResolvedValue([item])
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
  expect(onContinue).not.toHaveBeenCalled()
})

it('asks nothing without a session', () => {
  renderHook(() => usePlanContinuation({ sessionId: null, watching: true, hold: false, onContinue: vi.fn() }))
  expect(listContinuations).not.toHaveBeenCalled()
})

it('reads the server message back as plain words, never the job id', () => {
  const match = CONTINUATION_MESSAGE.exec(item.message)
  expect(match?.[2]).toBe('completed')
  expect(continuationMarker('completed')).toBe('The solve finished · continuing')
  expect(continuationMarker('failed')).toBe("The solve didn't finish · continuing")
  for (const status of ['completed', 'failed', 'aborted', 'unknown']) {
    expect(continuationMarker(status)).not.toMatch(/fg:|\b(completed|aborted|unknown)\b/)
  }
  expect(CONTINUATION_MESSAGE.test('job fg:01 finished: completed')).toBe(false)
})
