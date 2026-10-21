// The discount and inflation rates are edited under Economics (section 7);
// Solver settings shows them read-only, with a link home (design §2.2).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { SolverConfig } from '../api/types'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { EconomicAssumptions } from './SolverSettings'

const wrap = (ui: React.ReactElement) => render(
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const DRAFT = { discount_rate: 0.07, inflation_rate: 0.02, default_lifetime: 25, co2_price: 0 } as unknown as SolverConfig

afterEach(() => { cleanup(); useUIStore.setState({ activeSlidePanel: null }) })

describe('Solver settings: rates', () => {
  it('shows both rates as text with no input, and no patch can be sent', () => {
    const patch = vi.fn()
    wrap(<EconomicAssumptions draft={DRAFT} patch={patch} />)
    expect(screen.getByTestId('solver-rate-discount').textContent).toContain('7')
    expect(screen.getByTestId('solver-rate-inflation').textContent).toContain('2')
    expect(screen.queryByLabelText(/Discount rate/)).toBeNull()
    expect(patch).not.toHaveBeenCalled()
  })

  it('links home: Edit in Economics opens the Economics panel', () => {
    wrap(<EconomicAssumptions draft={DRAFT} patch={vi.fn()} />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Edit in Economics' })[0])
    expect(useUIStore.getState().activeSlidePanel).toBe('economics')
  })

  it('a missing rate reads "not set", never 0', () => {
    wrap(<EconomicAssumptions draft={{ ...DRAFT, discount_rate: undefined } as unknown as SolverConfig} patch={vi.fn()} />)
    expect(screen.getByTestId('solver-rate-discount').textContent).toContain('not set')
  })
})
