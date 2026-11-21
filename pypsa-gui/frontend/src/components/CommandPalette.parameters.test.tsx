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
import { pickAssetFor } from '../catalogue/openParameter'
import { useGuideStore } from '../catalogue/guideStore'
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
afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ paletteMode: null })
  useGuideStore.setState({ view: null, history: [], opener: null })
})

describe('CommandPalette — Parameters', () => {
  it('lists the asset value under Parameters and Enter opens that field', async () => {
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'capacity' } })
    expect(await screen.findByText('PV roof: Capacity')).toBeTruthy()
    expect(screen.getByText('Parameters')).toBeTruthy()
    expect(screen.getByText('2.4 MW')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' })
    expect(useUIStore.getState().paletteMode).toBeNull()
  })

  it('a model-file-name hit never shows the raw name in the rendered rows or their labels (RAW-NAMES-1)', async () => {
    const { container } = renderPalette()
    fireEvent.change(screen.getByPlaceholderText(/Type a command/), { target: { value: 'p_nom' } })
    expect(await screen.findByText(/matches the model-file name/)).toBeTruthy()
    const body = container.ownerDocument.body
    const rows = Array.from(body.querySelectorAll('button')).filter(b => b.textContent?.includes('Capacity'))
    expect(rows.length).toBeGreaterThan(0)
    for (const r of rows) {
      expect(r.textContent).not.toMatch(/p_nom/)
      for (const a of Array.from(r.attributes)) expect(a.value).not.toMatch(/p_nom/)
    }
  })

  it('an entry with no asset in the project is a disabled row: Enter does nothing and the footer hides select', async () => {
    vi.mocked(networkApi.getGenerators).mockResolvedValue([])
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'capacity' } })
    const row = (await screen.findByText(/None in this project/)).closest('button')!
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(screen.queryByText('⏎ select')).toBeNull()
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.click(row)
    expect(useUIStore.getState().paletteMode).toBe('all')
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })

  it('starts on the first row that can run, not on a disabled "None in this project" row', async () => {
    vi.mocked(networkApi.getGenerators).mockResolvedValue([])
    vi.spyOn(networkApi, 'getStorageUnits').mockResolvedValue([{ name: 'Battery', p_nom: 1, bus: 'B1' }] as never)
    vi.mocked(catalogueApi.fetchCatalogue).mockResolvedValue({ ...CATALOGUE, entries: [
      ...CATALOGUE.entries,
      entry({ id: 'storageunit.p_nom', plain_name: 'Peak capacity', scope: 'StorageUnit', unit_display: 'MW' }),
    ] })
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'capacity' } })
    const none = (await screen.findByText(/None in this project/)).closest('button')!
    const battery = (await screen.findByText('Battery: Peak capacity')).closest('button')!
    expect(none.compareDocumentPosition(battery) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(battery.className).toContain('bg-accent/10')
    expect(none.className).not.toContain('bg-accent/10')
    expect(screen.getByText('⏎ select')).toBeTruthy()
    // A pointer resting on the disabled row does not take the selection either.
    fireEvent.mouseEnter(none)
    expect(battery.className).toContain('bg-accent/10')
    expect(none.className).not.toMatch(/bg-accent\/10|hover:bg-panel/)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useUIStore.getState().propertiesEditRequest).not.toBeNull()
  })

  it('opens on a seeded query when a jump asks the user to pick the asset', async () => {
    pickAssetFor(CATALOGUE.entries[0])
    renderPalette()
    expect((screen.getByPlaceholderText(/Type a command/) as HTMLInputElement).value).toBe('Capacity')
    expect(await screen.findByText('PV roof: Capacity')).toBeTruthy()
  })

  it('shows no Parameters group without a query', async () => {
    renderPalette()
    await screen.findByText('Switch to Guided mode')
    expect(screen.queryByText('Parameters')).toBeNull()
  })
})

describe('CommandPalette — Parameters in the guide', () => {
  it('Shift+Enter opens the row\'s entry in the guide instead, and the footer says both', async () => {
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'capacity' } })
    await screen.findByText('PV roof: Capacity')
    expect(screen.getByText('⏎ select')).toBeTruthy()
    expect(screen.getByText('Shift+⏎ guide')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'generator.p_nom' })
    // The row's field is where the guide came from: its "Open on" hides over that field.
    expect(useGuideStore.getState().originField).toBe('p_nom')
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
    expect(useUIStore.getState().paletteMode).toBeNull()
  })

  it('a "None in this project" row still opens in the guide on Shift+Enter', async () => {
    vi.mocked(networkApi.getGenerators).mockResolvedValue([])
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'capacity' } })
    await screen.findByText(/None in this project/)
    expect(screen.getByText('Shift+⏎ guide')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'generator.p_nom' })
  })

  it('an entry with no field is listed, says "Opens in the guide", and Enter opens it there', async () => {
    vi.mocked(catalogueApi.fetchCatalogue).mockResolvedValue({ ...CATALOGUE, entries: [
      entry({ id: 'globalconstraint.constant', plain_name: 'Emission limit', scope: 'GlobalConstraint' }),
    ] })
    renderPalette()
    const input = screen.getByPlaceholderText(/Type a command/)
    fireEvent.change(input, { target: { value: 'emission limit' } })
    const row = (await screen.findByText('Emission limit')).closest('button')!
    expect(row.textContent).toContain('Opens in the guide')
    expect(row.getAttribute('aria-disabled')).toBeNull()
    expect(screen.getByText('⏎ open guide')).toBeTruthy()
    expect(screen.queryByText('⏎ select')).toBeNull()
    expect(screen.queryByText('Shift+⏎ guide')).toBeNull()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useGuideStore.getState().view).toEqual({ kind: 'entry', id: 'globalconstraint.constant' })
    expect(useUIStore.getState().paletteMode).toBeNull()
  })
})
