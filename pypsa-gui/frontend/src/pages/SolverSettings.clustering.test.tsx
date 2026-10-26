// R1-single-run (UX-flow audit finding 4): clustering changes the network, so
// "Apply clustering" asks first, and once it is done the next step is the one
// gated Run (`requestRunSolve`), never a second run path.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { nk } from '../utils/queryKeys'
import { ClusteringSection } from './SolverSettings'

vi.mock('../api/network', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/network')>()),
  networkApi: {
    getBuses: vi.fn().mockResolvedValue([
      { name: 'b1', country: 'DE' }, { name: 'b2', country: 'DE' }, { name: 'b3', country: 'FR' },
    ]),
    applyClustering: vi.fn().mockResolvedValue({ bus_count: 2, line_count: 1, message: 'Clustered: 3 → 2 buses · 2 → 1 lines' }),
    undoInfo: vi.fn(),
  },
}))

const requestRunSolve = vi.fn()
beforeEach(() => {
  vi.mocked(networkApi.applyClustering).mockClear()
  vi.mocked(networkApi.undoInfo).mockResolvedValue({ depth: 1, unsaved: true, network_revision: 7 })
  requestRunSolve.mockClear()
  useUIStore.setState({ currentProject: 'Demo', requestRunSolve })
})
afterEach(() => cleanup())

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ClusteringSection /></QueryClientProvider>)
  return qc
}

async function clusterByZone() {
  const qc = mount()
  fireEvent.click(screen.getByDisplayValue('zone'))
  fireEvent.click(applyBtn())
  fireEvent.click(await screen.findByRole('button', { name: 'Apply clustering' }))
  await screen.findByTestId('clustering-done')
  return qc
}

// What the StatusBar's undoInfo poll would bring in next.
const nextUndoInfo = (qc: QueryClient, info: { depth: number; network_revision: number }) =>
  act(async () => {
    qc.setQueryData(nk('Demo', 'undoInfo'), { unsaved: true, ...info })
    await new Promise(r => setTimeout(r, 0))  // react-query notifies on a timer
  })

const applyBtn = () => screen.getByRole('button', { name: 'Apply clustering…' })

it('keeps Apply disabled in nodal mode, where clustering would change nothing', () => {
  mount()
  expect((applyBtn() as HTMLButtonElement).disabled).toBe(true)
})

it('asks before it changes the network, and Cancel changes nothing', async () => {
  mount()
  fireEvent.click(screen.getByDisplayValue('zone'))
  fireEvent.click(applyBtn())
  expect(await screen.findByRole('heading', { name: 'Apply clustering?' })).toBeTruthy()
  expect(await screen.findByText(/Cluster 3 buses into 2 by country\? This clears any solve results\./)).toBeTruthy()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }))
  expect(networkApi.applyClustering).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(networkApi.applyClustering).not.toHaveBeenCalled()
  expect(screen.queryByTestId('clustering-done')).toBeNull()
})

it('clusters once on Confirm, then offers the gated Run as the next step', async () => {
  mount()
  fireEvent.click(screen.getByDisplayValue('zone'))
  fireEvent.click(applyBtn())
  fireEvent.click(await screen.findByRole('button', { name: 'Apply clustering' }))
  await waitFor(() => expect(networkApi.applyClustering).toHaveBeenCalledOnce())
  expect(vi.mocked(networkApi.applyClustering).mock.calls[0][0]).toMatchObject({ mode: 'zone' })
  const done = await screen.findByTestId('clustering-done')
  expect(done.textContent).toMatch(/^Clustered to 2 buses · solve results cleared/)
  fireEvent.click(screen.getByRole('button', { name: 'Run the solve ›' }))
  expect(requestRunSolve).toHaveBeenCalledOnce()
})

it('says "Clustering undone." when an undo takes the network back', async () => {
  const qc = await clusterByZone()
  await nextUndoInfo(qc, { depth: 1, network_revision: 7 })  // a Save or a re-poll: nothing moved
  expect(screen.getByTestId('clustering-done')).toBeTruthy()
  await nextUndoInfo(qc, { depth: 0, network_revision: 8 })
  expect(screen.queryByTestId('clustering-done')).toBeNull()
  expect(screen.getByTestId('clustering-undone').textContent).toBe('Clustering undone.')
})

it('clears the done line on the next network edit', async () => {
  const qc = await clusterByZone()
  await nextUndoInfo(qc, { depth: 2, network_revision: 8 })
  expect(screen.queryByTestId('clustering-done')).toBeNull()
  expect(screen.queryByTestId('clustering-undone')).toBeNull()
})
