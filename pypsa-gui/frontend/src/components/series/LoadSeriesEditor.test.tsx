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
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
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

const eh = vi.hoisted(() => ({ getEhTemplate: vi.fn() }))
vi.mock('../../api/simulation', async () => {
  const real = await vi.importActual<typeof import('../../api/simulation')>('../../api/simulation')
  return { ...real, resultsApi: { ...real.resultsApi, getEhTemplate: eh.getEhTemplate } }
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

function renderEditor(mode: 'guided' | 'expert' = 'guided', initialTab?: 'archetype' | 'upload', component = 'plant') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LoadSeriesEditor project="Site A" component={component} mode={mode} initialTab={initialTab} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  eh.getEhTemplate.mockResolvedValue(null)
  api.current.mockResolvedValue({ exists: false })
  api.loadArchetypes.mockResolvedValue(CATALOGUE)
})
afterEach(() => cleanup())

describe('LoadSeriesEditor', () => {
  it('says a data-centre set-up owns its load, with its numbers, and offers no picker', async () => {
    eh.getEhTemplate.mockResolvedValue({ dc_load: { spec: { pue_at_design: 1.3, phases: [{ it_mw: 36 }] } } })
    renderEditor('guided', undefined, 'it_load')
    const note = await screen.findByTestId('dc-owned-note')
    expect(note.textContent).toContain('data-centre set-up (IT 36 MW, PUE 1.3)')
    expect(screen.queryByRole('combobox', { name: 'Archetype' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Preview/ })).toBeNull()
  })

  it('names the ambient basis, and which fields were entered, on the card', async () => {
    const ambient = { source: 'site_location', entered: ['ambient_diurnal_amplitude_k'], mean_c: 27,
      design_day_peak_c: 45, peak_pue: 1.8, peak_facility_mw: 60.2 }
    eh.getEhTemplate.mockResolvedValue({ dc_load: { spec: { pue_at_design: 1.3, phases: [{ it_mw: 36 }] }, ambient } })
    renderEditor('guided', undefined, 'it_load')
    const note = await screen.findByTestId('dc-ambient-basis')
    expect(note.textContent).toBe('Ambient temperature: your entries (daily swing), the rest from the site location. '
      + 'It is a climate zone, not weather data. Mean 27.0 °C · design-day peak 45.0 °C · peak PUE 1.80 · peak facility load 60.2 MW')
  })

  it('says "the rest from the default temperate curve" for a partial entry with no site location', async () => {
    const ambient = { source: 'default', entered: ['ambient_annual_mean_c'], mean_c: 30,
      design_day_peak_c: 44, peak_pue: 1.78, peak_facility_mw: 50.1 }
    eh.getEhTemplate.mockResolvedValue({ dc_load: { spec: { pue_at_design: 1.3, phases: [{ it_mw: 36 }] }, ambient } })
    renderEditor('guided', undefined, 'it_load')
    const note = await screen.findByTestId('dc-ambient-basis')
    expect(note.textContent).toContain('your entries (mean), the rest from the default temperate curve. No site location was set.')
  })

  it('never prints an engine key for an unknown entered field', async () => {
    const ambient = { source: 'site_location', entered: ['ambient_future_key'], mean_c: 27,
      design_day_peak_c: 45, peak_pue: 1.8, peak_facility_mw: 60.2 }
    eh.getEhTemplate.mockResolvedValue({ dc_load: { spec: { pue_at_design: 1.3, phases: [{ it_mw: 36 }] }, ambient } })
    renderEditor('guided', undefined, 'it_load')
    const note = await screen.findByTestId('dc-ambient-basis')
    expect(note.textContent).toContain('your entries, the rest from the site location.')
    expect(note.textContent).not.toContain('ambient_future_key')
  })

  it('keeps the picker for another load of a data-centre project', async () => {
    eh.getEhTemplate.mockResolvedValue({ dc_load: { spec: { pue_at_design: 1.3, phases: [{ it_mw: 36 }] } } })
    renderEditor('guided', undefined, 'office')
    await screen.findByRole('option', { name: 'Industrial' })
    expect(screen.queryByTestId('dc-owned-note')).toBeNull()
  })


  it('shows only the key parameters in Guided', async () => {
    renderEditor('guided')
    await screen.findByRole('option', { name: 'Industrial' })
    expect(screen.queryByTestId('archetype-params')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Advanced' }))
    expect(screen.getByLabelText('Shifts per day')).toBeTruthy()
  })

  it('shows no snake_case name in the editor text (RAW-NAMES-1)', async () => {
    renderEditor('expert')
    await screen.findByRole('option', { name: 'Industrial' })
    expect(screen.getByTestId('archetype-params').textContent).not.toMatch(/\b[a-z]+(_[a-z0-9]+)+\b/)
    expect(screen.getByRole('option', { name: 'Industrial three shift' })).toBeTruthy()
  })

  it('says in place why Preview is off and what turns it on', async () => {
    renderEditor('guided')
    expect(screen.getByRole('button', { name: /Preview/ })).toHaveProperty('disabled', true)
    expect(screen.getByTestId('disabled-reason').textContent).toBe('Enter a peak in MW to preview.')
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    expect(screen.queryByTestId('disabled-reason')).toBeNull()
  })

  it('reports the peak as a share of the grid connection, and omits it without one', async () => {
    const withShare = { ...PENDING, stats: { plant: {
      ...PENDING.stats.plant, peak_share_of_connection: 0.8, connection_link: 'poc' } } }
    api.generateLoad.mockResolvedValueOnce(withShare).mockResolvedValueOnce(PENDING)
    renderEditor('expert')
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    const line = await screen.findByTestId('stat-connection-share')
    expect(line.textContent).toBe('80 % of the grid connection')
    expect(line.getAttribute('title')).toContain('rated power of poc')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(screen.queryByTestId('stat-connection-share')).toBeNull())
  })

  it('opens the archetype parameters in Expert', async () => {
    renderEditor('expert')
    await screen.findByRole('option', { name: 'Industrial' })
    expect(screen.getByTestId('archetype-params')).toBeTruthy()
  })

  it('previews an archetype at the stated peak and shows the energy the model counts', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    renderEditor('expert')
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    await userEvent.type(screen.getByLabelText('Shifts per day'), '3')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'], params: { peak_mw: 80, shifts: 3 },
    }))
    expect((await screen.findByTestId('stat-peak')).textContent).toBe('80 MW')
    expect(screen.getByTestId('stat-modelled').textContent).toBe('399,000 MWh')
    expect(screen.getByText('Time zone assumed UTC').tagName).toBe('SUMMARY')
    expect(screen.getByTestId('pending-warnings').textContent).toContain('applied unshifted')
  })

  it('sends a preset with its archetype', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    renderEditor()
    await screen.findByRole('option', { name: 'Industrial three shift' })
    await userEvent.selectOptions(screen.getByLabelText('Archetype'), 'industrial_three_shift')
    await userEvent.click(screen.getByRole('button', { name: 'Annual energy (MWh)' }))
    await userEvent.type(screen.getByLabelText('Annual energy (MWh)'), '5000')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'],
      params: { annual_mwh: 5000, preset: 'industrial_three_shift' },
    }))
  })

  it('applies the pending series and says undo reverts it', async () => {
    api.generateLoad.mockResolvedValue(PENDING)
    api.apply.mockResolvedValue({ component: 'plant', component_type: 'loads', attribute: 'p_set',
      peak: 80, mean: 40, min: 20, before: { peak: 10, mean: 10, min: 10 }, warnings: [] })
    renderEditor()
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await userEvent.click(await screen.findByRole('button', { name: /Apply to plant/ }))
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith('Site A', { job_id: 'job-1', component: 'plant' }))
    expect((await screen.findByTestId('applied')).textContent).toBe("Applied to plant's load: peak 10 → 80 MW. Undo (Ctrl+Z) reverts it.")
    expect(screen.queryByRole('button', { name: /Apply to plant/ })).toBeNull()
  })

  it('holds Apply when the preview says this network cannot take the series', async () => {
    api.generateLoad.mockResolvedValue({ ...PENDING,
      stats: { plant: { peak_mw: 80, annual_mwh: 400000, modelled_mwh: null } },
      warnings: ['not_applicable: network snapshots: snapshots must span one full calendar year'] })
    renderEditor()
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByRole('textbox', { name: 'Peak load (MW)' }), '80')
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
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByRole('textbox', { name: 'Peak load (MW)' }), '80')
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
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
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
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    expect((await screen.findByTestId('editor-error')).textContent).toContain('peak_mw or annual_mwh')
  })
})

