// AS-10: choosing intents starts one background job and renders one row per intent; a loop that did not
// converge is never a pass; a converged row names its not-run checks; intent A has an editable autonomy;
// intent D says when no grid code is set.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { IntentJob, IntentRow } from '../api/intentComparison'
import IntentComparisonSection from './IntentComparisonSection'

const api = vi.hoisted(() => ({ start: vi.fn(), get: vi.fn(), abort: vi.fn() }))
vi.mock('../api/intentComparison', async () => {
  const real = await vi.importActual<typeof import('../api/intentComparison')>('../api/intentComparison')
  return { ...real, intentComparisonApi: api }
})
const ui = vi.hoisted(() => ({ setSlidePanel: vi.fn() }))
vi.mock('../store/uiStore', () => ({ useUIStore: (sel: (s: typeof ui) => unknown) => sel(ui) }))
vi.mock('../layout/flowNavigation', () => ({ withReturnFrom: (jump: () => void) => jump() }))

const row = (intent: IntentRow['intent'], over: Partial<IntentRow> = {}): IntentRow => ({
  intent, status: 'converged', outcome: 'pass', verdict: '', cost_eur: 1000, assets: { 'store:bess': { value: 320, unit: 'MWh' } },
  transformer_mva: 100, statcom_mvar: 20, checks: [{ name: 'escr', state: 'ok', value: null, limit: null, note: '' }],
  basis_note: 'same LP; UC/MIP options not reproduced', warnings: [], backup_autonomy_h: null, backup_energy_mwh: null, ...over,
})
const job = (rows: IntentRow[], over: Partial<IntentJob> = {}): IntentJob => ({
  status: 'done', progress: Object.fromEntries(rows.map(r => [r.intent, 'done'])), rows, error: null, ...over,
})

async function fill(user: ReturnType<typeof userEvent.setup>) {
  const v: Record<string, string> = { critical: '80', link: 'poc', ramp: '10', sk: '1000', library: '50,100', swing: '20', amp: '5', period: '2' }
  for (const [k, val] of Object.entries(v)) await user.type(screen.getByTestId(`intent-in-${k}`), val)
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
afterEach(() => { cleanup(); Object.values(api).forEach(m => m.mockReset()) })

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
    expect(screen.getByTestId('intent-notrun-B').textContent).toBe('2 checks not run')
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
    renderIt(null)
    await user.click(await screen.findByLabelText(/^D\. /))
    expect(screen.getByTestId('intent-d-nocode').textContent).toContain('No grid code set')
    await user.click(screen.getByTestId('intent-set-grid-code'))
    expect(ui.setSlidePanel).toHaveBeenCalledWith('campusElectrical')
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
})
