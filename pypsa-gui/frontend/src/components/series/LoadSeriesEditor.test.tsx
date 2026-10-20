// The load series editor (TS-01, TS-04) held to the backend's contract:
// - Guided shows the key parameters only; Advanced holds the archetype's own
//   parameters and the refinement ops (open by default in Expert);
// - Preview posts the archetype, the load and the stated scale, and shows
//   peak, the source-year energy and what the model's snapshots count;
// - Apply posts the job and the component, and says undo reverts it;
// - the measured upload posts the file with its unit;
// - refinement posts the ordered ops and replaces the pending result;
// - refusals show inline.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import LoadSeriesEditor from './LoadSeriesEditor'

const api = vi.hoisted(() => ({
  loadArchetypes: vi.fn(), generateLoad: vi.fn(), uploadLoad: vi.fn(), refine: vi.fn(),
  apply: vi.fn(), generateAvailability: vi.fn(), generateSeries: vi.fn(),
  current: vi.fn(), fromCurrent: vi.fn(),
}))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: api }
})

const CATALOGUE = {
  archetypes: [
    { name: 'industrial', params: { peak_mw: null, annual_mwh: null, shifts: 2, base_share: 0.25, holidays: [] } },
    { name: 'commercial', params: { peak_mw: null, annual_mwh: null, kind: 'office' } },
  ],
  presets: [{ name: 'industrial_three_shift', archetype: 'industrial', params: { shifts: 3 } }],
}
const PENDING = {
  job_id: 'job-1', family: 'load', points: ['plant'], provenance: 'synthetic/assumed',
  stats: { plant: { peak_mw: 80, annual_mwh: 400000, modelled_mwh: { total: 399000 } } },
  warnings: ['time_basis_assumed_utc: applied unshifted'],
}

function renderEditor(mode: 'guided' | 'expert' = 'guided', initialTab?: 'archetype' | 'upload') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LoadSeriesEditor project="Site A" component="plant" mode={mode} initialTab={initialTab} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.current.mockResolvedValue({ exists: false })
  api.loadArchetypes.mockResolvedValue(CATALOGUE)
})
afterEach(() => cleanup())

