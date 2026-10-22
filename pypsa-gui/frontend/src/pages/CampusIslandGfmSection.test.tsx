// Recommended GFM settings in the island section (GFM-b). It holds the section
// to the backend's contract:
// - not run and stale say why, from the backend's reason;
// - a recommendation is labelled heuristic screening, shows current →
//   recommended per unit and each case before and after;
// - not achievable lists the capacity needed and offers no Apply;
// - Recommend queues the job (with allow_i_max when ticked) and is disabled
//   while a campus job runs;
// - Apply goes through the edit card (W10 R3): the card shows the changes, the write carries
//   its digest, a moved recommendation is refused, and the receipt reads the values back;
//   then it says the hub must be re-solved with a Solve button (owner decision 2026-10-15).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { GfmRecommendation, GfmSettingsState } from '../api/campusElectrical'
import { useUIStore } from '../store/uiStore'
import CampusIslandGfmSection from './CampusIslandGfmSection'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const api = vi.hoisted(() => ({
  gfmSettings: vi.fn(), recommendGfmSettings: vi.fn(), applyGfmSettings: vi.fn(), gfmApplyCard: vi.fn(), state: vi.fn(),
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

  it('not achievable with the island rows off says the hub solve will not show the capacity (UX review N7)', async () => {
    api.gfmSettings.mockResolvedValue({ ...ready(rec({ status: 'not_achievable', achievable: false, changed: false,
      settings: {} })), island_rows_now: false })
    renderSection()
    expect((await screen.findByTestId('gfm-island-rows-off')).textContent).toMatch(/not sized to island/)
  })

  it('says nothing about the rows when they are on, or when the server does not say', async () => {
    api.gfmSettings.mockResolvedValue({ ...ready(rec({ status: 'not_achievable', achievable: false, settings: {} })),
      island_rows_now: true })
    const { unmount } = renderSection()
    await screen.findByTestId('gfm-status')
    expect(screen.queryByTestId('gfm-island-rows-off')).toBeNull()
    unmount()
    api.gfmSettings.mockResolvedValue(ready(rec({ status: 'not_achievable', achievable: false, settings: {} })))
    renderSection()
    await screen.findByTestId('gfm-status')
    expect(screen.queryByTestId('gfm-island-rows-off')).toBeNull()
  })

  it('a collapse case names the collapse without a number', async () => {
    api.gfmSettings.mockResolvedValue(ready(rec({
      status: 'not_achievable', achievable: false,
      capacity: [{ case: '2027 h0 seamless E2', kind: 'inertia', delta: null, unit: 'MW·s',
                   binding: 'frequency collapse: no grid-forming headroom', derivation: 'collapse', feasible: false,
                   note: 'no machine joined and every GFM at a limit: the island needs a former, not a cut' }] })))
    renderSection()
    const text = (await screen.findByTestId('gfm-capacity')).textContent
    expect(text).toMatch(/2027 h0 seamless E2: frequency collapse: no grid-forming headroom/)
    expect(text).toMatch(/needs a former/)
    expect(text).not.toMatch(/\+/)
  })

  it('the live H2 hub collapse renders, as the backend serves it (Infinity read as null)', async () => {
    // The backend's own fixture: the run's file as gridspine wrote it before the fix.
    const text = readFileSync(resolve(__dirname, '../../../backend/tests/fixtures/gfm_settings_h2_collapse_pre_fix.json'), 'utf8')
    const live = JSON.parse(text.replace(/-?Infinity/g, 'null')) as GfmRecommendation
    api.gfmSettings.mockResolvedValue(ready(live))
    renderSection()
    expect((await screen.findByTestId('gfm-status')).textContent).toMatch(/not achievable.*capacity is needed/)
    const cap = screen.getByTestId('gfm-capacity').textContent ?? ''
    expect(cap).toMatch(/frequency collapse: no grid-forming headroom: no machine joined/)
    expect(cap).not.toMatch(/\+|NaN|undefined|null/)
    expect(screen.getByTestId('gfm-settings').textContent).toMatch(/SITE_BATTERY/)
    expect(screen.queryByRole('button', { name: /Apply/ })).toBeNull()
  })

  it('no case evaluated reads "Not applicable", not "already pass with margin" (UX review N6)', async () => {
    api.gfmSettings.mockResolvedValue(ready(rec({ status: 'unchanged', changed: false, settings: {}, cases: [],
      simulations: 0, notes: ['no grid-forming converter in any case: nothing to tune'] })))
    renderSection()
    const status = await screen.findByTestId('gfm-status')
    expect(status.textContent).toMatch(/Not applicable/)
    expect(status.textContent).toMatch(/nothing to tune/)
    expect(status.textContent).not.toMatch(/pass with margin|simulations/)
    expect(screen.queryByRole('button', { name: /Apply/ })).toBeNull()
  })

  it('an unchanged result that did evaluate cases still says the settings pass', async () => {
    api.gfmSettings.mockResolvedValue(ready(rec({ status: 'unchanged', changed: false, settings: {}, simulations: 12 })))
    renderSection()
    expect((await screen.findByTestId('gfm-status')).textContent).toMatch(/already pass with margin/)
  })

  it('a null setting reads "not stated"', async () => {
    api.gfmSettings.mockResolvedValue(ready(rec({ settings: { BESS1: { model: 'gfm_droop',
      current: { droop_pct: 4, tau_f_s: 0.1, i_max_pu: null, H_equivalent_s: 0.4 } } } })))
    renderSection()
    expect((await screen.findByTestId('gfm-settings')).textContent).toMatch(/not stated/)
  })

  it('stale says so and offers Recommend again, never Apply', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'stale', recommendation: rec(),
                                        reason: 'The campus run changed since these settings were recommended; recommend them again' })
    renderSection()
    expect((await screen.findByTestId('gfm-reason')).textContent).toMatch(/Out of date.*recommend them again/)
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

  const card = {
    version: 1, kind: 'settings_edit', tool: 'campus_apply_gfm_settings', complete: true, digest: 'd1',
    says: 'BESS1 droop_pct 4 → 2.5 %; BESS1 tau_f_s 0.1 → 0.6 s', section: { id: 'assets', label: 'Network elements' },
    rows: [
      { component_class: 'Settings', name: 'BESS1', field: 'droop_pct', label: 'Droop', old: 4, new: 2.5, unit: '%', role: 'typed' },
      { component_class: 'Settings', name: 'BESS1', field: 'tau_f_s', label: 'Frequency filter time τ_f', old: 0.1, new: 0.6, unit: 's', role: 'typed' },
      { component_class: 'Settings', name: 'Island study', field: 'note_1', label: 'Note', old: null, new: null, unit: '', role: 'note',
        note: 'The island study made with the current settings is discarded.' }],
  }
  const receipt = { version: 1, digest: 'd1', outcome: 'verified', says: card.says, results_stale: false,
    undo: { tool: 'undo_last', available: false }, rows: card.rows }

  it('Apply shows the edit card first, writes with its digest, then asks for a re-solve with the Solve button', async () => {
    const requestRunSolve = vi.fn()
    useUIStore.setState({ requestRunSolve })
    api.gfmSettings.mockResolvedValueOnce(ready())
    api.gfmSettings.mockResolvedValue({ ...ready(), state: 'stale', applied: { at: 1, hub_resolve_needed: true, changes: [
      { unit: 'BESS1', pypsa_name: 'bess', field: 'droop_pct', from: 4, to: 2.5 },
      { unit: 'BESS1', pypsa_name: 'bess', field: 'tau_f_s', from: 0.1, to: 0.6 }] } })
    api.gfmApplyCard.mockResolvedValue(card)
    api.applyGfmSettings.mockResolvedValue({ changes: [], hub_resolve_needed: true, receipt })
    renderSection()
    expect(screen.queryByTestId('gfm-card')).toBeNull()
    await userEvent.click(await screen.findByTestId('gfm-review'))
    expect(await screen.findByTestId('gfm-card')).toBeTruthy()
    expect(api.applyGfmSettings).not.toHaveBeenCalled()          // nothing is written by looking
    await userEvent.click(screen.getByRole('button', { name: /Apply to the island data/ }))
    await waitFor(() => expect(api.applyGfmSettings).toHaveBeenCalledWith('Hub', 'd1'))
    expect((await screen.findByTestId('chat-edit-receipt')).textContent).toMatch(/Read back from the project/)
    const resolve = await screen.findByTestId('gfm-resolve')
    expect(resolve.textContent).toMatch(/Applied \(2 settings\).*Re-solve the hub: these settings change its island rows/)
    expect(screen.queryByRole('button', { name: /Apply to the island data/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /Solve/ }))
    expect(requestRunSolve).toHaveBeenCalled()
  })

  it('a recommendation that moved since the card was shown is refused, and the card goes', async () => {
    api.gfmSettings.mockResolvedValue(ready())
    api.gfmApplyCard.mockResolvedValue(card)
    api.applyGfmSettings.mockRejectedValue({ response: { status: 409, data: { detail: {
      error_kind: 'edit_preview_stale', message: 'The recommendation changed since the card was shown; nothing was applied.' } } } })
    renderSection()
    await userEvent.click(await screen.findByTestId('gfm-review'))
    await userEvent.click(await screen.findByRole('button', { name: /Apply to the island data/ }))
    expect((await screen.findByTestId('gfm-refusal')).textContent).toMatch(/nothing was applied/)
    expect(screen.queryByTestId('gfm-card')).toBeNull()
  })

  it('Cancel closes the card and writes nothing', async () => {
    api.gfmSettings.mockResolvedValue(ready())
    api.gfmApplyCard.mockResolvedValue(card)
    renderSection()
    await userEvent.click(await screen.findByTestId('gfm-review'))
    await userEvent.click(await screen.findByRole('button', { name: /Cancel/ }))
    expect(screen.queryByTestId('gfm-card')).toBeNull()
    expect(api.applyGfmSettings).not.toHaveBeenCalled()
  })

  it('the re-solve line stays after a campus run removed the recommendation (#310 review)', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'not_run', reason: 'Recommend GFM settings: …', recommendation: null,
      applied: { at: 1, hub_resolve_needed: true, changes: [
        { unit: 'BESS1', pypsa_name: 'bess', field: 'droop_pct', from: 4, to: 2.5 }] } })
    renderSection()
    expect((await screen.findByTestId('gfm-resolve')).textContent)
      .toMatch(/Applied \(1 setting\).*Re-solve the hub: these settings change its island rows/)
  })

  it('with island constraints off it says the hub solve does not read the settings, no Solve', async () => {
    api.gfmSettings.mockResolvedValue({ state: 'stale', reason: 'x', recommendation: null,
      applied: { at: 1, hub_resolve_needed: false, changes: [
        { unit: 'BESS1', pypsa_name: 'bess', field: 'droop_pct', from: 4, to: 2.5 }] } })
    renderSection()
    const line = await screen.findByTestId('gfm-resolve')
    expect(line.textContent).toMatch(/Island constraints are off, so the hub solve does not read these settings/)
    expect(screen.queryByRole('button', { name: /Solve/ })).toBeNull()
  })
})
