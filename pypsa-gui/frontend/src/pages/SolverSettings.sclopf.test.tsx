// W4b: the N-1 panel shows the scope's state read-only; the branch selectors
// stay editable whether or not N-1 is on.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolverConfig } from '../api/types'
import { useUIStore } from '../store/uiStore'
import { SclopfPanel } from './SolverSettings'

vi.mock('../api/network', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/network')>()),
  networkApi: {
    getLines: vi.fn().mockResolvedValue([]),
    getTransformers: vi.fn().mockResolvedValue([]),
    getBuses: vi.fn().mockResolvedValue([]),
  },
}))

beforeEach(() => useUIStore.setState({ currentProject: 'Demo' }))
afterEach(() => cleanup())

function mount(n1On: boolean, patch = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}>
    <SclopfPanel draft={{ sclopf: false, sclopf_include_all_lines: false } as SolverConfig}
      patch={patch} n1On={n1On} />
  </QueryClientProvider>)
  return patch
}

it('reads On from the scope whatever the hydrated draft says, with no Enable checkbox', () => {
  mount(true)
  expect(screen.getByTestId('sclopf-state').textContent).toMatch(/^On: N-1 security is applied/)
  expect(screen.queryByText('Enable SCLOPF')).toBeNull()
})

it('reads Off and points at the scope row; the selectors stay clickable', () => {
  mount(false)
  expect(screen.getByTestId('sclopf-state').textContent)
    .toMatch(/^Off:.*What the Run computes › N-1 security, above\./)
  const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
  expect(boxes.length).toBeGreaterThan(0)
  expect(boxes.every(b => !b.disabled)).toBe(true)
  expect(document.querySelector('.pointer-events-none')).toBeNull()
})
