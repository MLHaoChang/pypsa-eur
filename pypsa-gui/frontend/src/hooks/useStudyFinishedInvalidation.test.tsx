// Bug 2 follow-up (guided-mode spec §2.2): a study's closing re-solve (FMEA
// sweep, frontier, loops) leaves fresh dispatch on the live network, but the
// dock greeting kept its cached `simulationStatus` and still said "Not solved
// yet.". Every panel that already polls a study's status now invalidates that
// query when the study leaves `running` — one shared hook.
import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { useStudyFinishedInvalidation } from './useStudyFinishedInvalidation'

function setup(initial: string | null | undefined) {
  useUIStore.setState({ currentProject: 'Demo' })
  const qc = new QueryClient()
  const spy = vi.spyOn(qc, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  const hook = renderHook(({ s }) => useStudyFinishedInvalidation(s),
    { wrapper, initialProps: { s: initial } })
  const statusCalls = () => spy.mock.calls.filter(([f]) =>
    JSON.stringify(f?.queryKey) === JSON.stringify(nk('Demo', 'simulationStatus'))).length
  return { hook, statusCalls }
}

describe('useStudyFinishedInvalidation', () => {
  it.each(['done', 'failed', 'aborted', null])('running → %s invalidates simulationStatus once', (end) => {
    const { hook, statusCalls } = setup('running')
    hook.rerender({ s: 'running' })
    expect(statusCalls()).toBe(0)
    hook.rerender({ s: end })
    expect(statusCalls()).toBe(1)
    hook.rerender({ s: end })
    expect(statusCalls()).toBe(1)
  })

  it('a mount on an already finished study does nothing', () => {
    const { hook, statusCalls } = setup('done')
    hook.rerender({ s: 'done' })
    expect(statusCalls()).toBe(0)
  })

  it('an undefined poll between running and done (loading) does not fire early', () => {
    const { hook, statusCalls } = setup(undefined)
    hook.rerender({ s: 'running' })
    hook.rerender({ s: 'done' })
    expect(statusCalls()).toBe(1)
  })
})
