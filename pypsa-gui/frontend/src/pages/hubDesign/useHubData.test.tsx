// P24-FE gate re-run: `App.hubDesignAutoOpen` hung about 1 run in 20 in an
// endless synchronous re-render of HubDesignPanel (a CDP stack showed the
// loop entering through the review hook). The hooks returned `{ ...q, … }`:
// spreading a react-query result reads every tracked property, including
// `promise`, whose getter rejects the observer's pending thenable — for a
// disabled query the observer then keeps producing a new one, so each render
// schedules the next. The hooks now return named fields only; this pins it.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi, simulationApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { goal } from '../../test/fixtures/reliabilityGoal'
import {
  useHubReadiness, useHubReview, useHubSolverConfig, useHubStudy, useHubTemplate,
} from './useHubData'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: { ...actual.resultsApi, getEhStudy: none(), getEhTemplate: none(),
      getEhReview: none(), getEhReadiness: none() },
    simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn().mockResolvedValue({ voll: 1 }) },
  }
})

const getReliabilityGoal = vi.fn()
vi.mock('../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/flow')>()
  return { ...actual, getReliabilityGoal: (...a: unknown[]) => getReliabilityGoal(...a) }
})

afterEach(() => { cleanup(); vi.clearAllMocks() })

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('hub data hooks return named fields, never a spread query result', () => {
  it('no `promise` (or any other react-query field) leaks out', () => {
    useUIStore.setState({ currentProject: 'Demo' })
    const { result } = renderHook(() => ({
      study: useHubStudy(),
      template: useHubTemplate(),
      review: useHubReview(false),
      readiness: useHubReadiness(null, false, true),
    }), { wrapper: wrap })
    expect(Object.keys(result.current.study).sort()).toEqual(
      ['data', 'isError', 'isPending', 'refetch', 'running', 'study'])
    expect(Object.keys(result.current.template).sort()).toEqual(['isError', 'isPending', 'refetch', 'template'])
    expect(Object.keys(result.current.review).sort()).toEqual(['review'])
    // P30 (B8): `refetch` added for the panel's Retry (named, not spread).
    expect(Object.keys(result.current.readiness).sort()).toEqual(['isError', 'readiness', 'refetch'])
    expect(resultsApi.getEhReview).not.toHaveBeenCalled()
  })

  // P30 (B8): the panel observes readiness for its error line without a
  // request of its own.
  it('readiness with observeOnly sends no request', async () => {
    useUIStore.setState({ currentProject: 'Demo' })
    renderHook(() => useHubReadiness(null, false, true, { observeOnly: true }), { wrapper: wrap })
    await new Promise(r => setTimeout(r, 30))
    expect(resultsApi.getEhReadiness).not.toHaveBeenCalled()
  })

  it('the solver-config hook exposes only its data', () => {
    useUIStore.setState({ currentProject: 'Demo' })
    const { result } = renderHook(() => useHubSolverConfig(), { wrapper: wrap })
    expect(Object.keys(result.current)).toEqual(['data'])
    expect(simulationApi.getSolverConfig).toHaveBeenCalledTimes(1)
  })

  // W3b-R: the Guided study runs on the stored goal, so its readiness preview
  // carries the goal's LOLE and ENS (as Expert's EH panel does), not the
  // template's.
  it('readiness sends the stored goal\'s LOLE and ENS targets', async () => {
    useUIStore.setState({ currentProject: 'Demo' })
    getReliabilityGoal.mockResolvedValue(goal({
      target_lole_h: [1.5, 'stored'], ens_cap_permyriad: [2, 'stored'],
      site_archetype: ['off_grid', 'stored'],
    }))
    renderHook(() => useHubReadiness(null, false, true), { wrapper: wrap })
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalled())
    const [archetype, , , opts] = vi.mocked(resultsApi.getEhReadiness).mock.calls[0]
    expect(archetype).toBe('off_grid')
    expect(opts?.pack_overrides).toMatchObject({ target_lole_h: 1.5, ens_cap_permyriad: 2 })
  })
})
