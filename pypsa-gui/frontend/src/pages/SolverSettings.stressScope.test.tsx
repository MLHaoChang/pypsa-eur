// W4a × W3b: the stress-conditions editor in Solver settings is Reliability ›
// Stress conditions & FMEA's. Out of scope it gives way to adding that
// sub-option in place; on a site that does not offer it, it says why.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowScopeRegistry } from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { StressConditionsSection } from './SolverSettings'

const getFlowScope = vi.fn()
vi.mock('../api/flow', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/flow')>()),
  getFlowScope: (...a: unknown[]) => getFlowScope(...a),
}))
vi.mock('./results/StressScenarioEditor', () => ({
  default: () => <div data-testid="stress-editor" />,
}))

function registry(groups: string[], stress: 'available' | 'not_yet_available', reason: string | null = null): FlowScopeRegistry {
  return {
    scope: { version: 1, groups: groups as never, source: 'project', preset_id: 'eh_site:industrial',
      options: { capacity: ['sizing', 'dispatch'], grid: [], control: [], reliability: ['goal_study'],
        commercial: [], handoff: ['reports'] } },
    cards: [{ id: 'reliability', label: 'Reliability', locked_reason: null, intended: ['goal_study'],
      sections: ['assets', 'scenarios'], chip: 'Reliability',
      options: [{ id: 'goal_study', label: 'Reliability goal & study', status: stress, reason },
        { id: 'stress', label: 'Stress conditions & FMEA', status: stress, reason }] }],
    preset_id: 'eh_site:industrial', preset_label: 'your own site', preset: { version: 1, groups: [], options: {} as never },
  }
}

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><StressConditionsSection project="Demo" /></QueryClientProvider>)
}

describe('Solver settings › Stress conditions (scope gate)', () => {
  it('shows the editor while Reliability › Stress is in scope', async () => {
    getFlowScope.mockResolvedValue({ ...registry(['capacity', 'reliability', 'handoff'], 'available'),
      scope: { ...registry([], 'available').scope, groups: ['capacity', 'reliability', 'handoff'],
        options: { ...registry([], 'available').scope.options, reliability: ['goal_study', 'stress'] } } })
    mount()
    expect(await screen.findByTestId('stress-editor')).toBeTruthy()
  })

  it('out of scope: the editor is hidden and "Add Stress conditions & FMEA" shows', async () => {
    getFlowScope.mockResolvedValue(registry(['capacity', 'reliability', 'handoff'], 'available'))
    mount()
    expect((await screen.findByTestId('solver-stress-scope-add')).textContent).toBe('Add Stress conditions & FMEA')
    expect(screen.queryByTestId('stress-editor')).toBeNull()
  })

  it('on a site that does not offer it: says why, no button', async () => {
    const why = 'Nothing to make adequate: this site only generates and has no load.'
    getFlowScope.mockResolvedValue(registry(['capacity', 'handoff'], 'not_yet_available', why))
    mount()
    expect((await screen.findByTestId('solver-stress-scope-why')).textContent).toBe(`Not offered for this site: ${why}`)
    expect(screen.queryByTestId('solver-stress-scope-add')).toBeNull()
    expect(screen.queryByTestId('stress-editor')).toBeNull()
  })
})
