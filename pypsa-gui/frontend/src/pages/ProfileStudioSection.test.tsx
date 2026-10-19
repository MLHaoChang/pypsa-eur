// The Profile Studio (profile_gen Task 9) held to the backend's contract:
// - without a project it shows an empty state, never fires a request;
// - presets load per technology into the select;
// - a point line that is not "name, lon, lat" refuses inline, nothing posts;
// - Preview estimate posts the spec (forced to the synthetic source) and
//   draws one sparkline row per point;
// - Generate queues a profile_gen job and says so;
// - pending artifacts render as cards; Apply posts job_id + component and
//   reports the summary, a bus-moved warning included; refusals show inline.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ProfileStudioSection, { parsePoints } from './ProfileStudioSection'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }),
}))

const api = vi.hoisted(() => ({
  presets: vi.fn(), estimate: vi.fn(), generate: vi.fn(), pending: vi.fn(), apply: vi.fn(),
}))
vi.mock('../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../api/profiles')>('../api/profiles')
  return { ...real, profilesApi: api }
})

const PRESETS = [
  { name: 'default', technology: 'wind', description: 'generic wind', params: {} },
  { name: 'vestas-v90-3mw', technology: 'wind', description: 'V90', params: { turbine: { hub_height: 80 } } },
]
const PENDING_RECORD = {
  job_id: 'job-abc123', project_id: 'Hub A', technology: 'wind', source: 'synthetic',
  preset: 'default', weather_year: 2023, bbox: [7, 49, 9, 51],
  generated_at: '2026-10-18T00:00:00Z', points: ['g_wind'],
}
const refuse = (status: number, detail: unknown) => ({ response: { status, data: { detail } } })

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ProfileStudioSection /></QueryClientProvider>)
}

beforeEach(() => {
  store.currentProject = 'Hub A'
  vi.clearAllMocks()
  api.presets.mockResolvedValue(PRESETS)
  api.pending.mockResolvedValue({ pending: [], jobs: [], keep: 10 })
})
afterEach(() => cleanup())

describe('parsePoints', () => {
  it('parses "name, lon, lat" lines and skips blanks', () => {
    expect(parsePoints('g_wind, 8.0, 50.0\n\nsite2,-3.5, 55.2\n')).toEqual([
      { id: 'g_wind', lon: 8.0, lat: 50.0 },
      { id: 'site2', lon: -3.5, lat: 55.2 },
    ])
  })
  it('names the offending line', () => {
    expect(() => parsePoints('g_wind, 8.0\n')).toThrow(/line 1/)
  })
})

describe('profile studio: project gate', () => {
  it('shows an empty state without a project and fires nothing', () => {
    store.currentProject = null
    renderPanel()
    expect(screen.getByTestId('profile-studio-empty')).toBeTruthy()
    expect(api.presets).not.toHaveBeenCalled()
    expect(api.pending).not.toHaveBeenCalled()
  })
})