// TE-04: the editor opens on what the load carries.
describe('LoadSeriesEditor opens on the load', () => {
  it('shows the current shape and peak, and asks for the load\'s own unit', async () => {
    api.current.mockResolvedValue({ exists: true, provenance: 'network', kind: 'commercial', rows: 168, peak: 485.85, unit: 't/h' })
    renderEditor()
    expect((await screen.findByTestId('current-series')).textContent).toContain('peak 485.85 t/h')
    const field = await screen.findByLabelText('Peak load (t/h)')
    expect(field.getAttribute('placeholder')).toBe('now 485.85')
    expect((field as HTMLInputElement).value).toBe('')
    await waitFor(() => expect((screen.getByLabelText('Archetype') as HTMLSelectElement).value).toBe('commercial'))
  })
})

// TSG 06: a refined current series replays its ops on a re-generate unless
// the editor's toggle is cleared; a refused replay names the op and offers
// the preview without them.
describe('LoadSeriesEditor replay of stored refinements', () => {
  const REFINED = { exists: true, provenance: 'synthetic/assumed, user-refined', kind: 'industrial',
    rows: 8760, refinements: 2 }

  it('keeps the refinements by default and drops them when the toggle is cleared', async () => {
    api.current.mockResolvedValue(REFINED)
    api.generateLoad.mockResolvedValue(PENDING)
    renderEditor()
    await screen.findByRole('option', { name: 'Industrial' })
    const toggle = within(await screen.findByTestId('replay-toggle')).getByRole('checkbox')
    expect((toggle as HTMLInputElement).checked).toBe(true)
    expect(screen.getByTestId('replay-toggle').textContent).toContain('Keep its 2 refinements')
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenLastCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'], params: { peak_mw: 80 },
    }))
    await userEvent.click(toggle)
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenLastCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'], params: { peak_mw: 80 }, replay_refinements: false,
    }))
  })

  it('hides the toggle when the current series is not refined', async () => {
    api.current.mockResolvedValue({ ...REFINED, refinements: 0 })
    renderEditor()
    await screen.findByTestId('current-series')
    expect(screen.queryByTestId('replay-toggle')).toBeNull()
  })

  it('hides the toggle when another archetype made the current series', async () => {
    api.current.mockResolvedValue({ ...REFINED, kind: 'commercial' })
    renderEditor()
    await screen.findByTestId('current-series')
    await userEvent.selectOptions(await screen.findByLabelText('Archetype'), 'industrial')
    expect(screen.queryByTestId('replay-toggle')).toBeNull()
  })

  it('names a refused op and its index and retries without the refinements', async () => {
    api.current.mockResolvedValue(REFINED)
    api.generateLoad.mockRejectedValueOnce({ response: { status: 422, data: { detail: {
      error_kind: 'replay_refused', op: 'scale_window', index: 1,
      message: 'op 1 (scale_window) no longer fits: the window is outside the year',
      retry: { replay_refinements: false } } } } })
    api.generateLoad.mockResolvedValueOnce(PENDING)
    renderEditor()
    await screen.findByRole('option', { name: 'Industrial' })
    await userEvent.type(screen.getByLabelText('Peak load (MW)'), '80')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    const refused = await screen.findByTestId('replay-refused')
    expect(refused.textContent).toBe(
      'Step 2 of the refinements (scale an hour window) no longer fits the new series.Preview without the refinements')
    expect(screen.queryByTestId('editor-error')).toBeNull()
    await userEvent.click(within(refused).getByRole('button', { name: 'Preview without the refinements' }))
    await waitFor(() => expect(api.generateLoad).toHaveBeenLastCalledWith('Site A', {
      archetype: 'industrial', loads: ['plant'], params: { peak_mw: 80 }, replay_refinements: false,
    }))
    expect(await screen.findByTestId('stat-peak')).toBeTruthy()
  })
})
