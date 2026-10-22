// The deep link into Time Series (`openTimeSeriesFor`, W3c's "Next: give it a
// time series ›"): it opens the component's tab, the field's slot, and that
// field's own editor when asked; a plain focus only selects the row.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { openTimeSeriesFor } from '../layout/flowNavigation'
import TimeSeriesManager from './TimeSeriesManager'

vi.mock('../api/network', () => ({
  networkApi: new Proxy({
    getLoads: async () => [{ name: 'plant', bus: 'site', p_set: 10 }],
    getGenerators: async () => [{ name: 'gt1', bus: 'site', carrier: 'gas', p_nom: 50 },
      { name: 'pv1', bus: 'site', carrier: 'solar', p_nom: 20 }],
    getLinks: async () => [{ name: 'hp1', bus0: 'site', bus1: 'heat', carrier: 'heat pump', p_nom: 5 }],
    getLoadProfiles: async () => ({}),
    getGeneratorProfiles: async () => ({ gt1: { category: 'conventional' }, pv1: { category: 'renewable' } }),
    getLinkProfiles: async () => ({}),
    getSnapshots: async () => ({ count: 0 }),
    getTimeseries: async () => ({ index: [], columns: [], data: [] }),
    uploadGeneratorProfile: async () => {
      throw { message: 'Request failed', response: { data: { detail: 'The file covers 100 rows; the model has 8760 snapshots' } } }
    },
  } as Record<string, unknown>, {
    get: (t, k: string) => t[k] ?? (async () => ({})),
  }),
}))
const stub = vi.hoisted(() => (id: string) => ({
  default: (p: { component: string; kind?: string; componentType?: string }) =>
    <div data-testid={id}>{p.component}:{p.kind ?? p.componentType ?? ''}</div>,
}))
vi.mock('../components/series/LoadSeriesEditor', () => stub('load-editor'))
vi.mock('../components/series/AvailabilityEditor', () => stub('availability-editor'))
vi.mock('../components/series/SeriesEditor', () => stub('series-editor'))
vi.mock('../components/series/DefaultsReview', () => stub('defaults-review'))

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><TimeSeriesManager /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Site A', timeSeriesFocus: null })
})
afterEach(() => cleanup())

describe('openTimeSeriesFor', () => {
  it('opens Time Series on the profiles section with the focus recorded', () => {
    openTimeSeriesFor('Generator', 'gt1', { field: 'marginal_cost', openEditor: true })
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('timeseries')
    expect(ui.activeSection).toBe('profiles')
    expect(ui.timeSeriesFocus).toEqual({ type: 'Generator', name: 'gt1', field: 'marginal_cost', openEditor: true })
  })

  it("opens a load's own editor", async () => {
    renderIt()
    act(() => openTimeSeriesFor('Load', 'plant', { field: 'p_set', openEditor: true }))
    expect((await screen.findByTestId('load-editor')).textContent).toBe('plant:')
    expect(useUIStore.getState().timeSeriesFocus).toBeNull()
  })

  it("opens a conventional generator's price editor for its marginal cost", async () => {
    renderIt()
    act(() => openTimeSeriesFor('Generator', 'gt1', { field: 'marginal_cost', openEditor: true }))
    expect((await screen.findByTestId('series-editor')).textContent).toBe('gt1:tou')
  })

  it("opens a link's availability editor for its availability", async () => {
    renderIt()
    act(() => openTimeSeriesFor('Link', 'hp1', { field: 'p_max_pu', openEditor: true }))
    expect((await screen.findByTestId('availability-editor')).textContent).toBe('hp1:links')
  })

  it('without openEditor, only selects the row', async () => {
    renderIt()
    act(() => openTimeSeriesFor('Load', 'plant'))
    await vi.waitFor(() => expect(useUIStore.getState().timeSeriesFocus).toBeNull())
    expect(screen.queryByTestId('load-editor')).toBeNull()
  })

  it("a row with an editor takes its measured file there; a renewable's row keeps its upload", async () => {
    renderIt()
    act(() => openTimeSeriesFor('Generator', 'gt1'))
    expect(await screen.findByTitle("Edit the availability series of 'gt1'")).toBeTruthy()
    expect(screen.queryByTitle(/Upload a measured .* file for 'gt1'/)).toBeNull()
    act(() => openTimeSeriesFor('Generator', 'pv1'))
    expect(await screen.findByTitle("Upload a measured availability file for 'pv1' (single-column file)")).toBeTruthy()
  })

  it("says under the row why its upload was refused, with the template as the fix", async () => {
    renderIt()
    act(() => openTimeSeriesFor('Generator', 'pv1'))
    const button = await screen.findByTitle("Upload a measured availability file for 'pv1' (single-column file)")
    const input = button.parentElement?.querySelector('input[type=file]') as HTMLInputElement
    await userEvent.upload(input, new File(['x'], 'pv1.csv'))
    const error = await screen.findByTestId('row-upload-error-pv1')
    expect(error.textContent).toContain('The file covers 100 rows; the model has 8760 snapshots')
    expect(error.textContent).toContain("Download the template (the model's hours, a column named pv1) ›")
  })
})
