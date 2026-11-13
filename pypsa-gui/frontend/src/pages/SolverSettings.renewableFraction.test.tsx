// homer-parity 01a (GK F5, R11): the page's Save gate. Typing 1 → 15 → 150
// patches 0.15 into the draft on the way; the refused 150 must keep Save
// disabled, or the passing-through 0.15 is sent.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { simulationApi } from '../api/simulation'
import SolverSettings from './SolverSettings'
import { MIN_RENEWABLE_FRACTION_LABEL } from './solver/OffGridConstraints'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('../api/simulation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/simulation')>()),
  simulationApi: {
    getSolverConfig: vi.fn().mockResolvedValue({
      solver_name: 'highs', solver_options: {}, run_ac_pf_after_lopf: false, min_renewable_fraction: null,
    }),
    checkSolvers: vi.fn().mockResolvedValue({ highs: true }),
    updateSolverConfig: vi.fn().mockResolvedValue({}),
  },
}))

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => cleanup())

it('keeps Save disabled and sends nothing while the fraction is refused', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><SolverSettings /></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Network' }))
  const label = await screen.findByText(MIN_RENEWABLE_FRACTION_LABEL)
  const input = label.closest('label')!.querySelector('input') as HTMLInputElement
  const saveButton = () => screen.getByText('Save settings').closest('button') as HTMLButtonElement
  for (const t of ['1', '15']) fireEvent.change(input, { target: { value: t } })
  expect(saveButton().disabled).toBe(false) // 0.15 is in the draft
  fireEvent.change(input, { target: { value: '150' } })
  expect(await screen.findByTestId('min-renewable-fraction-error')).toBeTruthy()
  expect(saveButton().disabled).toBe(true)
  fireEvent.click(saveButton())
  await new Promise(r => setTimeout(r, 50))
  expect(simulationApi.updateSolverConfig).not.toHaveBeenCalled()
})