describe('profile studio: generate', () => {
  it('loads the technology presets into the select', async () => {
    renderPanel()
    await screen.findByTestId('profile-studio')
    await waitFor(() => expect(api.presets).toHaveBeenCalledWith('wind'))
    const options = Array.from((screen.getByTestId('preset-select') as HTMLSelectElement).options).map(o => o.value)
    expect(options).toEqual(['default', 'vestas-v90-3mw'])
  })

  it('refuses a malformed point line inline and posts nothing', async () => {
    renderPanel()
    await screen.findByTestId('profile-studio')
    await userEvent.type(screen.getByTestId('points-input'), 'g_wind, 8.0')
    await userEvent.click(screen.getByRole('button', { name: /Generate \(queued\)/ }))
    expect((await screen.findByTestId('studio-refusal')).textContent).toMatch(/line 1/)
    expect(api.generate).not.toHaveBeenCalled()
  })

  it('previews an estimate and draws one sparkline row per point', async () => {
    api.estimate.mockResolvedValue({
      estimate: true, source: 'synthetic', technology: 'wind', weather_year: 2023,
      timestamps: ['2023-01-01', '2023-01-02'],
      series: { g_wind: [0.4, 0.5], g_solar: [0.1, 0.2] },
    })
    renderPanel()
    await screen.findByTestId('profile-studio')
    await userEvent.type(screen.getByTestId('points-input'), 'g_wind, 8.0, 50.0\ng_solar, 8.1, 50.1')
    await userEvent.click(screen.getByRole('button', { name: /Preview estimate/ }))
    await screen.findByTestId('estimate-preview')
    const spec = api.estimate.mock.calls[0][0]
    expect(spec.source).toBe('synthetic')   // the estimate is always synthetic
    expect(spec.points).toHaveLength(2)
    expect(screen.getByTestId('estimate-preview').textContent).toContain('g_wind')
    expect(screen.getByTestId('estimate-preview').textContent).toContain('g_solar')
  })

  it('queues generation as a profile_gen job and says so', async () => {
    api.generate.mockResolvedValue({ id: 'job-abc123-def', status: 'queued', kind: 'profile_gen' })
    renderPanel()
    await screen.findByTestId('profile-studio')
    await userEvent.type(screen.getByTestId('points-input'), 'g_wind, 8.0, 50.0')
    await userEvent.click(screen.getByRole('button', { name: /Generate \(queued\)/ }))
    await waitFor(() => expect(api.generate).toHaveBeenCalled())
    const [project, spec] = api.generate.mock.calls[0]
    expect(project).toBe('Hub A')
    expect(spec).toMatchObject({ technology: 'wind', source: 'synthetic', preset: 'default' })
    expect(spec.points).toEqual([{ id: 'g_wind', lon: 8.0, lat: 50.0 }])
    expect((await screen.findByTestId('generate-notice')).textContent).toMatch(/queued as job job-abc1/)
  })

  it('shows the backend refusal inline', async () => {
    api.generate.mockRejectedValue(refuse(422, { error_kind: 'no_cds_credentials', message: 'CDS API key missing — see setup' }))
    renderPanel()
    await screen.findByTestId('profile-studio')
    await userEvent.type(screen.getByTestId('points-input'), 'g_wind, 8.0, 50.0')
    await userEvent.click(screen.getByRole('button', { name: /Generate \(queued\)/ }))
    expect((await screen.findByTestId('studio-refusal')).textContent).toMatch(/CDS API key missing/)
  })

  it('sends the chosen resolution in the estimate and generate specs', async () => {
    api.estimate.mockResolvedValue({
      estimate: true, source: 'synthetic', technology: 'wind', weather_year: 2023,
      resolution: 'daily', timestamps: ['2023-01-01'], series: { g_wind: [0.4] },
    })
    api.generate.mockResolvedValue({ id: 'job-daily-1', status: 'queued', kind: 'profile_gen' })
    renderPanel()
    await screen.findByTestId('profile-studio')
    await userEvent.click(screen.getByRole('button', { name: 'Daily' }))
    await userEvent.type(screen.getByTestId('points-input'), 'g_wind, 8.0, 50.0')
    await userEvent.click(screen.getByRole('button', { name: /Preview estimate/ }))
    await waitFor(() => expect(api.estimate).toHaveBeenCalled())
    expect(api.estimate.mock.calls[0][0].resolution).toBe('daily')
    await userEvent.click(screen.getByRole('button', { name: /Generate \(queued\)/ }))
    await waitFor(() => expect(api.generate).toHaveBeenCalled())
    expect(api.generate.mock.calls[0][1].resolution).toBe('daily')
  })
})

describe('profile studio: pending + apply', () => {
  it('renders pending artifacts as cards', async () => {
    api.pending.mockResolvedValue({ pending: [PENDING_RECORD], jobs: [], keep: 10 })
    renderPanel()
    const card = await screen.findByTestId('pending-job-abc123')
    expect(card.textContent).toContain('wind')
    expect(card.textContent).toContain('2023')
    expect(card.textContent).toContain('1 point')
  })

  it('applies onto the named component and reports the summary', async () => {
    api.pending.mockResolvedValue({ pending: [PENDING_RECORD], jobs: [], keep: 10 })
    api.apply.mockResolvedValue({
      component: 'g_wind', attribute: 'p_max_pu', hours: 8784, weather_year: 2023,
      leap_day: 'dropped', tiled: false, dispatch_cleared: true, mean: 0.32, peak: 0.91,
    })
    renderPanel()
    await screen.findByTestId('pending-job-abc123')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith('Hub A', 'job-abc123', 'g_wind'))
    expect((await screen.findByTestId('apply-ok')).textContent).toMatch(/applied to g_wind: 8784 h, mean 32\.0%/)
  })

  it('surfaces the bus-moved warning with the result', async () => {
    api.pending.mockResolvedValue({ pending: [PENDING_RECORD], jobs: [], keep: 10 })
    api.apply.mockResolvedValue({
      component: 'g_wind', attribute: 'p_max_pu', hours: 8760, weather_year: 2023,
      leap_day: 'n/a', tiled: false, dispatch_cleared: true, mean: 0.3, peak: 0.9,
      warning: 'bus moved 1.2° since generation — weather is for the old location',
    })
    renderPanel()
    await screen.findByTestId('pending-job-abc123')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect((await screen.findByTestId('apply-ok')).textContent).toMatch(/bus moved 1\.2°/)
  })

  it('shows an apply refusal inline', async () => {
    api.pending.mockResolvedValue({ pending: [PENDING_RECORD], jobs: [], keep: 10 })
    api.apply.mockRejectedValue(refuse(422, { error_kind: 'component_not_found', message: 'no generator named g_wind' }))
    renderPanel()
    await screen.findByTestId('pending-job-abc123')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect((await screen.findByTestId('apply-refusal')).textContent).toMatch(/no generator named g_wind/)
  })
})
