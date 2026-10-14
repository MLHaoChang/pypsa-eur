// The Generation Stack grid heads `capital_cost`, PyPSA's ANNUALISED
// investment cost, so its column says per year: €/MW/yr for Generators and
// Storage Units, €/MWh/yr for Stores (gate S0 [S2]). Since S1d the headers
// come from the asset schema: the descriptor's label with its STORED unit,
// because the grid shows and writes the column as stored.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  const empty = vi.fn(async () => [])
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getGenerators: empty, getStorageUnits: empty, getStores: empty,
    },
  }
})

import GenerationStack from './GenerationStack'

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <GenerationStack />
    </QueryClientProvider>,
  )
}

beforeEach(() => { useUIStore.setState({ currentProject: 'Demo' }) })
afterEach(() => cleanup())

describe('Generation Stack heads the annual capital cost per year', () => {
  it('Generators: per MW per year', async () => {
    renderPage()
    expect(await screen.findByText('Marginal cost (€/MWh)')).toBeTruthy()
    expect(screen.getByText('Capital cost (annualised) (€/MW/yr)')).toBeTruthy()
    expect(screen.queryByText(/Capital cost.*\(€\/MW\)$/)).toBeNull()
  })
  it('Storage Units: per MW of power per year', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Storage Units' }))
    expect(await screen.findByText('Duration (h)')).toBeTruthy()
    expect(screen.getByText('Capital cost (annualised) (€/MW/yr)')).toBeTruthy()
  })
  it('Stores: per MWh per year, while the marginal cost stays per MWh', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Stores' }))
    expect(await screen.findByText('Energy capacity (MWh)')).toBeTruthy()
    expect(screen.getByText('Capital cost (annualised) (€/MWh/yr)')).toBeTruthy()
    expect(screen.queryByText('Capital cost (annualised) (€/MW/yr)')).toBeNull()
    expect(screen.getByText('Marginal cost (€/MWh)')).toBeTruthy()
  })
})
