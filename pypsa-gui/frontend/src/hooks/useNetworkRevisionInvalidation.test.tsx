// P33b 10b: after a finished hub study nothing polls the study record or its
// review, so an edit left the Guided greeting and the Results card on "not
// edited" until a reload. The hook watches the already-polled
// `/network/undo/info` edit counter and invalidates the two hub queries when
// it moves — on the same project only, never on the first sample.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { useNetworkRevisionInvalidation } from './useNetworkRevisionInvalidation'

function info(rev: number | undefined) {
  return { depth: 0, unsaved: false, network_revision: rev }
}

let qc: QueryClient
let spy: ReturnType<typeof vi.spyOn>

function setup(enabled = true) {
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  spy = vi.spyOn(qc, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  return renderHook(({ on }) => useNetworkRevisionInvalidation(on),
    { wrapper, initialProps: { on: enabled } })
}

/** Drive the polled sample directly (the 3 s poll is not waited for). */
async function sample(project: string, rev: number | undefined) {
  await act(async () => {
    qc.setQueryData(nk(project, 'undoInfo'), info(rev))
    await new Promise(r => setTimeout(r, 5))
  })
}

const hubCalls = (project: string) => spy.mock.calls
  .map((c: unknown[]) => JSON.stringify((c[0] as { queryKey?: unknown } | undefined)?.queryKey))
  .filter((k: string) => k === JSON.stringify(nk(project, 'results', 'eh_study'))
    || k === JSON.stringify(nk(project, 'results', 'eh_review')))

beforeEach(() => {
  useUIStore.setState({ currentProject: 'A' })
  vi.spyOn(networkApi, 'undoInfo').mockResolvedValue(info(5) as never)
})
afterEach(() => { vi.restoreAllMocks() })

describe('useNetworkRevisionInvalidation (P33b)', () => {
  it('a revision change on the same project invalidates exactly the two hub keys', async () => {
    setup()
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    await sample('A', 5)
    expect(spy).not.toHaveBeenCalled()
    await sample('A', 6)
    expect(spy).toHaveBeenCalledTimes(2)
    expect(spy).toHaveBeenCalledWith({ queryKey: nk('A', 'results', 'eh_study') })
    expect(spy).toHaveBeenCalledWith({ queryKey: nk('A', 'results', 'eh_review') })
    await sample('A', 6)
    expect(spy).toHaveBeenCalledTimes(2)
  })

  it('the first sample is not a transition', async () => {
    setup()
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 20))
    expect(spy).not.toHaveBeenCalled()
  })

  it('an undefined sample is not a transition and does not reset the last one', async () => {
    setup()
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    await sample('A', 5)
    await sample('A', undefined)
    expect(spy).not.toHaveBeenCalled()
    await sample('A', 5)
    expect(spy).not.toHaveBeenCalled()
  })

  it('a project switch between samples is not a transition', async () => {
    const hook = setup()
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    await sample('A', 5)
    // Deterministic (P33b gate S-3): B's own fetch answers the same value the
    // test plants, and is awaited, so B's first sample is 9 whichever lands
    // first. It used to answer 5, and when that fetch landed before the
    // planted 9 the test saw a genuine 5 → 9 transition on B.
    vi.mocked(networkApi.undoInfo).mockResolvedValue(info(9) as never)
    await act(async () => { useUIStore.setState({ currentProject: 'B' }) })
    await waitFor(() => expect(
      (qc.getQueryData(nk('B', 'undoInfo')) as { network_revision?: number } | undefined)
        ?.network_revision).toBe(9))
    await sample('B', 9)
    hook.rerender({ on: true })
    expect(hubCalls('A')).toHaveLength(0)
    expect(hubCalls('B')).toHaveLength(0)
    await sample('B', 10)
    expect(hubCalls('B')).toHaveLength(2)
  })

  it('a backwards move (a Saved-snapshot restore moves the counter back) also invalidates', async () => {
    setup()
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    await sample('A', 7)
    expect(spy).not.toHaveBeenCalled()
    await sample('A', 5)
    expect(hubCalls('A')).toHaveLength(2)
  })

  it('enabled: false never invalidates (and never fetches); re-enabling with a moved revision invalidates once', async () => {
    const hook = setup(true)
    await waitFor(() => expect(networkApi.undoInfo).toHaveBeenCalled())
    await sample('A', 5)
    hook.rerender({ on: false })
    vi.mocked(networkApi.undoInfo).mockClear()
    // An edit while disabled (Expert): the server's counter is now 7, and the
    // other always-mounted pollers put that sample in the shared cache.
    vi.mocked(networkApi.undoInfo).mockResolvedValue(info(7) as never)
    await sample('A', 7)
    await new Promise(r => setTimeout(r, 20))
    expect(spy).not.toHaveBeenCalled()
    expect(networkApi.undoInfo).not.toHaveBeenCalled()
    hook.rerender({ on: true })
    await waitFor(() => expect(hubCalls('A')).toHaveLength(2))
    hook.rerender({ on: true })
    expect(hubCalls('A')).toHaveLength(2)
  })

  it('a hook mounted disabled reads nothing', async () => {
    setup(false)
    await new Promise(r => setTimeout(r, 20))
    expect(networkApi.undoInfo).not.toHaveBeenCalled()
    await sample('A', 3)
    await sample('A', 4)
    expect(spy).not.toHaveBeenCalled()
  })
})
