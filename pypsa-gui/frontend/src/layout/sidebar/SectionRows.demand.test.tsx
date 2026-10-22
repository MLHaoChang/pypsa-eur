// Demand belongs to Loads & demand (4), not to the Assets palette (5): "Add demand"
// opens the demand items under Loads, and Assets no longer holds them.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SectionRows } from './SectionRows'

const mount = (id: 'loads' | 'assets') => render(
  <QueryClientProvider client={new QueryClient()}><SectionRows id={id} /></QueryClientProvider>)
afterEach(cleanup)

describe('demand placement', () => {
  it('Loads & demand offers Add demand with the three demand items', () => {
    mount('loads')
    expect(screen.queryByText('Electrical Demand')).toBeNull()
    fireEvent.click(screen.getByText('Add demand'))
    for (const label of ['Electrical Demand', 'Hydrogen Demand', 'Heating Demand']) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    expect(screen.queryByText('Battery')).toBeNull()
  })

  it('the Assets palette no longer holds demand', () => {
    mount('assets')
    fireEvent.click(screen.getByText('Assets'))
    expect(screen.getByText('Battery')).toBeTruthy()
    expect(screen.queryByText('Electrical Demand')).toBeNull()
    expect(screen.queryByText('DEMAND')).toBeNull()
  })
})
