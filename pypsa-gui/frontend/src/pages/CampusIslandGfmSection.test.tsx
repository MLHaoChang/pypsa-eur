// Recommended GFM settings in the island section (GFM-b). It holds the section
// to the backend's contract:
// - not run and stale say why, from the backend's reason;
// - a recommendation is labelled heuristic screening, shows current →
//   recommended per unit and each case before and after;
// - not achievable lists the capacity needed and offers no Apply;
// - Recommend queues the job (with allow_i_max when ticked) and is disabled
//   while a campus job runs;
// - Apply writes, then says the hub must be re-solved with a Solve button
//   (owner decision 2026-10-15).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { GfmRecommendation, GfmSettingsState } from '../api/campusElectrical'
import { useUIStore } from '../store/uiStore'
import CampusIslandGfmSection from './CampusIslandGfmSection'

const api = vi.hoisted(() => ({
  gfmSettings: vi.fn(), recommendGfmSettings: vi.fn(), applyGfmSettings: vi.fn(), state: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const rec = (over: Partial<GfmRecommendation> = {}): GfmRecommendation => ({
  label: 'heuristic screening: one droop and one inertia per GFM …', ledger: ['droop_pct 1–10 % (assumed)'],
  status: 'recommended', achievable: true, changed: true,
  settings: { BESS1: { model: 'gfm_droop',
                       current: { droop_pct: 4, tau_f_s: 0.1, i_max_pu: 1.2, H_equivalent_s: 0.4 },
                       recommended: { droop_pct: 2.5, tau_f_s: 0.6, i_max_pu: 1.2, H_equivalent_s: 1.2 } } },
  cases: [{ key: '2030 h5 seamless E2', ok_before: false, binding_before: 'transition RoCoF',
            ok_after: true, binding_after: '' }],
  capacity: [], comparison: [], simulations: 32, max_simulations: 400, margin: 0.1, allow_i_max: false,
  notes: [], excluded: ['E4 (no grid-forming converter in a UPS-bridged genset start)'], ...over,
})
const ready = (r: GfmRecommendation = rec()): GfmSettingsState => ({ state: 'ready', reason: '', recommendation: r })

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><CampusIslandGfmSection name="Hub" /></QueryClientProvider>)
}

describe('CampusIslandGfmSection', () => {
  beforeEach(() => {
    api.state.mockResolvedValue({ run_job: null })
    api.recommendGfmSettings.mockResolvedValue({ id: 'j1', kind: 'campus', status: 'queued' })
  })
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('not run says why and offers Recommend', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'not_run', recommendation: null,
                                        reason: 'Run the campus study with island requirements first, then Recommend GFM settings' })
    renderSection()
    expect((await screen.findByTestId('gfm-reason')).textContent).toMatch(/campus study with island requirements/)
    expect(screen.getByRole('button', { name: /Recommend GFM settings/ })).toBeTruthy()
    expect(screen.queryByTestId('gfm-recommendation')).toBeNull()
  })

  it('shows a recommendation as heuristic screening, current → recommended and the cases', async () => {
    api.gfmSettings.mockResolvedValue(ready())
    renderSection()
    expect((await screen.findByTestId('gfm-label')).textContent).toMatch(/^heuristic screening/)
    const table = screen.getByTestId('gfm-settings').textContent ?? ''
    expect(table).toMatch(/BESS1/)
    expect(table).toMatch(/4 → 2\.5/)
    expect(table).toMatch(/0\.1 → 0\.6/)
    expect(screen.getByTestId('gfm-cases').textContent).toMatch(/fails: transition RoCoF.*passes/)
    expect(screen.getByTestId('gfm-status').textContent).toMatch(/32 of at most 400 simulations/)
  })

  it('not achievable lists the capacity needed and offers no Apply', async () => {
    api.gfmSettings.mockResolvedValue(ready(rec({
      status: 'not_achievable', achievable: false,
      capacity: [{ case: '2030 h5 seamless E2', kind: 'inertia', delta: 40, unit: 'MW·s', binding: 'transition RoCoF',
                   derivation: 'f0·ΔP/RoCoF', feasible: true, note: '' }] })))
    renderSection()
    expect((await screen.findByTestId('gfm-status')).textContent).toMatch(/capacity is needed/)
    expect(screen.getByTestId('gfm-capacity').textContent).toMatch(/inertia \+40 MW·s/)
    expect(screen.queryByRole('button', { name: /Apply/ })).toBeNull()
  })

  it('stale says so and offers Recommend again, never Apply', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'stale', recommendation: rec(),
                                        reason: 'The campus run changed since these settings were recommended; recommend them again' })
    renderSection()
    expect((await screen.findByTestId('gfm-reason')).textContent).toMatch(/stale.*recommend them again/)
    expect(screen.getByRole('button', { name: /Recommend again/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Apply/ })).toBeNull()
  })

  it('Recommend queues the job with allow_i_max when ticked', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'not_run', recommendation: null, reason: 'Recommend GFM settings: …' })
    renderSection()
    await userEvent.click(await screen.findByLabelText(/let i_max rise/))
    await userEvent.click(screen.getByRole('button', { name: /Recommend GFM settings/ }))
    await waitFor(() => expect(api.recommendGfmSettings).toHaveBeenCalledWith('Hub', true))
  })

  it('a running campus job disables Recommend; a running search says Recommending…', async () => {
    api.gfmSettings.mockResolvedValue(ready())
    api.state.mockResolvedValue({ run_job: { id: 'j', status: 'running', stage: 'gfm_settings' } })
    renderSection()
    const btn = await screen.findByRole('button', { name: /Recommending…/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('a refusal shows its reason', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'not_run', recommendation: null, reason: '…' })
    api.recommendGfmSettings.mockRejectedValue({ response: { status: 422, data: {
      detail: 'the last campus run has no frequency check; run the campus study with island requirements first' } } })
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /Recommend GFM settings/ }))
    expect((await screen.findByTestId('gfm-refusal')).textContent).toMatch(/no frequency check/)
  })

  it('Apply writes, then asks for a re-solve with the Solve button', async () => {
    const requestRunSolve = vi.fn()
    useUIStore.setState({ requestRunSolve })
    api.gfmSettings.mockResolvedValueOnce(ready())
    api.gfmSettings.mockResolvedValue(ready(rec({ applied: { at: 1, hub_resolve_needed: true, changes: [
      { unit: 'BESS1', pypsa_name: 'bess', field: 'droop_pct', from: 4, to: 2.5 },
      { unit: 'BESS1', pypsa_name: 'bess', field: 'tau_f_s', from: 0.1, to: 0.6 }] } })))
    api.applyGfmSettings.mockResolvedValue({ changes: [], hub_resolve_needed: true })
    renderSection()
    await userEvent.click(await screen.findByRole('button', { name: /Apply to the island data/ }))
    await waitFor(() => expect(api.applyGfmSettings).toHaveBeenCalledWith('Hub'))
    const resolve = await screen.findByTestId('gfm-resolve')
    expect(resolve.textContent).toMatch(/Applied \(2 settings\).*Re-solve the hub: these settings change its island rows/)
    expect(screen.queryByRole('button', { name: /Apply to the island data/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Solve/ }))
    expect(requestRunSolve).toHaveBeenCalled()
  })
})
