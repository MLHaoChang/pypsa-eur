// The one reliability goal (W3b-R): the hook reads GET …/flow/reliability_goal
// and a save PUTs ONLY the fields the user edited (gatekeeper condition).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { goal } from '../test/fixtures/reliabilityGoal'
import { goalPatchFromEdits, goalText, useReliabilityGoal } from './useReliabilityGoal'

const getReliabilityGoal = vi.fn()
vi.mock('../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/flow')>()
  return { ...actual, getReliabilityGoal: (...a: unknown[]) => getReliabilityGoal(...a) }
})
vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, updateSolverConfig: vi.fn() } }
})

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  getReliabilityGoal.mockReset().mockResolvedValue(goal({ target_lole_h: [3, 'stored'],
    ens_cap_permyriad: [2, 'template'], site_archetype: ['weak_flexible', 'template'] }))
  vi.mocked(simulationApi.updateSolverConfig).mockReset().mockResolvedValue({} as never)
})
afterEach(() => cleanup())

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  return renderHook(() => useReliabilityGoal(), { wrapper })
}

describe('goalPatchFromEdits', () => {
  it('carries only the edited fields', () => {
    expect(goalPatchFromEdits({})).toEqual({ patch: {}, error: null })
    expect(goalPatchFromEdits({ target_lole_h: '2.5' })).toEqual({ patch: { target_lole_h: 2.5 }, error: null })
    expect(goalPatchFromEdits({ mc_draws: '800' })).toEqual({ patch: { mc_draws: 800 }, error: null })
  })

  it('a cleared field is "not set" (null); an ENS target of 0 is off', () => {
    expect(goalPatchFromEdits({ target_lole_h: '' }).patch).toEqual({ target_lole_h: null })
    expect(goalPatchFromEdits({ ens_cap_permyriad: '0' }).patch).toEqual({ ens_cap_permyriad: null })
    expect(goalPatchFromEdits({ mc_draws: ' ' }).patch).toEqual({ mc_draws: null })
    // 0 h/yr is a real (strict) shortfall target, not "not set"
    expect(goalPatchFromEdits({ target_lole_h: '0' }).patch).toEqual({ target_lole_h: 0 })
  })

  it.each([
    [{ target_lole_h: '-1' }, /Allowed shortfall/],
    [{ target_lole_h: 'abc' }, /Allowed shortfall/],
    [{ ens_cap_permyriad: '-2' }, /ENS target/],
    [{ mc_draws: '2.5' }, /MC draws/],
    [{ mc_draws: '0' }, /MC draws/],
    [{ mc_draws: '2001' }, /MC draws/],
  ])('refuses %j with an error naming the field, and no patch', (edits, msg) => {
    const r = goalPatchFromEdits(edits)
    expect(r.patch).toEqual({})
    expect(r.error).toMatch(msg)
  })

  it('shows stored and template values, never a default', () => {
    const g = goal({ target_lole_h: [3, 'stored'], ens_cap_permyriad: [2, 'template'] })
    expect(goalText(g, 'target_lole_h')).toBe('3')
    expect(goalText(g, 'ens_cap_permyriad')).toBe('2')
    expect(goalText(g, 'mc_draws')).toBe('')
    expect(goalText(null, 'target_lole_h')).toBe('')
  })
})

describe('useReliabilityGoal', () => {
  it('reads the open project\'s goal: the stored site type and target', async () => {
    const { result } = mount()
    await waitFor(() => expect(result.current.goal).not.toBeNull())
    expect(getReliabilityGoal).toHaveBeenCalledWith('Demo')
    expect(result.current.archetype).toBe('weak_flexible')
    expect(result.current.target).toBe(3)
  })

  it('falls back to strong_grid and no target before the goal is read', () => {
    getReliabilityGoal.mockReturnValue(new Promise(() => {}))
    const { result } = mount()
    expect(result.current.isPending).toBe(true)
    expect(result.current.archetype).toBe('strong_grid')
    expect(result.current.target).toBeNull()
  })

  it('a save PUTs exactly the edited fields, then reads the goal again', async () => {
    const { result } = mount()
    await waitFor(() => expect(result.current.goal).not.toBeNull())
    const { patch } = goalPatchFromEdits({ mc_draws: '750' })
    await act(async () => { await result.current.save.mutateAsync(patch) })
    expect(simulationApi.updateSolverConfig).toHaveBeenCalledTimes(1)
    expect(vi.mocked(simulationApi.updateSolverConfig).mock.calls[0][0]).toEqual({ mc_draws: 750 })
    // nothing the user did not type rides along
    const sent = vi.mocked(simulationApi.updateSolverConfig).mock.calls[0][0]
    expect(Object.keys(sent)).toEqual(['mc_draws'])
    await waitFor(() => expect(getReliabilityGoal).toHaveBeenCalledTimes(2))
  })
})
