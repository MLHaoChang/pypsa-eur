// A refused save shows the server's own message, not a generic one (#778 rolling-window refusal).
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import SolverSettings from './SolverSettings'

const { REFUSAL } = vi.hoisted(() => ({ REFUSAL: 'Rolling-window runs cannot be combined with this setting.' }))


vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('../api/simulation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/simulation')>()),
  simulationApi: {
    getSolverConfig: vi.fn().mockResolvedValue({ solver_name: 'highs', solver_options: {}, run_ac_pf_after_lopf: false }),
    checkSolvers: vi.fn().mockResolvedValue({ highs: true }),
    updateSolverConfig: vi.fn().mockRejectedValue({ response: { status: 422, data: { detail: { message: REFUSAL } } } }),
  },
}))

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => cleanup())

it('toasts the backend 422 message when saving is refused', async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><SolverSettings /></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Solver' }))
  const select = (await screen.findAllByRole('combobox'))[0] as HTMLSelectElement
  const other = Array.from(select.options).find(o => o.value !== select.value)!.value
  fireEvent.change(select, { target: { value: other } })
  fireEvent.click(await screen.findByText('Save settings'))
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith(REFUSAL))
})
