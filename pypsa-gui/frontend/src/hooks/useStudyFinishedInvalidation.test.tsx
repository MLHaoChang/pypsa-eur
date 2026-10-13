// Bug 2 follow-up (guided-mode spec §2.2): a study's closing re-solve (FMEA
// sweep, frontier, loops) leaves fresh dispatch on the live network, but the
// dock greeting kept its cached `simulationStatus` and still said "Not solved
// yet.". Every panel that already polls a study's status now invalidates that
// query when the study leaves `running` — one shared hook.
import { describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
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

// A5 (deferred spec 2026-09-28 §2.2; P22.9-FE note 7): `prev` survived a
// project switch, so project A's last `running` sample followed by project B's
// `done` read as a false running → done transition and invalidated B's status
// for a study B never ran. The previous sample now belongs to one project.
describe('useStudyFinishedInvalidation across a project switch (A5)', () => {
  function setupSwitch() {
    useUIStore.setState({ currentProject: 'A' })
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const wrapper = ({ children }: { children: ReactNode }) =>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    const hook = renderHook(({ s }: { s: string | null | undefined }) => useStudyFinishedInvalidation(s),
      { wrapper, initialProps: { s: 'running' as string | null | undefined } })
    return { hook, spy }
  }

  it('no invalidation when the project changes between samples', () => {
    const { hook, spy } = setupSwitch()
    // The switch and B's first sample land in one render: the status prop is
    // read from B's query key once `currentProject` is B.
    act(() => { useUIStore.setState({ currentProject: 'B' }); hook.rerender({ s: 'done' }) })
    hook.rerender({ s: 'done' })
    expect(spy).not.toHaveBeenCalled()
  })

  it('…and B\'s own running → done afterwards still invalidates B', () => {
    const { hook, spy } = setupSwitch()
    act(() => { useUIStore.setState({ currentProject: 'B' }); hook.rerender({ s: 'done' }) })
    hook.rerender({ s: 'running' })
    hook.rerender({ s: 'done' })
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith({ queryKey: nk('B', 'simulationStatus') })
  })
})
