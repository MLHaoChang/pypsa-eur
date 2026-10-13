// The Generation Stack grid heads `capital_cost`, PyPSA's ANNUALISED
// investment cost, so its column says per year: `CC (€/MW/yr)` for Generators
// and Storage Units, `CC (€/MWh/yr)` for Stores. The S0 pre-fix gave every
// Properties-panel badge `/yr`; this grid was its follow-up (gate S0 [S2]).
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
  it('Generators: CC (€/MW/yr)', async () => {
    renderPage()
    expect(await screen.findByText('MC (€/MWh)')).toBeTruthy()
    expect(screen.getByText('CC (€/MW/yr)')).toBeTruthy()
    expect(screen.queryByText('CC (€/MW)')).toBeNull()
  })
  it('Storage Units: CC (€/MW/yr)', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Storage Units' }))
    expect(await screen.findByText('Max hours')).toBeTruthy()
    expect(screen.getByText('CC (€/MW/yr)')).toBeTruthy()
    expect(screen.queryByText('CC (€/MW)')).toBeNull()
  })
  it('Stores: CC (€/MWh/yr), while the marginal cost stays per MWh', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Stores' }))
    expect(await screen.findByText('e_nom (MWh)')).toBeTruthy()
    expect(screen.getByText('CC (€/MWh/yr)')).toBeTruthy()
    expect(screen.queryByText('CC (€/MWh)')).toBeNull()
    expect(screen.getByText('MC (€/MWh)')).toBeTruthy()
  })
})
