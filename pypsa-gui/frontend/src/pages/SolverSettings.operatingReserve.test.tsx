// control-strategy walkthrough 03: the operating reserve sits under Solver
// settings › Dispatch (not Network), a 'reserve' block request lands on it, and
// a refused share keeps Save disabled like the other blocks.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { simulationApi } from '../api/simulation'
import SolverSettings from './SolverSettings'
import { MIN_RENEWABLE_FRACTION_LABEL, RESERVE_LABELS } from './solver/OffGridConstraints'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('../api/simulation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/simulation')>()),
  simulationApi: {
    getSolverConfig: vi.fn().mockResolvedValue({
      solver_name: 'highs', solver_options: {}, run_ac_pf_after_lopf: false, min_renewable_fraction: null,
      reserve_share_load: 0.1, reserve_share_pv: null, reserve_share_wind: null,
    }),
    checkSolvers: vi.fn().mockResolvedValue({ highs: true }),
    updateSolverConfig: vi.fn().mockResolvedValue({}),
  },
}))

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  useUIStore.setState({ currentProject: 'Demo', solverBlockRequest: null })
})
afterEach(() => cleanup())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><SolverSettings /></QueryClientProvider>)
}

const inputUnder = (label: string) =>
  screen.getByText(label).closest('label')!.querySelector('input') as HTMLInputElement

it('shows the three reserve shares under Dispatch, with the stored value', async () => {
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: 'Dispatch' }))
  const block = await screen.findByTestId('operating-reserve')
  for (const label of Object.values(RESERVE_LABELS)) expect(block.textContent).toContain(label)
  expect(inputUnder(RESERVE_LABELS.reserve_share_load).value).toBe('10')
})

it('keeps them out of Network, where the minimum renewable fraction stays', async () => {
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: 'Network' }))
  expect(await screen.findByText(MIN_RENEWABLE_FRACTION_LABEL)).toBeTruthy()
  expect(screen.queryByTestId('operating-reserve')).toBeNull()
  for (const label of Object.values(RESERVE_LABELS)) expect(screen.queryByText(label)).toBeNull()
})

it("lands a 'reserve' request on the Dispatch tab, scrolls to the block and clears the request", async () => {
  useUIStore.setState({ solverBlockRequest: 'reserve' })
  renderPage()
  const block = await screen.findByTestId('operating-reserve')
  await waitFor(() => expect(useUIStore.getState().solverBlockRequest).toBeNull())
  expect(block.scrollIntoView).toHaveBeenCalled()
})

it('keeps Save disabled and sends nothing while a share is refused', async () => {
  renderPage()
  fireEvent.click(await screen.findByRole('button', { name: 'Dispatch' }))
  await screen.findByTestId('operating-reserve')
  fireEvent.change(inputUnder(RESERVE_LABELS.reserve_share_pv), { target: { value: '150' } })
  expect(screen.getByTestId('reserve-share-pv-error').textContent).toBe('Reserve for solar output: 150 % is outside 0 to 100.')
  const save = screen.getByText('Save settings').closest('button') as HTMLButtonElement
  expect(save.disabled).toBe(true)
  fireEvent.click(save)
  await new Promise(r => setTimeout(r, 50))
  expect(simulationApi.updateSolverConfig).not.toHaveBeenCalled()
})
