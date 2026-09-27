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
import { fmtCurrency, fmtEnergy } from './shared'

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
