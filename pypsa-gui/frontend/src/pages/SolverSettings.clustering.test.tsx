// R1-single-run (UX-flow audit finding 4): clustering changes the network, so
// "Apply clustering" asks first, and once it is done the next step is the one
// gated Run (`requestRunSolve`), never a second run path.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { ClusteringSection } from './SolverSettings'

vi.mock('../api/network', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/network')>()),
  networkApi: {
    getBuses: vi.fn().mockResolvedValue([
      { name: 'b1', country: 'DE' }, { name: 'b2', country: 'DE' }, { name: 'b3', country: 'FR' },
    ]),
    applyClustering: vi.fn().mockResolvedValue({ bus_count: 2, line_count: 1, message: 'Clustered: 3 → 2 buses · 2 → 1 lines' }),
  },
}))

const requestRunSolve = vi.fn()
beforeEach(() => {
  vi.mocked(networkApi.applyClustering).mockClear()
  requestRunSolve.mockClear()
  useUIStore.setState({ currentProject: 'Demo', requestRunSolve })
})
afterEach(() => cleanup())

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ClusteringSection /></QueryClientProvider>)
}

const applyBtn = () => screen.getByRole('button', { name: 'Apply clustering…' })

it('keeps Apply disabled in nodal mode, where clustering would change nothing', () => {
  mount()
  expect((applyBtn() as HTMLButtonElement).disabled).toBe(true)
})

it('asks before it changes the network, and Cancel changes nothing', async () => {
  mount()
  fireEvent.click(screen.getByDisplayValue('zone'))
  fireEvent.click(applyBtn())
  expect(await screen.findByRole('heading', { name: 'Cluster the network?' })).toBeTruthy()
  expect(networkApi.applyClustering).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(networkApi.applyClustering).not.toHaveBeenCalled()
  expect(screen.queryByTestId('clustering-done')).toBeNull()
})

it('clusters once on Confirm, then offers the gated Run as the next step', async () => {
  mount()
  fireEvent.click(screen.getByDisplayValue('zone'))
  fireEvent.click(applyBtn())
  fireEvent.click(await screen.findByRole('button', { name: 'Cluster' }))
  await waitFor(() => expect(networkApi.applyClustering).toHaveBeenCalledOnce())
  expect(vi.mocked(networkApi.applyClustering).mock.calls[0][0]).toMatchObject({ mode: 'zone' })
  const done = await screen.findByTestId('clustering-done')
  expect(done.textContent).toMatch(/Clustered: 3 → 2 buses/)
  fireEvent.click(screen.getByRole('button', { name: 'Run the solve' }))
  expect(requestRunSolve).toHaveBeenCalledOnce()
})
