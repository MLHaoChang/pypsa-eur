// W4b: Stage 2 shows Load flow's state read-only, from the live scope.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolverConfig } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { Stage2Panel } from './SolverSettings'

vi.mock('../api/network', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/network')>()),
  networkApi: { getBuses: vi.fn().mockResolvedValue([{ name: 'b1' }]) },
}))

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => cleanup())

function mount(loadFlowOn: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}>
    <Stage2Panel draft={{ run_ac_pf_after_lopf: false } as SolverConfig} patch={() => {}}
      loadFlowOn={loadFlowOn} />
  </QueryClientProvider>)
}

it('reads On from the scope, whatever the hydrated draft says', () => {
  mount(true)
  expect(screen.getByTestId('stage2-state').textContent)
    .toMatch(/^On: AC power flow runs after each solve\./)
  expect(screen.queryByText(/Run AC Power Flow on current solution/)).toBeNull()
})

it('reads Off with the pointer to the scope row', () => {
  mount(false)
  expect(screen.getByTestId('stage2-state').textContent)
    .toMatch(/^Off:.*What the Run computes › Load flow, above\./)
})
