// Template-e2e 22 (IMPROVE-MANUAL-1): an Improve card's VOLL or goal change
// arrives in Solver settings filled in and unsaved. The block says "Not saved
// yet" until Save or Revert; leaving without saving says so (UX 2 S1 cond. 2);
// nothing is saved on arrival (GK B3 E1); another project's never applies (E3).
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { simulationApi } from '../api/simulation'
import { useUIStore } from '../store/uiStore'
import { useImprovePrefillStore } from './hubDesign/improvePrefill'
import SolverSettings from './SolverSettings'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('../api/simulation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/simulation')>()),
  simulationApi: {
    getSolverConfig: vi.fn().mockResolvedValue({ solver_name: 'highs', solver_options: {},
      run_ac_pf_after_lopf: false, voll: 0 }),
    checkSolvers: vi.fn().mockResolvedValue({ highs: true }),
    updateSolverConfig: vi.fn().mockResolvedValue({}),
  },
}))

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo', solverBlockRequest: 'voll' })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks(); useImprovePrefillStore.setState({ prefill: null }) })

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><SolverSettings /></QueryClientProvider>)
}

const vollInput = () => document.querySelector('#solver-voll input') as HTMLInputElement

it('fills VOLL in, unsaved, and says so; Save sends it and clears the tag', async () => {
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Set VOLL', voll: 5000 })
  mount()
  await screen.findByTestId('solver-voll-unsaved')
  expect(vollInput().value).toBe('5000')
  expect(screen.getByTestId('solver-improve-unsaved').textContent).toMatch(/Not saved yet: the value of lost load/)
  expect(simulationApi.updateSolverConfig).not.toHaveBeenCalled()
  expect(useImprovePrefillStore.getState().prefill).toBeNull()
  fireEvent.click(screen.getByText('Save settings'))
  await waitFor(() => expect(simulationApi.updateSolverConfig).toHaveBeenCalledWith({ voll: 5000 }))
  await waitFor(() => expect(screen.queryByTestId('solver-voll-unsaved')).toBeNull())
})

it('leaving without saving says it was not saved, and saves nothing', async () => {
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Set VOLL', voll: 5000 })
  const { unmount } = mount()
  await screen.findByTestId('solver-voll-unsaved')
  unmount()
  expect(toast).toHaveBeenCalledWith(expect.stringMatching(/^Not saved: the change from the Improve card \(Set VOLL\)/), expect.anything())
  expect(simulationApi.updateSolverConfig).not.toHaveBeenCalled()
})

it('Revert drops the filled-in value and the tag', async () => {
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Set VOLL', voll: 5000 })
  const { unmount } = mount()
  await screen.findByTestId('solver-voll-unsaved')
  fireEvent.click(screen.getByText('Revert'))
  await waitFor(() => expect(screen.queryByTestId('solver-voll-unsaved')).toBeNull())
  expect(vollInput().value).toBe('0')
  unmount()
  expect(toast).not.toHaveBeenCalled()
})

it('a goal change fills the goal editor and tags the goal block', async () => {
  useUIStore.setState({ solverBlockRequest: 'goal' })
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'More draws', goal: { mc_draws: '1000' } })
  mount()
  await screen.findByTestId('solver-goal-unsaved')
  expect((screen.getByTestId('solver-goal-draws') as HTMLInputElement).value).toBe('1000')
  fireEvent.click(screen.getByText('Save settings'))
  await waitFor(() => expect(simulationApi.updateSolverConfig).toHaveBeenCalledWith(expect.objectContaining({ mc_draws: 1000 })))
})

it('another project’s prefill never applies', async () => {
  useImprovePrefillStore.getState().set({ project: 'Other', title: 'Set VOLL', voll: 5000 })
  mount()
  await waitFor(() => expect(vollInput()).toBeTruthy())
  await new Promise(r => setTimeout(r, 50))
  expect(vollInput().value).toBe('0')
  expect(screen.queryByTestId('solver-voll-unsaved')).toBeNull()
  expect(useImprovePrefillStore.getState().prefill).toBeNull()
})

// GK B3 E5: a viewer can open Expert from the card; its save is refused in
// the gate's own plain words, never a raw code.
it('a viewer’s save of the filled-in value is refused in plain words', async () => {
  const MESSAGE = 'You can view this project but not change it. Ask its owner for edit access.'
  vi.mocked(simulationApi.updateSolverConfig).mockRejectedValueOnce(
    { response: { status: 403, data: { detail: { error_kind: 'project_view_only', message: MESSAGE } } } })
  useImprovePrefillStore.getState().set({ project: 'Demo', title: 'Set VOLL', voll: 5000 })
  mount()
  await screen.findByTestId('solver-voll-unsaved')
  fireEvent.click(screen.getByText('Save settings'))
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith(MESSAGE))
  expect(screen.getByTestId('solver-voll-unsaved')).toBeTruthy()
})
