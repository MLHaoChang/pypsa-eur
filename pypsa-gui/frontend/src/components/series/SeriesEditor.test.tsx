// The other-series editor (TS-03) held to the backend's contract:
// - a Guided price posts a weekday peak and a catch-all off-peak period with
//   the stated money basis; a link's price says component_type "links";
// - Advanced prices post the period list as written;
// - an inflow posts its annual energy and shape, or twelve monthly shares,
//   and shows MW and the year's energy;
// - a COP posts its source and sink, the ground temperature for a ground
//   source, and the Carnot parameters from Advanced.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import SeriesEditor, { type SeriesKind } from './SeriesEditor'

const api = vi.hoisted(() => ({
  loadArchetypes: vi.fn(), generateLoad: vi.fn(), uploadLoad: vi.fn(), refine: vi.fn(),
  apply: vi.fn(), generateAvailability: vi.fn(), generateSeries: vi.fn(),
  current: vi.fn(), fromCurrent: vi.fn(),
}))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: api }
})

function renderEditor(kind: SeriesKind, mode: 'guided' | 'expert' = 'guided', componentType?: 'generators' | 'links') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SeriesEditor project="Site A" component="hp1" kind={kind} componentType={componentType} mode={mode} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.current.mockResolvedValue({ exists: false })
  api.generateSeries.mockResolvedValue({
    job_id: 'job-s', family: 'series', points: ['hp1'], unit: 'EUR/MWh',
    stats: { hp1: { min: 40, mean: 70, max: 120 } }, warnings: [],
  })
})
afterEach(() => cleanup())

describe('SeriesEditor', () => {
  it('posts a Guided peak and off-peak price with its money basis', async () => {
    renderEditor('tou', 'guided', 'links')
    await userEvent.type(screen.getByLabelText('Peak price'), '120')
    await userEvent.type(screen.getByLabelText('Off-peak price'), '40')
    await userEvent.clear(screen.getByLabelText('Currency year'))
    await userEvent.type(screen.getByLabelText('Currency year'), '2025')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateSeries).toHaveBeenCalledWith('Site A', {
      kind: 'tou', components: ['hp1'], params: {
        periods: [
          { name: 'peak', rate: 120, weekdays: [0, 1, 2, 3, 4], start_hour: 8, end_hour: 20 },
          { name: 'off-peak', rate: 40 },
        ],
        currency: 'EUR', price_basis: 'real', currency_year: 2025, component_type: 'links',
      },
    }))
    expect((await screen.findByTestId('stat-max')).textContent).toBe('120 EUR/MWh')
  })

  it('posts the Advanced period list as written', async () => {
    renderEditor('tou', 'expert')
    await userEvent.type(screen.getByLabelText('Period 1 price'), '150')
    await userEvent.type(screen.getByLabelText('Period 1 months'), '12,1,2')
    await userEvent.type(screen.getByLabelText('Period 2 price'), '50')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateSeries).toHaveBeenCalledWith('Site A', expect.objectContaining({
      params: expect.objectContaining({ periods: [
        { name: 'peak', rate: 150, months: [12, 1, 2], weekdays: [0, 1, 2, 3, 4], start_hour: 8, end_hour: 20 },
        { name: 'off-peak', rate: 50 },
      ] }),
    })))
  })

  it('posts an inflow with its shape and shows MW and the year', async () => {
    api.generateSeries.mockResolvedValue({ job_id: 'job-i', family: 'series', points: ['hp1'],
      stats: { hp1: { min: 20, mean: 57, max: 140, annual_mwh: 500000 } }, warnings: [] })
    renderEditor('inflow')
    await userEvent.type(screen.getByLabelText('Annual inflow MWh'), '500000')
    await userEvent.click(screen.getByRole('button', { name: 'Rain-fed' }))
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateSeries).toHaveBeenCalledWith('Site A', {
      kind: 'inflow', components: ['hp1'], params: { annual_mwh: 500000, shape: 'rain_fed' },
    }))
    expect((await screen.findByTestId('stat-max')).textContent).toBe('140 MW')
    expect(screen.getByTestId('stat-annual').textContent).toBe('500,000 MWh')
  })

  it('posts twelve monthly inflow shares from Advanced', async () => {
    renderEditor('inflow', 'expert')
    await userEvent.type(screen.getByLabelText('Annual inflow MWh'), '1000')
    await userEvent.click(screen.getByLabelText(/Monthly shares/))
    await userEvent.clear(screen.getByLabelText('May share'))
    await userEvent.type(screen.getByLabelText('May share'), '3')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateSeries).toHaveBeenCalledWith('Site A', expect.objectContaining({
      params: { annual_mwh: 1000, monthly: [1, 1, 1, 1, 3, 1, 1, 1, 1, 1, 1, 1] },
    })))
  })

  it('posts a ground-source COP with its Carnot share', async () => {
    renderEditor('cop', 'expert')
    await userEvent.click(screen.getByRole('button', { name: 'Ground' }))
    await userEvent.clear(screen.getByLabelText('Supply at °C'))
    await userEvent.type(screen.getByLabelText('Supply at °C'), '55')
    await userEvent.type(screen.getByLabelText('Carnot share'), '0.5')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateSeries).toHaveBeenCalledWith('Site A', {
      kind: 'cop', components: ['hp1'], params: { source: 'ground', sink_c: 55, source_c: 10, eta: 0.5 },
    }))
  })

  it('shows a refusal inline', async () => {
    api.generateSeries.mockRejectedValue({ response: { status: 422, data: { detail: {
      error_kind: 'cop_bus_not_electric', message: "bus0 'heat' carries heat, not electricity" } } } })
    renderEditor('cop')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    expect((await screen.findByTestId('editor-error')).textContent).toContain('not electricity')
  })
})
