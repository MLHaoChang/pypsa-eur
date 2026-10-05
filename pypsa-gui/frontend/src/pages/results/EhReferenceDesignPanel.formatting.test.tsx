// Bug 1 (guided-mode spec §2.4): the DtC stress / planning tables printed
// raw floats (`String(v)` → "59558.514428810326"). Numeric cells now go
// through `cellNum(v, kind)`: MWh via `fmtEnergy(v, 2)`, € via
// `fmtCurrency(v, 2)`, anything else as before.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { cellNum, EhReferenceDesignPanel } from './EhReferenceDesignPanel'
import { fmtCurrency, fmtEnergy, fmtMwhNumber, fmtPower } from './shared'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  const none = () => vi.fn().mockResolvedValue(null)
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: none(), getEhReferenceDesign: none(), getEhRedundancy: none(),
      getEhLevers: none(), getEhDtc: vi.fn(), getEhDtcPlanning: vi.fn(),
      getEhReadiness: none(), getEhTemplate: none(),
    },
  }
})

const RAW = 59558.514428810326

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getEhDtc).mockReset().mockResolvedValue({
    contingencies: [{ contingency: 'grid_import', status: 'ok',
      critical_unserved_mwh: RAW, noncritical_unserved_mwh: 0.25 }],
  } as never)
  vi.mocked(resultsApi.getEhDtcPlanning).mockReset().mockResolvedValue({
    contingencies: [{ contingency: 'grid_import', status: 'ok',
      cost_at_target_eur: 1_234_567.8, built_p_nom_mw: 40 }],
  } as never)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('cellNum', () => {
  it('formats MWh and € kinds and keeps the rest', () => {
    expect(cellNum(RAW, 'mwh')).toBe(fmtEnergy(RAW, 2))
    expect(cellNum(590_000, 'eur')).toBe(fmtCurrency(590_000, 2))
    expect(cellNum(40, 'plain')).toBe('40')
    expect(cellNum(null, 'mwh')).toBe('—')
    expect(cellNum('n/a', 'mwh')).toBe('n/a')
  })
})

describe('DtC tables', () => {
  it('render the unserved MWh through fmtEnergy', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    await user.click(screen.getByTestId('eh-reference-design-toggle'))
    const row = await screen.findByTestId('eh-dtc-stress-row-grid_import')
    const tds = Array.from(row.querySelectorAll('td')).map(td => td.textContent)
    expect(tds[2]).toBe(fmtEnergy(RAW, 2))
    expect(tds[2]).toBe('59.56 GWh')
    expect(tds[3]).toBe(fmtEnergy(0.25, 2))
    const plan = await screen.findByTestId('eh-dtc-planning-row-grid_import')
    expect(plan.textContent).not.toMatch(/1234567/)
  })
})

// Bug 1 remainder: every other MWh / MW value in the panel's tables went out
// as a bare float (`toFixed(2)` / `String(v)`) — ΔEUE 315605.35 in the
// class-B table, frontier ENS, redundancy ENS, multi-energy MWh, built MW.
describe('remaining EH panel numbers (bug 1 remainder)', () => {
  const REPORT = {
    archetype: 'weak_flexible', pack_hash: 'h', assumptions_hash: 'a',
    completeness: { frontier: 'ok', fmea_top: 'ok', multi_energy: 'ok' },
    sections: {
      frontier: { status: 'ok', note: null, payload: { points: [
        { target_permyriad: 10, status: 'ok',
          point: { total_system_cost_eur: 1500, achieved_ens_mwh: 12345.678 } }] } },
      fmea_top: { status: 'ok', note: null, payload: { rows: [
        { mode_id: 'l', name: 'site_transformer', criticality_eur_per_year: 7.9e6,
          occurrence_per_year: 0.61, severity_eur: 1e7, delta_eue_mwh: 315605.35 }] } },
      multi_energy: { status: 'ok', note: null, payload: {
        ens_by_carrier_mwh: { hydrogen: 80.5 }, ens_by_load_mwh: { offtake: 0.25 } } },
    },
  }

  async function open() {
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(REPORT as never)
    vi.mocked(resultsApi.getEhRedundancy).mockResolvedValue({ options: [
      { scenario_id: 'n1', status: 'ok', cost_at_target_eur: 50, achieved_ens_mwh: 4321.5,
        meets_target: true }] } as never)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><EhReferenceDesignPanel /></QueryClientProvider>)
    await userEvent.setup().click(screen.getByTestId('eh-reference-design-toggle'))
    await screen.findByTestId('eh-report')
  }

  it('class-B ΔEUE, frontier ENS, redundancy ENS and multi-energy go through fmtEnergy', async () => {
    await open()
    const panel = screen.getByTestId('eh-reference-design-panel')
    await screen.findByTestId('eh-redundancy-row-n1')
    const text = panel.textContent ?? ''
    // P31 C3: the ΔEUE column carries its unit in the header, so its cell
    // is the bare MWh number (asserted below), not fmtEnergy's GWh string.
    expect(text).not.toContain('315605.35')
    expect(text).toContain(fmtEnergy(12345.678, 2))
    expect(text).not.toContain('12345.68')
    expect(text).toContain(fmtEnergy(4321.5, 2))
    expect(text).not.toMatch(/4321\.5(?!\d)/)
    expect(text).toContain(fmtEnergy(80.5, 2))
    expect(text).toContain(fmtEnergy(0.25, 2))            // 0.250 MWh (P31 C3)
    expect(text).not.toContain('kWh')
  })

  it('the class-B ΔEUE header names MWh and every cell is a bare MWh number (P31 C3)', async () => {
    await open()
    const row = await screen.findByTestId('eh-fmea-top-row-1')
    const table = row.closest('table')!
    const heads = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent)
    expect(heads[heads.length - 1]).toBe('ΔEUE (MWh)')
    const tds = Array.from(row.querySelectorAll('td')).map(td => td.textContent)
    expect(tds[tds.length - 1]).toBe(fmtMwhNumber(315605.35))
    expect(tds[tds.length - 1]).not.toMatch(/Wh/)
  })

  it('DtC planning built MW goes through fmtPower', async () => {
    vi.mocked(resultsApi.getEhDtcPlanning).mockResolvedValue({
      contingencies: [{ contingency: 'x', status: 'ok', cost_at_target_eur: 1, built_p_nom_mw: 40.00000123 }],
    } as never)
    await open()
    const row = await screen.findByTestId('eh-dtc-planning-row-x')
    expect(row.textContent).toContain(fmtPower(40.00000123, 2))
    expect(row.textContent).not.toContain('40.00000123')
  })
})
