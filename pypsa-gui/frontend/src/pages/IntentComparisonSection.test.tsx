// AS-10: choosing intents starts one background job and renders one row per intent; a loop that did not
// converge is never a pass; a converged row names its not-run checks; intent A has an editable autonomy;
// intent D says when no grid code is set.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { IntentInputDefaults, IntentJob, IntentRow } from '../api/intentComparison'
import IntentComparisonSection, { assetLabel, checkLabel, refusalField } from './IntentComparisonSection'

const api = vi.hoisted(() => ({ start: vi.fn(), get: vi.fn(), abort: vi.fn(), defaults: vi.fn() }))
vi.mock('../api/intentComparison', async () => {
  const real = await vi.importActual<typeof import('../api/intentComparison')>('../api/intentComparison')
  return { ...real, intentComparisonApi: api }
})
const proj = vi.hoisted(() => ({ list: vi.fn(), updateSettings: vi.fn() }))
vi.mock('../api/projects', () => ({ projectsApi: proj }))
const net = vi.hoisted(() => ({ getBuses: vi.fn(), getLoads: vi.fn(), getLinks: vi.fn() }))
vi.mock('../api/network', () => ({ networkApi: net }))
const ui = vi.hoisted(() => ({ setSlidePanel: vi.fn(), requestGridCodeScroll: vi.fn() }))
vi.mock('../store/uiStore', () => ({ useUIStore: (sel: (s: typeof ui) => unknown) => sel(ui) }))
const nav = vi.hoisted(() => ({ openIslandLimits: vi.fn() }))
vi.mock('../layout/flowNavigation', () => ({ withReturnFrom: (jump: () => void) => jump(), openIslandLimits: nav.openIslandLimits }))

const row = (intent: IntentRow['intent'], over: Partial<IntentRow> = {}): IntentRow => ({
  intent, status: 'converged', outcome: 'pass', verdict: '', cost_eur: 1000, assets: { 'store:bess': { value: 320, unit: 'MWh' }, q_ss: { value: 12, unit: 'Mvar' } },
  transformer_mva: 100, statcom_mvar: 20, checks: [{ name: 'escr', state: 'ok', value: null, limit: null, note: '' }],
  basis_note: 'same LP; UC/MIP options not reproduced', warnings: [], backup_autonomy_h: null, backup_energy_mwh: null, ...over,
})
const job = (rows: IntentRow[], over: Partial<IntentJob> = {}): IntentJob => ({
  status: 'done', progress: Object.fromEntries(rows.map(r => [r.intent, 'done'])), rows, error: null, ...over,
})

const PQ = {
  x_pu: { value: 0.12, source: 'assumed: a typical transformer short-circuit reactance', kind: 'assumed' as const },
  pf: { value: 0.95, source: 'assumed: a typical load power factor', kind: 'assumed' as const },
  pf_req: { value: 0.95, source: 'assumed: no grid code value', kind: 'assumed' as const },
  s_n_mva: { value: 40, source: "assumed: the connection's rating", kind: 'assumed' as const },
  p_cap_mw: { value: 40, source: "assumed: the connection's rating", kind: 'assumed' as const },
  statcom_capital_cost: { value: 11195.36331689301, source: 'assumed (defaults pack …)', kind: 'assumed' as const },
}
/** A project that holds none of the inputs but the assumed point-of-connection values. */
const HELD_NONE: IntentInputDefaults = {
  link: { value: null, source: null, reason: 'No link is marked as the grid import.' },
  ramp_mw_per_min: { value: null, source: null, reason: 'The project has no ramp limit.' },
  envelope: { value: null, source: null, reason: 'The project has no DC load envelope.' },
  pq: PQ, grid_code: { value: 'eu_rfg_dcc_ce', title: 'EU RfG and DCC', source: 'default' },
  pst_limit: { value: null, source: null, reason: 'The grid code sets no Pst limit.' },
}
/** The Data Center Energy Hub template as shipped (walkthrough bug 06). */
const HELD_DC: IntentInputDefaults = {
  link: { value: 'poc', source: 'project', kind: 'project' },
  ramp_mw_per_min: { value: 4, source: 'Template assumption: 10% of the connection per minute', kind: 'assumed' },
  envelope: { value: { phases: [{}], max: { swing_mw: 23.4, oscillation_amplitude_mw: 7.2, oscillation_period_s: 2 } },
    source: "the DC load's dynamic envelope", kind: 'load_envelope' },
  pq: PQ, grid_code: { value: 'eu_rfg_dcc_ce', title: 'EU RfG and DCC', source: 'default' },
  pst_limit: { value: 0.8, source: 'IEC TR 61000-3-7 indicative planning level', kind: 'assumed' },
}

