// DC 03: the backup section shows only on a data-centre project; a mode click
// stores it and a refusal is shown under the control with the mode unchanged;
// the assessment shows both legs' verdicts, the sizing and the cost delta.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { DcBackupAssessment } from '../api/campusElectrical'
import DcBackupSection from './DcBackupSection'

const campus = vi.hoisted(() => ({ setDcBackup: vi.fn(), assessDcBackup: vi.fn() }))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: campus }
})
const results = vi.hoisted(() => ({ getEhTemplate: vi.fn() }))
vi.mock('../api/simulation', () => ({ resultsApi: results }))
const ui = vi.hoisted(() => ({ uiMode: 'expert' as 'expert' | 'guided' }))
vi.mock('../store/uiStore', () => ({ useUIStore: (sel: (s: typeof ui) => unknown) => sel(ui) }))

const meta = (mode?: string) => ({ id: 'eh_datacenter', dc_load: { spec: mode ? { backup: { mode } } : {} } })
const proof = (verdict: 'pass' | 'fail' | 'not_shown', reason: string | null = null) => ({
  verdict, reason, strings_in_service: 7, window_unserved_mwh: verdict === 'fail' ? 3.2 : 0, on_grid_unserved_mwh: 0,
})
const assessment = (over: Partial<DcBackupAssessment> = {}): DcBackupAssessment => ({
  mode: 'bess', verdict: 'pass',
  window: { first: '2030-01-14 18:00', hours: 4, snapshots: 4, energy_mwh: 110.4 },
  sizing: { strings_n: 7, strings: 7, string_mw: 5, redundancy: 'N+1', e_mwh: 145.8, e_usable_mwh_per_string: 16, shortfall_mw: 27.6,
    critical_peak_mw: 27.6, firm_genset_mw: 0 },
  proofs: { genset: proof('pass'), bess: proof('pass') },
  cost: { years: 20, discount_rate: 0.07, currency: 'EUR', money_year: 2025, delta: -1_250_000,
    npv_genset: 9e6, npv_chosen: 7.75e6,
    lines: [{ leg: 'bess', line: 'power_capex_and_repurchase', unit: 'EUR', npv: 3e6 }],
    inputs: [{ key: 'fade_per_year', value: 0.02, unit: '1/y', assumed: true }] },
  notes: [],
  basis: { network_saved_at: '2026-11-06T01:00:00+00:00', network_sha256: 'abc', not_reproduced: ['x'] }, ...over,
})

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\u00a0/g, ' ')

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><DcBackupSection name="DC A" /></QueryClientProvider>)
}

describe('DcBackupSection', () => {
  beforeEach(() => { vi.resetAllMocks(); ui.uiMode = 'expert' })
  afterEach(cleanup)

  it('shows nothing on a project without a dc_load', async () => {
    results.getEhTemplate.mockResolvedValue({ id: 'eh_ipp_hybrid' })
    renderIt()
    await waitFor(() => expect(results.getEhTemplate).toHaveBeenCalled())
    expect(screen.queryByTestId('dc-backup')).toBeNull()
  })

  it('stores a mode on click and shows a refusal with the mode kept', async () => {
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue(meta())
    campus.setDcBackup.mockRejectedValue({ response: { status: 422, data: { detail: {
      error_kind: 'dc_backup_invalid', message: 'peak: the gensets cover the peak above the import' } } } })
    renderIt()
    await user.click(await screen.findByRole('button', { name: 'Batteries' }))
    expect(campus.setDcBackup).toHaveBeenCalledWith('DC A', { mode: 'bess' })
    expect((await screen.findByTestId('dc-backup-refusal')).textContent).toContain('peak')
    expect(screen.getByTestId('dc-backup-mode').getAttribute('data-mode')).toBe('genset')
  })

  it('shows both verdicts, the sizing and a saving', async () => {
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValue(meta('bess'))
    campus.assessDcBackup.mockResolvedValue(assessment({ proofs: {
      genset: proof('pass'), bess: proof('not_shown', 'the unserved energy could not be read') } }))
    renderIt()
    await user.click(await screen.findByTestId('dc-backup-assess'))
    await screen.findByTestId('dc-backup-result')
    expect((screen.getByTestId('dc-backup-proof-genset')).textContent).toContain('Pass')
    expect((screen.getByTestId('dc-backup-proof-bess')).textContent).toContain('could not be read')
    expect(text(screen.getByTestId('dc-backup-proof-bess'))).toContain(': the unserved')
    expect(text(screen.getByTestId('dc-backup-sizing'))).toContain('7 strings of 5 MW (one spare string)')
    expect(text(screen.getByTestId('dc-backup-cost'))).toContain('Cost over 20 years vs gensets: saves € 1.3 m')
    expect(text(screen.getByTestId('dc-backup-cost'))).not.toMatch(/[+−-]€/)
    expect(text(screen.getByTestId('dc-backup-assumed'))).toContain('fade per year 0.02 1/y assumed')
    expect(screen.getByTestId('dc-backup-lines').textContent).toContain('Power capex and repurchase')
    expect(text(screen.getByTestId('dc-backup-sizing'))).toContain('112 MWh usable')
    expect(text(screen.getByTestId('dc-backup-basis'))).toContain('2026-11-06 01:00 UTC')
  })

  it('says when the delta cannot be priced, and Guided drops the lines', async () => {
    const user = userEvent.setup()
    ui.uiMode = 'guided'
    results.getEhTemplate.mockResolvedValue(meta('hybrid'))
    const a = assessment({ mode: 'hybrid' })
    campus.assessDcBackup.mockResolvedValue({ ...a, proofs: { genset: proof('pass'), hybrid: proof('fail') },
      cost: { ...a.cost, delta: null } })
    renderIt()
    await user.click(await screen.findByTestId('dc-backup-assess'))
    expect((await screen.findByTestId('dc-backup-cost')).textContent).toContain('cannot be priced')
    expect((screen.getByTestId('dc-backup-proof-hybrid')).textContent).toContain('Fail')
    expect(screen.queryByTestId('dc-backup-lines')).toBeNull()
    expect(screen.getByTestId('dc-backup-assumed')).toBeTruthy()
  })

  it('says costs more, and marks the result out of date once the stored mode changes', async () => {
    const user = userEvent.setup()
    results.getEhTemplate.mockResolvedValueOnce(meta('bess')).mockResolvedValue(meta('hybrid'))
    campus.setDcBackup.mockResolvedValue({ mode: 'hybrid' })
    const a = assessment()
    campus.assessDcBackup.mockResolvedValue({ ...a, cost: { ...a.cost, delta: 400_000 } })
    renderIt()
    await user.click(await screen.findByTestId('dc-backup-assess'))
    expect(text(await screen.findByTestId('dc-backup-cost'))).toContain('costs € 400 k more')
    expect(screen.queryByTestId('dc-backup-stale')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Hybrid' }))
    expect(text(await screen.findByTestId('dc-backup-stale'))).toContain('Out of date')
    expect(screen.getByTestId('dc-backup-result').getAttribute('data-stale')).toBe('true')
  })
})
