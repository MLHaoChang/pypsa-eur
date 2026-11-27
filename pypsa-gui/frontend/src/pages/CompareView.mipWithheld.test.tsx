// Compare → Economics with an LP scenario beside an integer (MIP) one. On the
// MIP side the backend withholds revenue and storage charge cost (and opex +
// LCOE of a carrier holding one) as null with unavailable_reason
// "mip_duals_not_marginal". Those cells read "Not available (integer solve)"
// — never 0, "—" or NaN — and the Δ against them is withheld too; the LP
// side keeps its numbers.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/projects', () => ({
  projectsApi: { resultsSummary: vi.fn(), compareState: vi.fn() },
}))

import { projectsApi } from '../api/projects'
import { EconomicsTab } from './CompareView'

const WITHHELD = 'Not available (integer solve)'
const pv = (total: number) => ({ total, by_period: {} })

const lp = (project: string) => ({
  project, has_solve: true, periods: [],
  economics: {
    available: true,
    by_carrier: {
      gas: {
        revenue_meur: pv(12), opex_meur: pv(5), gen_cost_meur: pv(5), storage_charge_cost_meur: pv(0),
        curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(3), dispatch_gwh: pv(100),
        lcoe_eur_per_mwh: pv(80),
      },
      battery: {
        revenue_meur: pv(4), opex_meur: pv(2), gen_cost_meur: pv(0), storage_charge_cost_meur: pv(2),
        curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(1), dispatch_gwh: pv(10),
        lcoe_eur_per_mwh: pv(300),
      },
    },
    per_asset_lcoh: [],
  },
})

const mip = (project: string) => ({
  project, has_solve: true, periods: [],
  economics: {
    available: true,
    unavailable_reason: 'mip_duals_not_marginal',
    by_carrier: {
      gas: {
        revenue_meur: null, opex_meur: pv(5), gen_cost_meur: pv(5), storage_charge_cost_meur: null,
        curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(3), dispatch_gwh: pv(100),
        lcoe_eur_per_mwh: pv(80),
      },
      battery: {
        revenue_meur: null, opex_meur: null, gen_cost_meur: pv(0), storage_charge_cost_meur: null,
        curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(1), dispatch_gwh: pv(10),
        lcoe_eur_per_mwh: null,
      },
    },
    per_asset_lcoh: [],
  },
})

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <EconomicsTab a="alpha" b="beta" />
    </QueryClientProvider>,
  )
}

// Rows of one metric, in carrier order (battery, gas): [metric, A, B, Δ] cells.
const metricRows = (label: string) => screen.getAllByText(label, { selector: 'td' })
  .map(td => [...td.closest('tr')!.querySelectorAll('td')].slice(-4).map(c => c.textContent ?? ''))