async function fill(user: ReturnType<typeof userEvent.setup>) {
  const v: Record<string, string> = { critical: '80', ramp: '10', sk: '1000', pst: '1', library: '50,100', swing: '20', amp: '5', period: '2' }
  for (const [k, val] of Object.entries(v)) await user.type(screen.getByTestId(`intent-in-${k}`), val)
  await user.selectOptions(screen.getByTestId('intent-in-link'), 'poc')
}
function renderIt(gridCode: string | null = 'eu_rfg_dcc_ce') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><IntentComparisonSection name="Hub A" gridCode={gridCode} /></QueryClientProvider>)
}
async function runWith(user: ReturnType<typeof userEvent.setup>, rows: IntentRow[], pick: string[]) {
  api.get.mockResolvedValue(null)
  api.start.mockResolvedValue(job(rows))
  renderIt(); await fill(user)
  for (const p of pick) await user.click(screen.getByLabelText(new RegExp(`^${p}\\. `)))
  await user.click(screen.getByTestId('intent-run'))
  await waitFor(() => expect(screen.getByTestId(`intent-row-${rows[0].intent}`)).toBeTruthy())
}
afterEach(() => { cleanup(); Object.values(api).forEach(m => m.mockReset()); Object.values(proj).forEach(m => m.mockReset())
  Object.values(net).forEach(m => m.mockReset()); })
beforeEach(() => {
  net.getBuses.mockResolvedValue([]); net.getLoads.mockResolvedValue([])
  net.getLinks.mockResolvedValue([{ name: 'poc', bus0: 'grid', bus1: 'hub', p_min_pu: 0 }])
  api.defaults.mockResolvedValue(HELD_NONE)
})
const stored = (h?: number) => proj.list.mockResolvedValue([{ name: 'Hub A', settings: h == null ? {} : { backup_autonomy_h: h } }])

