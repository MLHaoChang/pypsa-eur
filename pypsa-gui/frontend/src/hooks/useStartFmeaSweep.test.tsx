// P24 (guided-mode spec §5.3, §5.9): FmeaTab's "run the B/C sweep" mutation,
// lifted so the hub-design Improve card starts the same sweep. Behaviour is
// FmeaTab's, unchanged: the project's stress-scenario registry is read first
// and an unreadable registry REFUSES the sweep (E2E review m4 — class C would
// be missing silently); the registry's scenarios are posted; a refused start
// toasts the backend's own sentence.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { resultsApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { useStartFmeaSweep } from './useStartFmeaSweep'

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getStressScenarios: vi.fn(),
      postFmeaSweep: vi.fn(),
    },
  }
})
vi.mock('react-hot-toast', () => {
  const t = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() })
  return { default: t }
})

const SCEN = [{ id: 'heatwave', kind: 'parametric', frequency_per_year: 0.3 }]

function setup(project: string | null, opts?: Parameters<typeof useStartFmeaSweep>[0]) {
  useUIStore.setState({ currentProject: project })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const inv = vi.spyOn(qc, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  const hook = renderHook(() => useStartFmeaSweep(opts), { wrapper })
  return { hook, inv }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(resultsApi.postFmeaSweep).mockResolvedValue({ status: 'running' } as never)
  vi.mocked(resultsApi.getStressScenarios).mockResolvedValue(
    { scenarios: SCEN, error: null } as never)
})

describe('useStartFmeaSweep', () => {
  it('reads the registry and posts its scenarios; onStarted runs', async () => {
    const onStarted = vi.fn()
    const { hook } = setup('Demo', { onStarted })
    act(() => hook.result.current.mutate())
    await waitFor(() => expect(onStarted).toHaveBeenCalledTimes(1))
    expect(resultsApi.getStressScenarios).toHaveBeenCalledWith('Demo')
    expect(resultsApi.postFmeaSweep).toHaveBeenCalledWith(SCEN)
  })

  it('without onStarted, the fmea_modes query is invalidated', async () => {
    const { hook, inv } = setup('Demo')
    act(() => hook.result.current.mutate())
    await waitFor(() => expect(inv).toHaveBeenCalledWith(
      { queryKey: nk('Demo', 'results', 'fmea_modes') }))
  })

  it('a registry error refuses the sweep', async () => {
    const toast = (await import('react-hot-toast')).default
    vi.mocked(resultsApi.getStressScenarios).mockResolvedValue(
      { scenarios: [], error: 'registry file is corrupt' } as never)
    const { hook } = setup('Demo')
    act(() => hook.result.current.mutate())
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Sweep failed to start: registry file is corrupt — sweep not started, ' +
      'class C would be missing'))
    expect(resultsApi.postFmeaSweep).not.toHaveBeenCalled()
  })

  it('an unreadable registry refuses the sweep', async () => {
    const toast = (await import('react-hot-toast')).default
    vi.mocked(resultsApi.getStressScenarios).mockRejectedValue(new Error('offline'))
    const { hook } = setup('Demo')
    act(() => hook.result.current.mutate())
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    const msg = String(vi.mocked(toast.error).mock.calls[0][0])
    expect(msg).toMatch(/^Sweep failed to start: could not read this project's stress-scenario registry/)
    expect(msg).toMatch(/sweep not started, class C would be missing$/)
    expect(resultsApi.postFmeaSweep).not.toHaveBeenCalled()
  })

  it('without a project the registry is not read and no scenarios are sent', async () => {
    const { hook } = setup(null)
    act(() => hook.result.current.mutate())
    await waitFor(() => expect(resultsApi.postFmeaSweep).toHaveBeenCalledWith([]))
    expect(resultsApi.getStressScenarios).not.toHaveBeenCalled()
  })

  it("a refused start toasts the backend's sentence", async () => {
    const toast = (await import('react-hot-toast')).default
    vi.mocked(resultsApi.postFmeaSweep).mockRejectedValue(Object.assign(
      new Error('Request failed with status code 409'),
      { response: { status: 409, data: { detail: 'an EH study is running' } } }))
    const { hook } = setup('Demo')
    act(() => hook.result.current.mutate())
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Sweep failed to start: an EH study is running'))
  })
})
