// expert-catalogue §4.3: the palette's "Parameters" source. A typed query lists
// the matching entries on the project's assets; Enter opens that field.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import CommandPalette from './CommandPalette'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import * as catalogueApi from '../catalogue/api'
import { entry } from '../catalogue/testEntries'
import { fetchLocalSettings } from '../api/localSettings'
import { fetchLLMSettingsOrNull } from '../api/llmSettings'

vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn(),
}))
vi.mock('../api/llmSettings', async (orig) => ({
  ...(await orig<typeof import('../api/llmSettings')>()),
  fetchLLMSettingsOrNull: vi.fn(),
}))

const CATALOGUE: catalogueApi.Catalogue = {
  version: 'v', functions: [], asset_types: [],
  groups: [{ id: 'size_limits', plain_name: 'Size and limits', short_help: '', order: 0, guide: null }],
  entries: [entry({ id: 'generator.p_nom', plain_name: 'Capacity', unit_display: 'MW' })],
}

function renderPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useUIStore.setState({ paletteMode: 'all' })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CommandPalette /></MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(fetchLocalSettings).mockResolvedValue(null)
  vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
  vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CATALOGUE)
  vi.spyOn(networkApi, 'getGenerators').mockResolvedValue([{ name: 'PV roof', p_nom: 2.4, bus: 'B1', carrier: 'solar' }] as never)
  useUIStore.setState({ uiMode: 'expert', selectedComponent: null, propertiesEditRequest: null, activeSlidePanel: null })
})
afterEach(() => { vi.restoreAllMocks(); useUIStore.setState({ paletteMode: null }) })

describe('CommandPalette — Parameters', () => {
  it('lists the asset value under Parameters and Enter opens that field', async () => {
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'capacity' } })
    expect(await screen.findByText('PV roof › Capacity')).toBeTruthy()
    expect(screen.getByText('Parameters')).toBeTruthy()
    expect(screen.getByText('2.4 MW')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' })
    expect(useUIStore.getState().paletteMode).toBeNull()
  })

  it('shows no Parameters group without a query', async () => {
    renderPalette()
    await screen.findByText('Switch to Guided mode')
    expect(screen.queryByText('Parameters')).toBeNull()
  })
})
