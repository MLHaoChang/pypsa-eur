import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { WeatherBar } from './WeatherBar'

const prof = vi.hoisted(() => ({ weather: vi.fn(), saveWeather: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, profilesApi: { ...real.profilesApi, ...prof } }
})
const nav = vi.hoisted(() => ({ openSiteConnection: vi.fn() }))
vi.mock('../../pages/economics/SiteConnectionLink', () => nav)

const answer = (o: object = {}) => ({ source: 'synthetic', year: 2023, year_source: 'snapshots',
  time_basis: { value: 'UTC', assumed: false }, ...o })

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><WeatherBar project="Site A" /></QueryClientProvider>)
}

beforeEach(() => { vi.clearAllMocks(); prof.weather.mockResolvedValue(answer()) })
afterEach(() => cleanup())

describe('WeatherBar', () => {
  it('names a sampled week and its UTC hours', async () => {
    prof.weather.mockResolvedValue(answer({ window: { first: '2025-07-01T00:00:00', last: '2025-07-07T23:00:00', hours: 168, runs: 1 } }))
    renderIt()
    expect((await screen.findByTestId('weather-window')).textContent).toContain('1–7 Jul · 168 h (UTC)')
  })
  it('names multiple sampled periods', async () => {
    prof.weather.mockResolvedValue(answer({ window: { first: '2025-01-01', last: '2025-07-07', hours: 336, runs: 2 } }))
    renderIt()
    expect((await screen.findByTestId('weather-window')).textContent).toContain('2 sampled periods')
  })
  it('does not add a window line for a full weather year', async () => {
    prof.weather.mockResolvedValue(answer({ window: null }))
    renderIt(); await screen.findByTestId('weather-time-basis')
    expect(screen.queryByTestId('weather-window')).toBeNull()
  })
  it('explains an unsupported window', async () => {
    prof.weather.mockResolvedValue(answer({ window: { unsupported: 'Snapshots must be hourly' } }))
    renderIt()
    expect((await screen.findByTestId('weather-window')).textContent).toContain('Profile window unavailable: Snapshots must be hourly')
  })
  it('shows where the year came from', async () => {
    renderIt()
    expect((await screen.findByTestId('weather-year-source')).textContent).toBe('from your snapshots')
    expect((screen.getByTestId('weather-year') as HTMLInputElement).value).toBe('2023')
  })

  it('says it is a default when the snapshots are not a completed year', async () => {
    prof.weather.mockResolvedValue(answer({ year: 2025, year_source: 'default' }))
    renderIt()
    expect((await screen.findByTestId('weather-year-source')).textContent).toContain('default')
  })

  it('saves a new year on Enter and a source on click', async () => {
    prof.saveWeather.mockImplementation(async (_p: string, c: object) => answer({ ...c, year_source: 'saved' }))
    renderIt()
    const year = await screen.findByTestId('weather-year')
    await userEvent.clear(year)
    await userEvent.type(year, '2019{Enter}')
    await waitFor(() => expect(prof.saveWeather).toHaveBeenCalledWith('Site A', { source: 'synthetic', year: 2019 }))
    expect((await screen.findByTestId('weather-year-source')).textContent).toBe('your choice')
    await userEvent.click(screen.getByRole('button', { name: 'ERA5' }))
    await waitFor(() => expect(prof.saveWeather).toHaveBeenLastCalledWith('Site A', { source: 'atlite', year: 2019 }))
  })

  it('shows the time basis read-only and links to where it is set', async () => {
    prof.weather.mockResolvedValue(answer({ time_basis: { value: 'UTC (assumed; site clock unknown)', assumed: true } }))
    renderIt()
    const line = await screen.findByTestId('weather-time-basis')
    expect(line.textContent).toContain('Snapshots are read as UTC (assumed; site clock unknown)')
    expect(line.querySelector('input')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Edit in Connection & grid/ }))
    expect(nav.openSiteConnection).toHaveBeenCalled()
  })

  it('shows a refused year in words', async () => {
    prof.saveWeather.mockRejectedValue({ response: { data: { detail: { message: 'the weather year is a completed year' } } } })
    renderIt()
    const year = await screen.findByTestId('weather-year')
    await userEvent.clear(year)
    await userEvent.type(year, '2099{Enter}')
    expect((await screen.findByTestId('weather-error')).textContent).toContain('completed year')
  })
})
