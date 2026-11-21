// UI-COHESION-1 B2: each Results tab's empty case is one EmptyState: a status region with a
// title that names the state, the reason as visible text, and at most one action.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'

const api = vi.hoisted(() => ({ campusState: vi.fn(), gsStatus: vi.fn(), risk: vi.fn() }))
vi.mock('../../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../../api/campusElectrical')>('../../api/campusElectrical')
  return { ...real, campusApi: { ...real.campusApi, state: api.campusState } }
})
vi.mock('../../api/gridspine', async () => {
  const real = await vi.importActual<typeof import('../../api/gridspine')>('../../api/gridspine')
  return { ...real, gridspineApi: { ...real.gridspineApi, status: api.gsStatus } }
})
vi.mock('../../api/simulation', async () => {
  const real = await vi.importActual<typeof import('../../api/simulation')>('../../api/simulation')
  return { ...real, resultsApi: { ...real.resultsApi, getGridRiskProduction: api.risk } }
})
vi.mock('../../components/flowScope/useFlowScope', () => ({
  useFlowScope: () => ({ scope: { version: 1, groups: ['grid'], options: {}, source: 'project', preset_id: 'p' }, isSuccess: true }),
}))

import { GridCodeTab, ElectricalDesignTab } from './GridStudyTabs'
import GridRiskTab from './GridRiskTab'

const notStudy = () => Object.assign(new Error('x'), { response: { status: 409 } })
const mount = (el: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{el}</QueryClientProvider>)

async function expectEmptyState(testId: string, title: string, buttons: number) {
  await waitFor(() => expect(screen.getByTestId(testId).getAttribute('role')).toBe('status'))
  const box = screen.getByTestId(testId)
  expect(box.getAttribute('role')).toBe('status')
  expect(box.querySelector('div')?.textContent).toBe(title)
  expect(box.getAttribute('title')).toBeNull()
  expect(box.querySelectorAll('button').length).toBe(buttons)
  expect(buttons).toBeLessThanOrEqual(1)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Hub A', activeSlidePanel: null, uiMode: 'expert', fieldDrawer: null } as never)
  Object.values(api).forEach(f => f.mockReset())
  api.gsStatus.mockRejectedValue(notStudy())
})
afterEach(cleanup)

describe('Results tabs: empty states', () => {
  it('Grid code: no project', async () => {
    useUIStore.setState({ currentProject: null } as never)
    mount(<GridCodeTab />)
    await expectEmptyState('grid-tab-state', 'No project open', 0)
  })
  it('Grid code: the grid study has not run', async () => {
    api.campusState.mockResolvedValue({ results: null, run_in_progress: false })
    mount(<GridCodeTab />)
    await expectEmptyState('campus-tab-state', 'Not run yet', 1)
  })
  it('Electrical design: a planning → dynamics study has no campus design', async () => {
    api.gsStatus.mockResolvedValue({ status: 'completed' })
    mount(<ElectricalDesignTab />)
    await expectEmptyState('grid-tab-state', 'Not available for this project', 0)
  })
  it('Grid risk: not run', async () => {
    api.risk.mockResolvedValue({ available: false })
    mount(<GridRiskTab />)
    await expectEmptyState('grid-risk-empty', 'Not run yet', 0)
  })
  it('Grid risk: no process unit', async () => {
    api.risk.mockResolvedValue({ available: false, omitted: 'no_process_unit' })
    mount(<GridRiskTab />)
    await expectEmptyState('grid-risk-empty', 'Not applicable', 0)
  })
})
