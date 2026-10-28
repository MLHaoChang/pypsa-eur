import { beforeEach, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import RenewableTuner from './RenewableTuner'

const api = vi.hoisted(() => ({ schema: vi.fn(), weather: vi.fn(), presets: vi.fn(), generate: vi.fn(), pending: vi.fn(), compare: vi.fn(), apply: vi.fn() }))
vi.mock('../../api/profiles', async () => ({ ...await vi.importActual('../../api/profiles'), profilesApi: api }))
const row = { generator: 'PV', technology: 'solar' as const, bus: 'B', lon: 8, lat: 50, coord_source: 'bus' as const, profile: 'generated' as const }
function mount(onApplied = vi.fn()) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <RenewableTuner project="P" row={row} onApplied={onApplied} />
  </QueryClientProvider>)
}
beforeEach(() => {
  vi.clearAllMocks()
  useUIStore.setState({ currentProject: 'P', uiMode: 'expert', readOnly: false })
  api.presets.mockResolvedValue([{ name: 'pv', technology: 'solar', source_type: 'atlite', params: {} }, { name: 'synthetic', technology: 'solar', source_type: 'synthetic', params: {} }])
  api.weather.mockResolvedValue({ source: 'atlite', year: 2025 })
  api.schema.mockResolvedValue({ technology: 'solar', preset: 'pv', fields: [
    { key: 'tilt', label: 'Tilt', unit: '°', type: 'number', min: 0, max: 90, preset_value: 35, sources: ['atlite'], provenance: 'Preset panel' },
    { key: 'inverter_efficiency', label: 'Inverter efficiency', unit: 'fraction', type: 'number', min: 0.8, max: 1, preset_value: 0.95, sources: ['atlite'], provenance: 'Preset panel' },
  ] })
  api.generate.mockResolvedValue({ id: 'job' })
  api.pending.mockResolvedValue({ pending: [{ job_id: 'job' }], jobs: [] })
  api.compare.mockResolvedValue({ a: null, b: { cf: 0.4, flh: 3504, peak: 1, peak_hour: 12, p10: 0.1, p50: 0.4, p90: 0.9, monthly: {}, duration: [1, 0] }, delta: null,
    week: { snapshots: ['2025-07-01T00:00:00'], a: null, b: [0.4] } })
  api.apply.mockResolvedValue({ component: 'PV', hours: 168, warnings: [] })
})
it('marks changed schema fields and Reset to preset restores them', async () => {
  mount()
  const tilt = await screen.findByLabelText('Tilt (°)')
  await userEvent.clear(tilt); await userEvent.type(tilt, '20')
  expect(screen.getByTestId('tune-changed-tilt').textContent).toContain('Changed')
  await userEvent.click(screen.getByRole('button', { name: 'Reset to preset' }))
  expect((tilt as HTMLInputElement).value).toBe('35')
  expect(screen.queryByTestId('tune-changed-tilt')).toBeNull()
})
it('sends changed fields only, compares before apply, and applies the single pending job', async () => {
  mount()
  const tilt = await screen.findByLabelText('Tilt (°)')
  await userEvent.clear(tilt); await userEvent.type(tilt, '20')
  await userEvent.click(screen.getByRole('button', { name: 'Generate' }))
  await waitFor(() => expect(api.compare).toHaveBeenCalledWith('P', 'current:PV', 'pending:job:PV', 'PV'))
  expect(api.generate).toHaveBeenCalledWith('P', { points: [], from_generators: ['PV'], technology: 'solar', preset: 'pv', params: { tilt: 20 } })
  expect(api.apply).not.toHaveBeenCalled()
  await userEvent.click(await screen.findByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(api.apply).toHaveBeenCalledWith('P', 'job', 'PV'))
})
it('explains source-disabled fields and filters presets by the weather source', async () => {
  api.weather.mockResolvedValue({ source: 'synthetic', year: 2025 })
  mount()
  expect((await screen.findByLabelText('Inverter efficiency (fraction)') as HTMLInputElement).disabled).toBe(true)
  expect(screen.getAllByText('Needs ERA5 weather: switch the weather source to use it').length).toBeGreaterThan(0)
  expect(screen.queryByRole('option', { name: 'pv' })).toBeNull()
  expect(screen.getByRole('option', { name: 'synthetic' })).toBeTruthy()
})
it('keeps presets visible when an older server omits source_type', async () => {
  api.presets.mockResolvedValue([{ name: 'legacy', technology: 'solar', params: {} }])
  mount()
  expect(await screen.findByRole('option', { name: 'legacy' })).toBeTruthy()
})
it('rejects non-object Advanced JSON with a plain message before generation', async () => {
  mount(); await screen.findByLabelText('Tilt (°)')
  await userEvent.click(screen.getByText('Advanced'))
  fireEvent.change(screen.getByLabelText('Advanced settings'), { target: { value: '[]' } })
  await userEvent.click(screen.getByRole('button', { name: 'Generate' }))
  expect(await screen.findByText('Advanced settings must be a JSON object.')).toBeTruthy()
  expect(api.generate).not.toHaveBeenCalled()
})
it('refuses comparison in place, provides Retry, and prevents apply', async () => {
  api.compare.mockRejectedValue(new Error('Profile unavailable'))
  mount(); await screen.findByLabelText('Tilt (°)')
  await userEvent.click(screen.getByRole('button', { name: 'Generate' }))
  expect(await screen.findByText(/Couldn't compare: Profile unavailable/)).toBeTruthy()
  expect(screen.getByRole('button', { name: '› Retry' })).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText('Comparison failed: retry or close')).toBeTruthy()
})
it('invalidates the preview when settings change and prevents an old apply', async () => {
  mount(); const tilt = await screen.findByLabelText('Tilt (°)')
  await userEvent.click(screen.getByRole('button', { name: 'Generate' }))
  await screen.findByTestId('compare-week')
  await userEvent.clear(tilt); await userEvent.type(tilt, '20')
  expect(screen.getByText('This preview is out of date: generate again')).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(true)
})

it('does not close a newer editor when an old apply finishes after Close', async () => {
  let finish!: (result: object) => void
  api.apply.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const applied = vi.fn()
  const view = mount(applied)
  await screen.findByLabelText('Tilt (°)')
  await userEvent.click(screen.getByRole('button', { name: 'Generate' }))
  await screen.findByTestId('compare-week')
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  view.unmount()
  await act(async () => { finish({ component: 'PV', hours: 168 }); await Promise.resolve() })
  expect(applied).not.toHaveBeenCalled()
})