describe('Compare economics: LP scenario beside an integer (MIP) one', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve((name === 'alpha' ? lp(name) : mip(name)) as never),
    )
  })

  it('keeps the LP numbers and withholds the MIP revenue and its Δ', async () => {
    renderTab()
    await screen.findAllByText('Revenue', { selector: 'td' })
    for (const [, a, b, d] of metricRows('Revenue')) {
      expect(a).toMatch(/M€/)
      expect(b).toBe(WITHHELD)
      expect(d).toBe(WITHHELD)
    }
    expect(document.body.textContent).not.toContain('NaN')
  })

  it('withholds OPEX, storage charge cost and LCOE of the carrier holding a charge cost', async () => {
    renderTab()
    await screen.findAllByText('Revenue', { selector: 'td' })
    const [batteryOpex, gasOpex] = metricRows('OPEX (total)')
    expect(batteryOpex[1]).toBe('2.00 M€')
    expect(batteryOpex[2]).toBe(WITHHELD)
    expect(batteryOpex[3]).toBe(WITHHELD)
    // gas holds no charge cost: its OPEX stays a number on both sides.
    expect(gasOpex[2]).toBe('5.00 M€')
    const [batteryLcoe, gasLcoe] = metricRows('Carrier LCOE (cash cost)')
    expect(batteryLcoe[2]).toBe(WITHHELD)
    expect(gasLcoe[2]).toMatch(/€\/MWh/)
    const charge = metricRows('Storage charge cost')
    expect(charge.length).toBeGreaterThan(0)
    expect(charge[0][2]).toBe(WITHHELD)
    expect(charge[0][2]).not.toMatch(/^0/)
  })

  it('words the MIP side of the per-asset LCOH table, never a dash', async () => {
    const lcoh = {
      name: 'electrolysis', carrier: 'H2 electrolysis', output_gwh: pv(7), lcoh_eur_per_mwh: pv(90),
      capex_meur: pv(1), opex_meur: pv(0.1), input_cost_meur: pv(0.5),
    }
    vi.mocked(projectsApi.resultsSummary).mockImplementation((name: string) => {
      const s = name === 'alpha' ? lp(name) : mip(name)
      if (name === 'alpha') (s.economics as { per_asset_lcoh: unknown[] }).per_asset_lcoh = [lcoh]
      return Promise.resolve(s as never)
    })
    renderTab()
    const cell = await screen.findByText('electrolysis')
    const cells = [...cell.closest('tr')!.querySelectorAll('td')].map(c => c.textContent ?? '')
    expect(cells[3]).toBe(WITHHELD)  // B output
    expect(cells[5]).toBe(WITHHELD)  // B LCOH
    expect(cells[6]).toBe(WITHHELD)  // Δ
    expect(cells).not.toContain('—')
  })

  it('says why when both sides withhold the per-asset LCOH table', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(mip(name) as never),
    )
    renderTab()
    expect(await screen.findByText('Per-asset levelised cost (LCOH / LCOE)')).toBeTruthy()
    expect(screen.queryByText('No assets match the selected carrier.')).toBeNull()
  })

  it('renders an LP pair with no withheld text', async () => {
    vi.mocked(projectsApi.resultsSummary).mockImplementation(
      (name: string) => Promise.resolve(lp(name) as never),
    )
    renderTab()
    await screen.findAllByText('Revenue', { selector: 'td' })
    expect(screen.queryByText(WITHHELD)).toBeNull()
  })
})

// GK B2 F1 (S1): two spellings of one carrier merge into one row (`canonicaliseCarrier`
// lowercases and trims). A withheld part on either side of the merge withholds the
// merged figure: never the other spelling's number alone (a partial sum) or 0.
describe('Compare economics: a merged carrier with one withheld spelling', () => {
  const bucket = (revenue: number | null, opex: number | null) => ({
    revenue_meur: revenue === null ? null : pv(revenue), opex_meur: opex === null ? null : pv(opex),
    gen_cost_meur: pv(5), storage_charge_cost_meur: null,
    curtailment_cost_meur: pv(0), lost_load_cost_meur: pv(0), capex_meur: pv(3), dispatch_gwh: pv(100),
    lcoe_eur_per_mwh: null,
  })
  const side = (project: string, by_carrier: Record<string, unknown>, withheld: boolean) => ({
    project, has_solve: true, periods: [],
    economics: {
      available: true, ...(withheld ? { unavailable_reason: 'mip_duals_not_marginal' } : {}),
      by_carrier, per_asset_lcoh: [],
    },
  })

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(projectsApi.resultsSummary).mockImplementation((name: string) => Promise.resolve((
      name === 'alpha'
        ? side(name, { gas: bucket(12, 5) }, false)
        // revenue: a number, then a withheld part; opex: a withheld part, then a number.
        : side(name, { gas: bucket(7, null), 'Gas ': bucket(null, 4) }, true)
    ) as never))
  })

  it('reads the merged revenue and OPEX as not available, in either merge order', async () => {
    renderTab()
    await screen.findAllByText('Revenue', { selector: 'td' })
    for (const label of ['Revenue', 'OPEX (total)']) {
      const rows = metricRows(label)
      expect(rows, label).toHaveLength(1)
      const [, a, b, d] = rows[0]
      expect(a, label).toMatch(/M€/)
      expect(b, label).toBe(WITHHELD)
      expect(d, label).toBe(WITHHELD)
    }
    expect(document.body.textContent).not.toMatch(/7\.00 M€|4\.00 M€|NaN/)
  })
})
