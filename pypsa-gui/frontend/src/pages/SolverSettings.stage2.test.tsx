// Stage 2 holds the AC power flow inputs only; the run is the header Run (W10 R2).
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { SolverConfig } from '../api/types'

vi.mock('../api/network', () => ({ networkApi: { getBuses: vi.fn().mockResolvedValue([{ name: 'B1' }]) } }))

import { Stage2Panel } from './SolverSettings'

describe('Stage 2: AC Power Flow', () => {
  it('keeps the toggle, slack bus and tolerance, with no standalone run button', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}>
      <Stage2Panel draft={{ run_ac_pf_after_lopf: true } as unknown as SolverConfig} patch={vi.fn()} />
    </QueryClientProvider>)
    expect(screen.getByText(/Auto-run AC PF after optimisation/)).toBeTruthy()
    expect(screen.getByText(/Slack bus/)).toBeTruthy()
    expect(screen.getByText(/Tolerance/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Run AC Power Flow/i })).toBeNull()
    expect(screen.getByText(/AC power flow runs after every solve \(the header Run\)/)).toBeTruthy()
  })
})
