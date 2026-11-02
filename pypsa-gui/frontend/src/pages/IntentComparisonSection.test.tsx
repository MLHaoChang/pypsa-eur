// AS-10: choosing intents runs one scenario each and renders one row per intent; a loop that did not
// converge is never a pass; intent A shows its editable autonomy; intent D shows the grid-code warning.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { IntentRow } from '../api/intentComparison'
import IntentComparisonSection from './IntentComparisonSection'

const api = vi.hoisted(() => ({ run: vi.fn() }))
vi.mock('../api/intentComparison', async () => {
  const real = await vi.importActual<typeof import('../api/intentComparison')>('../api/intentComparison')
  return { ...real, intentComparisonApi: api }
})

const row = (intent: IntentRow['intent'], over: Partial<IntentRow> = {}): IntentRow => ({
  intent, status: 'converged', outcome: 'pass', verdict: '', cost_eur: 1000, assets: { 'store:bess': { value: 320, unit: 'MWh' } },
  transformer_mva: 100, statcom_mvar: 20, checks: [{ name: 'escr', state: 'ok', value: null, limit: null, note: '' }],
  basis_note: 'same LP; UC/MIP options not reproduced', warnings: [], backup_autonomy_h: null, backup_energy_mwh: null, ...over,
})

async function fill(user: ReturnType<typeof userEvent.setup>) {
  const v: Record<string, string> = { critical: '80', link: 'poc', ramp: '10', sk: '1000', library: '50,100', swing: '20', amp: '5', period: '2' }
  for (const [k, val] of Object.entries(v)) await user.type(screen.getByTestId(`intent-in-${k}`), val)
}
function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><IntentComparisonSection name="Hub A" /></QueryClientProvider>)
}
afterEach(() => { cleanup(); api.run.mockReset() })

describe('IntentComparisonSection', () => {
  it('needs two intents, then renders one row per intent', async () => {
    const user = userEvent.setup()
    api.run.mockResolvedValue({ rows: [row('B'), row('C')] })
    renderIt()
    await fill(user)
    expect((screen.getByTestId('intent-run') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId('intent-blocked').textContent).toContain('Choose at least two intents')
    await user.click(screen.getByLabelText(/B · /)); await user.click(screen.getByLabelText(/C · /))
    await user.click(screen.getByTestId('intent-run'))
    await waitFor(() => expect(screen.getByTestId('intent-row-B')).toBeTruthy())
    expect(screen.getByTestId('intent-row-C')).toBeTruthy()
    expect(api.run.mock.calls[0][1].intents).toEqual([{ intent: 'B' }, { intent: 'C' }])
    expect(screen.getByTestId('intent-basis').textContent).toContain('UC/MIP')
  })

  it('never shows a not converged or unresolved loop as a pass', async () => {
    const user = userEvent.setup()
    api.run.mockResolvedValue({ rows: [row('B', { outcome: 'not_converged', status: 'not_converged' }),
      row('C', { outcome: 'unresolved', status: 'unresolved' })] })
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/B · /)); await user.click(screen.getByLabelText(/C · /))
    await user.click(screen.getByTestId('intent-run'))
    await waitFor(() => expect(screen.getByTestId('intent-row-B')).toBeTruthy())
    expect(screen.getByTestId('intent-row-B').textContent).toContain('Not converged')
    expect(screen.getByTestId('intent-row-C').textContent).toContain('Unresolved')
    expect(screen.queryByText('Pass')).toBeNull()
  })

  it('A shows editable autonomy defaulting to 4 h and sends it; D shows the grid-code warning', async () => {
    const user = userEvent.setup()
    api.run.mockResolvedValue({ rows: [row('A', { backup_autonomy_h: 6, backup_energy_mwh: 480 }),
      row('D', { warnings: ['This grid code may not count transfer to BESS as ride-through.'] })] })
    renderIt(); await fill(user)
    await user.click(screen.getByLabelText(/A · /)); await user.click(screen.getByLabelText(/D · /))
    const h = screen.getByTestId('intent-autonomy')
    expect((h as HTMLInputElement).value).toBe('4')
    await user.clear(h); await user.type(h, '6')
    await user.click(screen.getByTestId('intent-run'))
    await waitFor(() => expect(screen.getByTestId('intent-warning-D').textContent).toContain('ride-through'))
    expect(api.run.mock.calls[0][1].intents[0]).toEqual({ intent: 'A', backup_autonomy_h: 6 })
    expect(screen.getByTestId('intent-row-A').textContent).toContain('480')
  })
})
