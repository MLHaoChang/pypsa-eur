// W4b: the readers beside the scope cards (Solver settings › Stage 2, the
// rolling and myopic warnings) follow the scope live, not a hydrated copy of
// the solver config.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useUIStore } from '../../store/uiStore'
import { flowScopeKey, useLoadFlowOn } from './useFlowScope'

const getFlowScope = vi.fn()
vi.mock('../../api/flow', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/flow')>()),
  getFlowScope: (...a: unknown[]) => getFlowScope(...a),
}))

const reg = (grid: string[]) => ({
  scope: { version: 1, groups: grid.length ? ['capacity', 'grid', 'handoff'] : ['capacity', 'handoff'],
    source: 'project', preset_id: 'x',
    options: { capacity: ['dispatch'], grid, control: [], reliability: [], commercial: [], handoff: ['reports'] } },
  cards: [], preset_id: 'x', preset_label: 'x', preset: { version: 1, groups: [], options: {} },
})

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('flips with the scope without a remount', async () => {
  getFlowScope.mockResolvedValue(reg([]))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  const { result } = renderHook(() => useLoadFlowOn(true), { wrapper })
  expect(result.current).toBe(true)                       // the fallback, until the scope is read
  await waitFor(() => expect(result.current).toBe(false))
  act(() => { qc.setQueryData(flowScopeKey('Demo'), reg(['load_flow'])) })
  await waitFor(() => expect(result.current).toBe(true))
})