describe('LoadSeriesEditor', () => {
  it('shows only the key parameters in Guided', async () => {
    renderEditor('guided')
    await screen.findByRole('option', { name: 'industrial' })
    expect(screen.queryByTestId('archetype-params')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    expect(screen.getByLabelText('shifts')).toBeTruthy()
  })

  it('opens the archetype parameters in Expert', async () => {
    renderEditor('expert')
    await screen.findByRole('option', { name: 'industrial' })
    expect(screen.getByTestId('archetype-params')).toBeTruthy()
  })

  it('previews an archetype at the stated peak and shows the energy the model counts', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    renderEditor('expert')
    await screen.findByRole('option', { name: 'industrial' })
    await userEvent.type(screen.getByLabelText('Peak MW'), '80')
    await userEvent.type(screen.getByLabelText('shifts'), '3')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'], params: { peak_mw: 80, shifts: 3 },
    }))
    expect((await screen.findByTestId('stat-peak')).textContent).toBe('80 MW')
    expect(screen.getByTestId('stat-modelled').textContent).toBe('399,000 MWh')
    expect(screen.getByTestId('pending-warnings').textContent).toContain('time_basis_assumed_utc')
  })

  it('sends a preset with its archetype', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    renderEditor()
    await screen.findByRole('option', { name: 'industrial three shift' })
    await userEvent.selectOptions(screen.getByLabelText('Archetype'), 'industrial_three_shift')
    await userEvent.click(screen.getByRole('button', { name: 'Annual MWh' }))
    await userEvent.type(screen.getByLabelText('Annual MWh'), '5000')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'],
      params: { annual_mwh: 5000, preset: 'industrial_three_shift' },
    }))
  })

  it('applies the pending series and says undo reverts it', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    api.apply.mockResolvedValue({ component: 'plant', component_type: 'loads', attribute: 'p_set',
      peak: 80, mean: 40, min: 20, warnings: [] })
    renderEditor()
    await screen.findByRole('option', { name: 'industrial' })
    await userEvent.type(screen.getByLabelText('Peak MW'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Apply to plant/ }))
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith('Site A', { job_id: 'job-1', component: 'plant' }))
    expect((await screen.findByTestId('applied')).textContent).toContain('Undo reverts it')
  })

  it('holds Apply when the preview says this network cannot take the series', async () => {
    api.generateLoad.mockResolvedValue({ ...PENDING,
      stats: { plant: { peak_mw: 80, annual_mwh: 400000, modelled_mwh: null } },
      warnings: ['not_applicable: network snapshots: snapshots must span one full calendar year'] })
    renderEditor()
    await screen.findByRole('option', { name: 'industrial' })
    await userEvent.type(screen.getByRole('textbox', { name: 'Peak MW' }), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    const btn = await screen.findByRole('button', { name: /Apply to plant/ })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('stat-modelled').textContent).toBe('—')
  })

  it('shows the preview week with its dates and a text alternative', async () => {
    const snaps = Array.from({ length: 168 }, (_, i) =>
      new Date(Date.UTC(2030, 0, 14, i)).toISOString().slice(0, 19))
    api.generateLoad.mockResolvedValue({ ...PENDING, weeks: { plant: {
      label: 'peak week', period: null, snapshots: snaps,
      new: snaps.map((_, i) => (i === 10 ? 80 : 40)), current: snaps.map(() => 30) } } })
    renderEditor()
    await screen.findByRole('option', { name: 'industrial' })
    await userEvent.type(screen.getByRole('textbox', { name: 'Peak MW' }), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    const chart = await screen.findByTestId('week-chart')
    expect(chart.querySelector('figcaption')?.textContent).toBe('peak week: 14–20 Jan 2030')
    const text = screen.getByTestId('week-chart-text').textContent
    expect(text).toContain('peak 80 MW')
    expect(text).toContain('min 40 MW')
    expect(text).toContain('mean 40.238 MW')
    expect(text).toContain('current series is the faint line')
  })

  it('reads a measured upload with its unit', async () => {
    api.uploadLoad.mockResolvedValue({ ...PENDING, provenance: 'measured', unmatched: ['shed'] })
    renderEditor('guided', 'upload')
    const file = new File(['timestamp,plant\n'], 'meter.csv', { type: 'text/csv' })
    await userEvent.upload(screen.getByLabelText('Meter file'), file)
    await userEvent.selectOptions(screen.getByLabelText('Unit'), 'MWh')
    await userEvent.click(screen.getByRole('button', { name: /Read file/ }))
    await waitFor(() => expect(api.uploadLoad).toHaveBeenCalledWith('Site A', file, {
      unit: 'MWh', timezone: undefined, annual_mwh: undefined,
    }))
    expect(await screen.findByText(/Not loads here: shed/)).toBeTruthy()
  })

  it('refines by an ordered op list and shows the refined result', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    api.refine.mockResolvedValue({ ...PENDING, job_id: 'job-2', refinements: [{ op: 'scale_window' }],
      stats: { plant: { peak_mw: 75, annual_mwh: 420000, modelled_mwh: { total: 419000 } } } })
    renderEditor('expert')
    await screen.findByRole('option', { name: 'industrial' })
    await userEvent.type(screen.getByLabelText('Peak MW'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await screen.findByTestId('refine-panel')
    await userEvent.selectOptions(screen.getByLabelText('Refinement'), 'scale_window')
    await userEvent.type(screen.getByLabelText('From hour'), '5')
    await userEvent.type(screen.getByLabelText('To hour'), '10')
    await userEvent.type(screen.getByLabelText('Factor'), '1.3')
    await userEvent.click(screen.getByRole('button', { name: /Add/ }))
    await userEvent.selectOptions(screen.getByLabelText('Refinement'), 'scale_peak_to')
    await userEvent.type(screen.getByLabelText('Value'), '75')
    await userEvent.selectOptions(screen.getByLabelText('Peak basis'), 'connection')
    await userEvent.click(screen.getByRole('button', { name: /Add/ }))
    await userEvent.click(screen.getByRole('button', { name: /Refine/ }))
    await waitFor(() => expect(api.refine).toHaveBeenCalledWith('Site A', 'job-1', [
      { op: 'scale_window', hours: [5, 10], factor: 1.3 },
      { op: 'scale_peak_to', fraction: 0.75, of: 'connection' },
    ]))
    expect((await screen.findByTestId('stat-peak')).textContent).toBe('75 MW')
  })

  it('shows a refusal inline', async () => {
    api.generateLoad.mockRejectedValue({ response: { status: 422, data: { detail: {
      error_kind: 'invalid_archetype_params', message: 'state exactly one of peak_mw or annual_mwh' } } } })
    renderEditor()
    await screen.findByRole('option', { name: 'industrial' })
    await userEvent.type(screen.getByLabelText('Peak MW'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    expect((await screen.findByTestId('editor-error')).textContent).toContain('peak_mw or annual_mwh')
  })
})