describe('IntentComparisonSection', () => {
  it('needs two intents, then starts one job and renders one row per intent', async () => {
    const user = userEvent.setup()
    await runWith(user, [row('B'), row('C')], ['B', 'C'])
    expect(api.start.mock.calls[0][1].intents).toEqual([{ intent: 'B' }, { intent: 'C' }])
    expect(api.start.mock.calls[0][1].grid_code).toBe('eu_rfg_dcc_ce')
    expect(screen.getByTestId('intent-row-C')).toBeTruthy()
    expect(screen.getByTestId('intent-basis').textContent).toContain('UC/MIP')
  })

  it('disables Compare with a visible reason under two intents', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/^B\. /))
    expect((screen.getByTestId('intent-run') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('intent-blocked').textContent).toContain('Pick at least two intents')
  })

  it('never shows a not converged, unresolved, failed or stopped loop as a pass', async () => {
    const user = userEvent.setup()
    await runWith(user, [row('B', { outcome: 'not_converged', status: 'not_converged' }),
      row('C', { outcome: 'unresolved', status: 'unresolved' })], ['B', 'C'])
    expect(screen.getByTestId('intent-row-B').textContent).toContain('Not converged')
    expect(screen.getByTestId('intent-row-B').textContent).toContain('not final')
    expect(screen.getByTestId('intent-row-C').textContent).toContain('Unresolved')
    expect(screen.queryByText('Converged')).toBeNull()
    expect(screen.getByTestId('intent-table').textContent).not.toContain('✓')
  })

  it('names the checks that did not run next to a converged row', async () => {
    const user = userEvent.setup()
    const checks = [{ name: 'escr', state: 'ok' as const, value: null, limit: null, note: '' },
      { name: 'harmonics', state: 'not_run' as const, value: null, limit: null, note: '' },
      { name: 'grid_readiness', state: 'not_run' as const, value: null, limit: null, note: '' }]
    await runWith(user, [row('B', { checks }), row('C')], ['B', 'C'])
    expect(screen.getByTestId('intent-notrun-B').textContent).toBe(' · 2 checks not run')
    expect(screen.getByTestId('intent-row-B').textContent).toContain('Not run')
    expect(screen.queryByTestId('intent-notrun-C')).toBeNull()
  })

  it('A shows editable autonomy, 4 h by default, and rejects a value of zero', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/^A\. /)); await user.click(screen.getByLabelText(/^B\. /))
    const h = screen.getByTestId('intent-autonomy') as HTMLInputElement
    expect(h.value).toBe('4')
    await user.clear(h); await user.type(h, '0')
    expect(screen.getByTestId('intent-autonomy-error').textContent).toContain('above zero')
    expect((screen.getByTestId('intent-run') as HTMLButtonElement).disabled).toBe(true)
    await user.clear(h); await user.type(h, '6')
    api.start.mockResolvedValue(job([row('A'), row('B')]))
    await user.click(screen.getByTestId('intent-run'))
    await waitFor(() => expect(api.start).toHaveBeenCalled())
    expect(api.start.mock.calls[0][1].intents[0]).toEqual({ intent: 'A', backup_autonomy_h: 6 })
  })

  it('D with no grid code says so and links to the grid-code section; D rows show the backend warning', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(job([row('D', { warnings: ['This grid code may not count transfer to BESS.'] }), row('B')]))
    api.defaults.mockResolvedValue({ ...HELD_NONE, grid_code: { value: null, title: null, source: 'default' },
      pst_limit: { value: null, source: null, reason: 'No grid code is set.' } })
    renderIt(null)
    await user.click(await screen.findByLabelText(/^D\. /))
    expect(screen.getByTestId('intent-d-nocode').textContent).toContain('No grid code set')
    await user.click(screen.getByTestId('intent-set-grid-code'))
    expect(ui.setSlidePanel).toHaveBeenCalledWith('campusElectrical')
    expect(ui.requestGridCodeScroll).toHaveBeenCalled()
    expect(screen.getByTestId('intent-warning-D').textContent).toContain('may not count transfer')
  })

  it('shows per-row progress and an Abort with its title while running', async () => {
    api.get.mockResolvedValue(job([], { status: 'running', progress: { A: 'running', B: 'queued' } }))
    renderIt()
    const abort = await screen.findByTestId('intent-abort')
    expect(abort.getAttribute('title')).toContain('cannot be resumed')
    expect(screen.getByTestId('intent-progress-A').textContent).toBe('A: running')
    expect(screen.getByTestId('intent-progress-B').textContent).toBe('B: queued')
  })

  it.each(['done', 'aborted', 'failed'] as const)('stops polling once the job is %s', async status => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.get.mockResolvedValue(job([row('A'), row('B')], { status }))
    renderIt()
    await screen.findByTestId('intent-row-A')
    const n = api.get.mock.calls.length
    await vi.advanceTimersByTimeAsync(7000)
    expect(api.get.mock.calls.length).toBe(n)
    vi.useRealTimers()
  })

  it('a refusal naming a field on this form links to it on its own line and focuses it', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    api.start.mockRejectedValue({ response: { status: 422, data: { detail: 'Grid ramp limit: the project has no ramp limit; enter one' } } })
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/^B\. /)); await user.click(screen.getByLabelText(/^C\. /))
    await user.click(screen.getByTestId('intent-run'))
    const link = await screen.findByTestId('intent-error-field')
    expect(link.textContent).toBe('Grid ramp limit ›')
    expect(screen.getByTestId('intent-error').contains(link)).toBe(true)
    await user.click(link)
    expect(document.activeElement).toBe(screen.getByTestId('intent-in-ramp'))
  })

  it('the island-limits refusal carries an "Island limits ›" link on its own line that opens that setting', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    api.start.mockRejectedValue({ response: { status: 422, data: { detail: 'Island limits in the optimisation: on for this project, and the intent comparison cannot include them yet.' } } })
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/^B\. /)); await user.click(screen.getByLabelText(/^C\. /))
    await user.click(screen.getByTestId('intent-run'))
    const link = await screen.findByTestId('intent-error-island')
    expect(link.textContent).toBe('Island limits ›')
    expect(screen.getByTestId('intent-error').contains(link)).toBe(true)
    await user.click(link)
    expect(nav.openIslandLimits).toHaveBeenCalledTimes(1)
  })

  it('maps each refusal label to its field, and a refusal from another panel to none', () => {
    expect(refusalField('Campus link: nowhere is not in the network')?.testId).toBe('intent-in-link')
    expect(refusalField('Load swing, oscillation amplitude and period: the project has none')?.testId).toBe('intent-in-swing')
    expect(refusalField('Point of connection: transformer rating (MVA): enter it')).toEqual(
      { label: 'Transformer rating (MVA)', testId: 'intent-pq-s_n_mva' })
    expect(refusalField('Point of connection: every value must be a number')?.testId).toBe('intent-pq-x_pu')
    expect(refusalField('Island limits in the optimisation: on for this project')).toBeNull()
  })

  it('a 409 on start shows the running job and does not start a second one', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValueOnce(null).mockResolvedValue(job([], { status: 'running', progress: { B: 'running', C: 'queued' } }))
    api.start.mockRejectedValue({ response: { status: 409, data: { detail: 'a comparison is already running' } } })
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/^B\. /)); await user.click(screen.getByLabelText(/^C\. /))
    await user.click(screen.getByTestId('intent-run'))
    await screen.findByTestId('intent-progress-B')
    expect(api.start).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('intent-error')).toBeNull()
  })

  it('shows plain labels, never a raw key, and falls back to a readable form for an unmapped key', async () => {
    const user = userEvent.setup()
    await runWith(user, [row('B', { assets: { 'store:bess': { value: 1, unit: 'MWh' }, weird_key: { value: 2, unit: 'MW' } },
      checks: [{ name: 'escr', state: 'ok', value: null, limit: null, note: '' }, { name: 'new_check', state: 'ok', value: null, limit: null, note: '' }] }), row('C')], ['B', 'C'])
    const table = (await screen.findByTestId('intent-table')).textContent ?? ''
    expect(table).toContain('Store BESS: 1 MWh')
    expect(table).toContain('Weird key: 2 MW')
    expect(table).toContain('New check')
    expect(table).not.toMatch(/store:|weird_key|new_check|escr:/)
    expect(assetLabel('q_dyn')).toBe('STATCOM dynamic')
    expect(checkLabel('grid_readiness')).toBe('Grid readiness')
  })

  it('sorts rows and the progress line A to E and shows the intent label', async () => {
    api.get.mockResolvedValue(job([row('D', { warnings: ['w'] }), row('A'), row('B')], { progress: { D: 'done', B: 'done', A: 'done' } }))
    renderIt()
    await screen.findByTestId('intent-table')
    const rowIds = screen.getAllByTestId(/^intent-row-/).map(e => e.getAttribute('data-testid'))
    expect(rowIds).toEqual(['intent-row-A', 'intent-row-B', 'intent-row-D'])
    expect(screen.getAllByTestId(/^intent-progress-/).map(e => e.textContent)).toEqual(['A: done', 'B: done', 'D: done'])
    expect(screen.getByTestId('intent-row-D').textContent).toContain('No UPS (BESS-EPS in parallel)')
    expect(screen.getByTestId('intent-row-D').textContent).toContain('Grid-code warning')
  })

  it('a row that did not converge never shows a Pass or an ok tone on its checks', async () => {
    api.get.mockResolvedValue(job([row('B', { status: 'unresolved', outcome: 'unresolved',
      checks: [{ name: 'escr', state: 'ok', value: null, limit: null, note: '' }, { name: 'pst', state: 'unresolved', value: null, limit: null, note: '' }] }), row('C')]))
    renderIt()
    const r = await screen.findByTestId('intent-row-B')
    expect(r.textContent).not.toContain('Pass')
    expect(r.textContent).toContain('OK in last round')
    expect(r.innerHTML).not.toMatch(/success/)
    expect(screen.getByTestId('intent-row-C').textContent).toContain('Pass')
  })

  describe('stored backup autonomy (AS10-AUTONOMY-2)', () => {
    const pickA = async (user: ReturnType<typeof userEvent.setup>) => { await user.click(screen.getByLabelText(/^A\. /)) }

    it('loads the stored value as the run default and sends it', async () => {
      const user = userEvent.setup()
      stored(12); api.get.mockResolvedValue(null); api.start.mockResolvedValue(job([row('A'), row('B')]))
      renderIt(); await fill(user); await pickA(user); await user.click(screen.getByLabelText(/^B\. /))
      await waitFor(() => expect((screen.getByTestId('intent-autonomy') as HTMLInputElement).value).toBe('12'))
      await user.click(screen.getByTestId('intent-run'))
      await waitFor(() => expect(api.start).toHaveBeenCalled())
      expect(api.start.mock.calls[0][1].intents[0]).toEqual({ intent: 'A', backup_autonomy_h: 12 })
    })

    it('defaults to 4 h when the project stores nothing', async () => {
      const user = userEvent.setup()
      stored(); api.get.mockResolvedValue(null)
      renderIt(); await pickA(user)
      expect((screen.getByTestId('intent-autonomy') as HTMLInputElement).value).toBe('4')
    })

    it('saves the value to the project with PATCH settings; the run keeps its own value', async () => {
      const user = userEvent.setup()
      stored(); api.get.mockResolvedValue(null)
      proj.updateSettings.mockResolvedValue({ name: 'Hub A', settings: { backup_autonomy_h: 8 } })
      renderIt(); await pickA(user)
      const input = screen.getByTestId('intent-autonomy')
      await user.clear(input); await user.type(input, '8')
      await user.click(screen.getByTestId('intent-autonomy-save'))
      await waitFor(() => expect(proj.updateSettings).toHaveBeenCalledWith('Hub A', { backup_autonomy_h: 8 }))
    })

    it.each(['0', '-1', '720.5', 'abc', ''])('refuses %j with text and a disabled Save', async v => {
      const user = userEvent.setup()
      stored(); api.get.mockResolvedValue(null)
      renderIt(); await pickA(user)
      const input = screen.getByTestId('intent-autonomy')
      await user.clear(input); if (v) await user.type(input, v)
      expect((screen.getByTestId('intent-autonomy-save') as HTMLButtonElement).disabled).toBe(true)
      expect(screen.getByTestId('intent-autonomy-error').textContent).toContain('720')
      expect(proj.updateSettings).not.toHaveBeenCalled()
    })

    it('accepts 720 and shows the server refusal inline', async () => {
      const user = userEvent.setup()
      stored(); api.get.mockResolvedValue(null)
      proj.updateSettings.mockRejectedValue({ response: { status: 409, data: { detail: 'Project has never been saved' } } })
      renderIt(); await pickA(user)
      const input = screen.getByTestId('intent-autonomy')
      await user.clear(input); await user.type(input, '720')
      await user.click(screen.getByTestId('intent-autonomy-save'))
      await waitFor(() => expect(screen.getByTestId('intent-autonomy-save-error').textContent).toContain('never been saved'))
    })
  })

  describe('prefills from the network and the grid code', () => {
    it('prefills critical load, Sk, link and Pst, still editable', async () => {
      const user = userEvent.setup()
      net.getBuses.mockResolvedValue([{ name: 'a', eh_critical: true }, { name: 'p', eh_poc: true, eh_sk_mva: 1200 }])
      net.getLoads.mockResolvedValue([{ name: 'l', bus: 'a', p_set: 42 }])
      net.getLinks.mockResolvedValue([{ name: 'z', bus0: 'g', bus1: 'h', p_min_pu: 0 }, { name: 'imp', eh_role: 'grid_import', bus0: 'g', bus1: 'h', p_min_pu: 0 }])
      api.defaults.mockResolvedValue({ ...HELD_NONE, pst_limit: { value: 0.8, source: 'the grid code', kind: 'grid_code' } })
      api.get.mockResolvedValue(null)
      renderIt()
      await waitFor(() => expect((screen.getByTestId('intent-in-critical') as HTMLInputElement).value).toBe('42'))
      expect((screen.getByTestId('intent-in-sk') as HTMLInputElement).value).toBe('1200')
      expect((screen.getByTestId('intent-in-pst') as HTMLInputElement).value).toBe('0.8')
      expect((screen.getByTestId('intent-in-link') as HTMLSelectElement).value).toBe('imp')
      expect(Array.from((screen.getByTestId('intent-in-link') as HTMLSelectElement).options).map(o => o.value)).toEqual(['', 'imp', 'z'])
      expect(screen.queryByTestId('intent-why-sk')).toBeNull()
      await user.clear(screen.getByTestId('intent-in-sk')); await user.type(screen.getByTestId('intent-in-sk'), '900')
      expect((screen.getByTestId('intent-in-sk') as HTMLInputElement).value).toBe('900')
    })

    it('leaves a field blank with its reason when the source is missing', async () => {
      api.get.mockResolvedValue(null)
      api.defaults.mockResolvedValue({ ...HELD_NONE, grid_code: { value: null, title: null, source: 'default' },
      pst_limit: { value: null, source: null, reason: 'No grid code is set.' } })
      renderIt(null)
      await waitFor(() => expect(screen.getByTestId('intent-why-critical')).toBeTruthy())
      for (const k of ['critical', 'sk', 'pst']) {
        expect((screen.getByTestId(`intent-in-${k}`) as HTMLInputElement).value).toBe('')
        expect(screen.getByTestId(`intent-why-${k}`).textContent?.length).toBeGreaterThan(10)
      }
      expect(screen.getByTestId('intent-why-link').textContent).toContain('grid import')
      expect(screen.getByTestId('intent-why-pst').textContent).toBe('No grid code is set.')
    })

    it('shows no reason while loading, and says what failed on an error', async () => {
      api.get.mockResolvedValue(null)
      net.getBuses.mockReturnValue(new Promise(() => {}))
      renderIt()
      expect(screen.queryByTestId('intent-why-sk')).toBeNull()
      cleanup()
      net.getBuses.mockRejectedValue(new Error('x'))
      renderIt()
      await waitFor(() => expect(screen.getByTestId('intent-why-sk').textContent).toContain('could not be read'))
    })
  })
})

