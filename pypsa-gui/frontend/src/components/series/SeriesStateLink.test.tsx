// A field with no editor yet (a renewable's profile) shows its series' state
// in plain words and one link to its row upload in Time Series, opened in
// place over the card in Guided and as the Time Series panel in Expert.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { SeriesStateLink } from './SeriesKit'

const api = vi.hoisted(() => ({ current: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: api }
})

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <SeriesStateLink project="Site A" componentType="generators" attribute="p_max_pu" component="pv1" />
  </QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ timeSeriesFocus: null, fieldDrawer: null, activeSlidePanel: null })
})
afterEach(() => cleanup())

describe('SeriesStateLink', () => {
  it('says the profile is missing and opens its row in place in Guided', async () => {
    api.current.mockResolvedValue({ exists: false })
    useUIStore.getState().setUiMode('guided')
    renderIt()
    expect((await screen.findByText('missing')).textContent).toBe('missing')
    await userEvent.click(screen.getByRole('button', { name: 'Upload in Time Series ›' }))
    const ui = useUIStore.getState()
    expect(ui.fieldDrawer).toEqual({ section: 'profiles', panel: 'timeseries', component: null,
      entity: null, field: 'p_max_pu' })
    expect(ui.timeSeriesFocus).toEqual({ type: 'Generator', name: 'pv1', field: 'p_max_pu' })
  })

  it('shows a measured upload in plain words and opens Time Series in Expert', async () => {
    api.current.mockResolvedValue({ exists: true, provenance: 'uploaded', rows: 8760 })
    useUIStore.getState().setUiMode('expert')
    renderIt()
    expect(await screen.findByText('measured, 8760 h')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Upload in Time Series ›' }))
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('timeseries')
    expect(ui.activeSection).toBe('profiles')
    expect(ui.timeSeriesFocus).toEqual({ type: 'Generator', name: 'pv1', field: 'p_max_pu' })
  })
})