describe('IntentComparisonSection prefill from the project (walkthrough bug 06)', () => {
  const dcNet = () => {
    net.getBuses.mockResolvedValue([{ name: 'grid', eh_poc: true, eh_sk_mva: 250 }, { name: 'it', eh_critical: true }])
    net.getLoads.mockResolvedValue([{ name: 'it', bus: 'it', p_set: 34.4937816188 }])
    net.getLinks.mockResolvedValue([{ name: 'poc', bus0: 'grid', bus1: 'hub', eh_role: 'grid_import' },
      { name: 'site_transformer', bus0: 'hub', bus1: 'it' }])
    api.defaults.mockResolvedValue(HELD_DC)
  }

  it('prefills ramp, envelope, PoC values and the PoC link, each with its source, and rounds the critical load', async () => {
    dcNet(); api.get.mockResolvedValue(null)
    renderIt(null)
    await waitFor(() => expect((screen.getByTestId('intent-in-ramp') as HTMLInputElement).value).toBe('4'))
    const val = (id: string) => (screen.getByTestId(id) as HTMLInputElement).value
    expect(val('intent-in-critical')).toBe('34.49')
    expect(val('intent-in-swing')).toBe('23.4'); expect(val('intent-in-amp')).toBe('7.2'); expect(val('intent-in-period')).toBe('2')
    expect(val('intent-in-link')).toBe('poc')
    const select = screen.getByTestId('intent-in-link') as HTMLSelectElement
    expect(select.selectedOptions[0].textContent).toBe('Grid import (point of connection)')
    expect(screen.getByTestId('intent-src-ramp').textContent).toBe('assumed')
    expect(screen.getByTestId('intent-src-swing').textContent).toBe('load envelope')
    expect(screen.getByTestId('intent-src-link').textContent).toBe('project')
    expect(val('intent-in-pst')).toBe('0.8'); expect(screen.getByTestId('intent-src-pst').textContent).toBe('assumed')
    expect(val('intent-pq-x_pu')).toBe('0.12'); expect(val('intent-pq-statcom_capital_cost')).toBe('11195')
    expect(screen.getByTestId('intent-pq-src-pf').textContent).toBe('assumed')
    expect(screen.getByTestId('intent-poc-scope').textContent).toContain('apply to this comparison only')
    expect(screen.getByTestId('intent-poc-scope').textContent).not.toContain('›')
    expect(screen.getByTestId('intent-source-key').textContent).toContain('assumed a typical value')
    expect(screen.getByTestId('intent-pq-cost-money').textContent).toBe('€\u00a011.2\u00a0k per Mvar per year')
  })

  it('leaves a PoC value the link cannot give empty, with its reason and no source chip', async () => {
    dcNet(); api.get.mockResolvedValue(null)
    api.defaults.mockResolvedValue({ ...HELD_DC, pq: { ...PQ,
      s_n_mva: { value: null, source: null, kind: 'assumed', reason: 'The connection has no finite rating; enter it.' } } })
    renderIt(null)
    await waitFor(() => expect((screen.getByTestId('intent-pq-x_pu') as HTMLInputElement).value).toBe('0.12'))
    expect((screen.getByTestId('intent-pq-s_n_mva') as HTMLInputElement).value).toBe('')
    expect(screen.getByTestId('intent-why-pq-s_n_mva').textContent).toContain('no finite rating')
    expect(screen.queryByTestId('intent-pq-src-s_n_mva')).toBeNull()
  })

  it('shows the reason under the ramp limit and each envelope field when the project has none (GK B4)', async () => {
    dcNet(); api.get.mockResolvedValue(null); api.defaults.mockResolvedValue(HELD_NONE)
    renderIt(null)
    expect((await screen.findByTestId('intent-why-ramp')).textContent).toBe('The project has no ramp limit.')
    for (const k of ['swing', 'amp', 'period'])
      expect(screen.getByTestId(`intent-why-${k}`).textContent).toBe('The project has no DC load envelope.')
  })

  it('shows the PoC values of the link the user picks, the ones the run takes on it (GK B3)', async () => {
    const user = userEvent.setup()
    dcNet(); api.get.mockResolvedValue(null)
    const site = { ...HELD_DC, pq: { ...PQ, s_n_mva: { value: 25, source: "assumed: the connection's rating", kind: 'assumed' as const } } }
    api.defaults.mockImplementation(async (_p: string, link?: string) => (link === 'site_transformer' ? site : HELD_DC))
    renderIt(null)
    await waitFor(() => expect((screen.getByTestId('intent-pq-s_n_mva') as HTMLInputElement).value).toBe('40'))
    await user.selectOptions(screen.getByTestId('intent-in-link'), 'site_transformer')
    await waitFor(() => expect((screen.getByTestId('intent-pq-s_n_mva') as HTMLInputElement).value).toBe('25'))
    expect(api.defaults).toHaveBeenCalledWith('Hub A', 'site_transformer')
    await user.selectOptions(screen.getByTestId('intent-in-link'), 'poc')
    await waitFor(() => expect((screen.getByTestId('intent-pq-s_n_mva') as HTMLInputElement).value).toBe('40'))
  })

  it('leaves untouched project values out of the request and sends only the typed PoC values', async () => {
    const user = userEvent.setup()
    dcNet(); api.get.mockResolvedValue(null); api.start.mockResolvedValue(job([row('B'), row('C')]))
    renderIt(null)
    await waitFor(() => expect((screen.getByTestId('intent-in-ramp') as HTMLInputElement).value).toBe('4'))
    await user.type(screen.getByTestId('intent-in-library'), '40,63')
    await user.clear(screen.getByTestId('intent-pq-x_pu')); await user.type(screen.getByTestId('intent-pq-x_pu'), '0.08')
    expect(screen.getByTestId('intent-pq-src-x_pu').textContent).toBe('entered here')
    await user.click(screen.getByLabelText(/^B\. /)); await user.click(screen.getByLabelText(/^C\. /))
    await user.click(screen.getByTestId('intent-run'))
    await waitFor(() => expect(api.start).toHaveBeenCalled())
    const body = api.start.mock.calls[0][1]
    expect(body.inputs).not.toHaveProperty('link'); expect(body.inputs).not.toHaveProperty('ramp_mw_per_min')
    expect(body.inputs).not.toHaveProperty('envelope')
    expect(body.inputs.pq).toEqual({ x_pu: 0.08 })
    expect(body.critical_mw).toBe(34.49)
    expect(body.grid_code).toBe('eu_rfg_dcc_ce')                      // the code the backend applies
  })

  it('says "No grid code set" only when the backend applies none', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue(null)
    renderIt(null)
    await waitFor(() => expect(api.defaults).toHaveBeenCalled())
    await user.click(screen.getByLabelText(/^D\. /))
    expect(screen.queryByTestId('intent-d-nocode')).toBeNull()
    cleanup()
    api.defaults.mockResolvedValue({ ...HELD_NONE, grid_code: { value: null, title: null, source: 'default' },
      pst_limit: { value: null, source: null, reason: 'No grid code is set.' } })
    renderIt(null)
    await user.click(screen.getByLabelText(/^D\. /))
    await waitFor(() => expect(screen.getByTestId('intent-d-nocode')).toBeTruthy())
  })

  it('says which inputs a finished run took from where', async () => {
    api.get.mockResolvedValue(job([row('B'), row('C')], { inputs: { values: {}, sources: {
      link: 'project', ramp_mw_per_min: 'Template assumption: 10%', envelope: "the DC load's dynamic envelope",
      pq: { x_pu: 'request', pf: 'assumed: a typical load power factor' }, xr: 'assumed' } } }))
    renderIt()
    await waitFor(() => expect(screen.getByTestId('intent-inputs-used')).toBeTruthy())
    const text = screen.getByTestId('intent-inputs-used').textContent
    expect(text).toContain('Grid ramp limit assumed')
    expect(text).toContain('transformer reactance entered here')
    expect(text).toContain('load power factor assumed')
  })
})

