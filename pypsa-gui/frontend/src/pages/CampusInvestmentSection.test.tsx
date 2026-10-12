// The investment section of the campus electrical panel (plan C9). It holds the
// panel to what the backend sent:
// - the table of what was bought, one row per need, in euro with thousands separators;
// - the unresolved needs, highlighted, each with its reason;
// - the escalation history, collapsed until opened;
// - the electrical cost per period beside the hub's own system cost, their sum and
//   the electrical share, or the electrical cost alone when the hub's cannot be had;
// - who owns the PCC switchgear;
// - the compliance table re-checked with the assets replacing "not re-checked";
// - nothing at all when the run did not invest.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { CampusState, CampusResults, CostBasis, InvestmentRow } from '../api/campusElectrical'
import CampusElectricalPanel from './CampusElectricalPanel'
import { formatEur } from './CampusInvestmentSection'

const store = vi.hoisted(() => ({ currentProject: 'Hub A' as string | null }))
vi.mock('../store/uiStore', () => ({
  useUIStore: (sel: (s: { currentProject: string | null }) => unknown) => sel({ currentProject: store.currentProject }),
}))

const api = vi.hoisted(() => ({
  state: vi.fn(), draft: vi.fn(), save: vi.fn(), run: vi.fn(), gridCodes: vi.fn(),
  library: vi.fn(), saveLibrary: vi.fn(), resetLibrary: vi.fn(),
}))
vi.mock('../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../api/campusElectrical')>('../api/campusElectrical')
  return { ...real, campusApi: api }
})

const row = (over: Partial<InvestmentRow>): InvestmentRow => ({
  need: 'transformer GRID_IMPORT', library_id: 'TR_110_20_63', kind: 'transformer', units: 1, length_km: null,
  invest_period: 2030, capex_eur: 2_150_000, opex_eur_per_a: 32_250, annualised_eur_per_a: 193_519.65,
  existing: false, status: 'chosen', reason: null, ...over,
})

const results = (over: Partial<CampusResults> = {}): CampusResults => ({
  selection: [{ period: 2030, hour: 158, reasons: ['max_consumption_mw'] }],
  transformers: [], compensation: [], short_circuit: [],
  compliance: [
    { check: 'pcc_reactive', status_as_is: 'fail', status_with_measures: 'pass', value: 16.75, limit: 13.15,
      unit: 'Mvar', worst_period: 2030, worst_hour: 158, clause: 'agreement', source: 'assumed', detail: 'recommended' },
    { check: 'pcc_voltage', status_as_is: 'pass', status_with_measures: 'not_rechecked', value: 1.0, limit: 0.9,
      unit: 'pu', worst_period: 2030, worst_hour: 0, clause: 'DCC Annex II', source: 'code', detail: 'as is detail' },
  ],
  requirement: { q_limit_mvar: 13.15, clause: 'agreement', source: 'assumed', profile: 'eu_rfg_dcc_ce', pf: 0.95,
                 p_ref_mw: 40, p_ref_from: 'peak' },
  investment: [
    row({}),
    row({ need: 'switchgear GRID', library_id: 'SG_110_31p5', kind: 'switchgear', units: 2, capex_eur: 800_000,
          annualised_eur_per_a: 68_007.31 }),
    row({ need: 'cable CBL_1', library_id: 'CB_20_240', kind: 'cable', units: 2, length_km: 1.5, existing: true,
          status: 'kept', capex_eur: 0, annualised_eur_per_a: 0 }),
    row({ need: 'reactive', library_id: null, kind: 'none', units: 0, invest_period: 2030, capex_eur: 0,
          annualised_eur_per_a: 0, status: 'not_needed' }),
  ],
  cost: [{ period: 2030, capex_eur: 2_950_000, annualised_eur_per_a: 261_526.96 }],
  compliance_invested: [
    { check: 'pcc_reactive', status_as_is: 'fail', status_with_measures: 'pass', value: 16.75, limit: 13.15,
      unit: 'Mvar', worst_period: 2030, worst_hour: 158, clause: 'agreement', source: 'assumed',
      detail: 'as is: fails', value_with_measures: 11.93, detail_with_measures: 'with the assets: inside the band' },
    { check: 'pcc_voltage', status_as_is: 'pass', status_with_measures: 'pass', value: 1.0, limit: 0.9,
      unit: 'pu', worst_period: 2030, worst_hour: 0, clause: 'DCC Annex II', source: 'code',
      detail: 'as is detail', value_with_measures: 0.9794, detail_with_measures: 'worst at MV (intact)' },
    { check: 'cable_loading', status_as_is: 'pass', status_with_measures: 'pass', value: null, limit: 100,
      unit: '%', worst_period: null, worst_hour: null, clause: 'equipment rating', source: 'assumed',
      detail: 'no cable', value_with_measures: null, detail_with_measures: 'no cable' },
  ],
  history: [],
  unresolved: [],
  scope: { pcc_switchgear: 'campus' },
  ...over,
})

const stateWith = (res: CampusResults | null, over: Partial<CampusState> = {}): CampusState => ({
  campus_yaml: 'campus:\n  pcc: {bus: GRID}\n', skipped: [], profiles: { eu_rfg_dcc_ce: 'EU RfG' },
  settings: null, results: res, stale: false,
  hub_cost: { basis: 'per_period', per_period: { '2030': 1_000_000 }, total: 10_000_000 },
  hub_cost_reason: null, ...over,
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CampusElectricalPanel /></QueryClientProvider>)
}

beforeEach(() => {
  store.currentProject = 'Hub A'
  vi.clearAllMocks()
  api.state.mockResolvedValue(stateWith(results()))
  api.gridCodes.mockResolvedValue({ shipped: {}, published: [], drafts: [], documents: [], extraction_available: false })
  api.library.mockResolvedValue({ yaml: 'x: 1', is_default: true })
})
afterEach(() => cleanup())

describe('formatEur', () => {
  it.each([
    [193519.65, '€193,520'],
    [999_999, '€999,999'],
    [1_000_000, '€1.00 M'],
    [6_890_000, '€6.89 M'],
    [-2_500_000, '-€2.50 M'],
    [0, '€0'],
  ])('formats %s as %s', (v, out) => expect(formatEur(v)).toBe(out))

  it('shows a dash for a missing or non-finite number', () => {
    expect(formatEur(null)).toBe('—')
    expect(formatEur(NaN)).toBe('—')
  })
})

describe('investment: what was bought', () => {
  it('has one row per need with the asset, units, period, capex and annualised cost', async () => {
    renderPanel()
    const t = await screen.findByTestId('invest-transformer GRID_IMPORT')
    expect(t.textContent).toContain('TR_110_20_63')
    expect(t.textContent).toContain('2030')
    expect(t.textContent).toContain('€2.15 M')
    expect(t.textContent).toContain('€193,520')
    expect(t.textContent).toContain('no')                         // existing
    const sg = screen.getByTestId('invest-switchgear GRID')
    expect(sg.textContent).toContain('SG_110_31p5')
    expect(sg.textContent).toContain('2')
    expect(sg.textContent).toContain('€800,000')
    expect(sg.textContent).toContain('€68,007')
  })

  it('marks an existing asset that was kept, and a need that needs nothing', async () => {
    renderPanel()
    const kept = await screen.findByTestId('invest-cable CBL_1')
    expect(kept.textContent).toContain('yes')
    expect(kept.textContent).toContain('kept')
    expect(kept.textContent).toContain('1.5 km')
    const none = screen.getByTestId('invest-reactive')
    expect(none.textContent).toContain('not needed')
    expect(none.textContent).toContain('—')
  })

  it('says costs are assumed placeholders', async () => {
    renderPanel()
    const section = await screen.findByTestId('campus-investment')
    expect(section.textContent).toMatch(/assumed placeholders/i)
  })

  it('says who owns the PCC switchgear: the campus', async () => {
    renderPanel()
    expect((await screen.findByTestId('invest-scope')).textContent).toBe('PCC switchgear: costed to the campus')
  })

  it('says who owns the PCC switchgear: the grid operator', async () => {
    api.state.mockResolvedValue(stateWith(results({ scope: { pcc_switchgear: 'grid_operator' } })))
    renderPanel()
    expect((await screen.findByTestId('invest-scope')).textContent)
      .toBe('PCC switchgear: owned by the grid operator, not costed')
  })

  it('shows no scope line when the backend reports none', async () => {
    api.state.mockResolvedValue(stateWith(results({ scope: null })))
    renderPanel()
    await screen.findByTestId('campus-investment')
    expect(screen.queryByTestId('invest-scope')).toBeNull()
  })

  it('is absent when the run did not invest', async () => {
    api.state.mockResolvedValue(stateWith(results({
      investment: null, cost: null, compliance_invested: null, history: null, unresolved: null, scope: null,
    })))
    renderPanel()
    await screen.findByTestId('compliance-pcc_reactive')
    expect(screen.queryByTestId('campus-investment')).toBeNull()
  })
})

// Part three, D1a: the rate and the money year the costs stand on, in one plain line.
const basis = (over: Partial<CostBasis> = {}): CostBasis => ({
  discount_rate: 0.07, discount_rate_from: 'asset library', price_year: 2026, price_year_from: 'asset library',
  library_price_year: 2026, price_year_mismatch: false, currency: 'EUR', ...over,
})

describe('investment: the cost basis', () => {
  it('says the rate and the price year, and where each came from', async () => {
    api.state.mockResolvedValue(stateWith(results({ cost_basis: basis() })))
    renderPanel()
    expect((await screen.findByTestId('invest-cost-basis')).textContent)
      .toBe('Annualised at 7 % (from asset library); costs in 2026 money (from asset library).')
    expect(screen.queryByTestId('invest-price-year-mismatch')).toBeNull()
  })

  it('names the project as the source of the rate and the year', async () => {
    api.state.mockResolvedValue(stateWith(results({ cost_basis: basis({
      discount_rate: 0.035, discount_rate_from: 'project solver config',
      price_year_from: 'project finance inputs',
    }) })))
    renderPanel()
    expect((await screen.findByTestId('invest-cost-basis')).textContent)
      .toBe('Annualised at 3.5 % (from project solver config); costs in 2026 money (from project finance inputs).')
  })

  it('warns when the project states another money year, and says no escalation is applied', async () => {
    api.state.mockResolvedValue(stateWith(results({ cost_basis: basis({
      discount_rate_from: 'project solver config', price_year: 2024, price_year_from: 'project finance inputs',
      price_year_mismatch: true,
    }) })))
    renderPanel()
    const line = await screen.findByTestId('invest-cost-basis')
    expect(line.textContent).toContain('costs in 2026 money (from asset library)')   // what the library's costs are in
    const warn = screen.getByTestId('invest-price-year-mismatch')
    expect(warn.textContent).toContain('2024')
    expect(warn.textContent).toContain('2026')
    expect(warn.textContent).toMatch(/no escalation/i)
  })

  it('shows no line when the backend sent no basis', async () => {
    api.state.mockResolvedValue(stateWith(results({ cost_basis: null })))
    renderPanel()
    await screen.findByTestId('campus-investment')
    expect(screen.queryByTestId('invest-cost-basis')).toBeNull()
  })
})

describe('investment: unresolved needs and history', () => {
  it('highlights an unresolved need with its reason', async () => {
    const reason = 'campus_voltage: 0.88 pu; a reactive escalation did not reduce the excursion; needs a tap change'
    api.state.mockResolvedValue(stateWith(results({
      investment: [row({}), row({ need: 'reactive', library_id: null, kind: 'none', units: 0, capex_eur: 0,
                                  annualised_eur_per_a: 0, status: 'unresolved', reason })],
      unresolved: [{ need: 'reactive', reason }],
    })))
    renderPanel()
    const box = await screen.findByTestId('unresolved')
    expect(box.textContent).toContain('reactive')
    expect(box.textContent).toContain('needs a tap change')
    expect(box.getAttribute('role')).toBe('alert')
    expect(screen.getByTestId('invest-reactive').textContent).toContain('unresolved')
    expect(screen.getByTestId('invest-reactive').className).toMatch(/err|danger|red/)
    expect(screen.getByTestId('invest-transformer GRID_IMPORT').className).not.toMatch(/err|danger|red/)
  })

  it('shows no unresolved box when every need is met', async () => {
    renderPanel()
    await screen.findByTestId('campus-investment')
    expect(screen.queryByTestId('unresolved')).toBeNull()
  })

  it('keeps the escalation history collapsed, with its count, until opened', async () => {
    api.state.mockResolvedValue(stateWith(results({
      history: [{ iteration: 1, need: 'transformer GRID_IMPORT', from: 'TR_110_20_40', to: 'TR_110_20_63',
                  check: 'transformer_loading', detail: '104 % at h158' }],
    })))
    renderPanel()
    const details = (await screen.findByTestId('invest-history')) as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(within(details).getByText(/Escalation history \(1\)/)).toBeTruthy()
    await userEvent.click(within(details).getByText(/Escalation history/))
    expect(details.open).toBe(true)
    expect(details.textContent).toContain('TR_110_20_40')
    expect(details.textContent).toContain('TR_110_20_63')
    expect(details.textContent).toContain('104 % at h158')
  })

  it('says there was no escalation when the history is empty', async () => {
    renderPanel()
    expect((await screen.findByTestId('invest-history')).textContent).toMatch(/Escalation history \(0\)/)
    expect(screen.getByTestId('invest-history').textContent).toMatch(/no escalation/i)
  })
})

describe('investment: cost per period beside the hub', () => {
  it('shows the electrical annualised cost, the hub system cost, their sum and the electrical share', async () => {
    renderPanel()
    const r = await screen.findByTestId('cost-2030')
    expect(r.textContent).toContain('€2.95 M')                     // capex invested
    expect(r.textContent).toContain('€261,527')                    // electrical, annualised
    expect(r.textContent).toContain('€1.00 M')                     // hub
    expect(r.textContent).toContain('€1.26 M')                     // sum
    expect(r.textContent).toContain('20.7 %')                      // 261527 / 1261527
  })

  it('uses the hub total, labelled as solved, for a single-period project', async () => {
    api.state.mockResolvedValue(stateWith(results(), {
      hub_cost: { basis: 'single_period', per_period: null, total: 2_000_000 },
    }))
    renderPanel()
    const r = await screen.findByTestId('cost-2030')
    expect(r.textContent).toContain('€2.00 M')
    expect(r.textContent).toContain('11.6 %')                      // 261527 / 2261527
    expect(screen.getByTestId('campus-investment').textContent).toMatch(/single period.*cost as solved/i)
  })

  it('shows the total alone, labelled, when the project has several periods but only a total', async () => {
    api.state.mockResolvedValue(stateWith(results({
      cost: [{ period: 2030, capex_eur: 1, annualised_eur_per_a: 100_000 }, { period: 2040, capex_eur: 2, annualised_eur_per_a: 150_000 }],
    }), { hub_cost: { basis: 'single_period', per_period: null, total: 5_000_000 } }))
    renderPanel()
    const section = await screen.findByTestId('campus-investment')
    expect(section.textContent).toMatch(/no per-period split/i)
    expect(section.textContent).toContain('€5.00 M')
    expect(screen.getByTestId('cost-2030').textContent).not.toContain('%')
    expect(screen.getByTestId('cost-2040').textContent).toContain('€150,000')
  })

  it('shows a dash for a period the hub has no figure for', async () => {
    api.state.mockResolvedValue(stateWith(results({
      cost: [{ period: 2030, capex_eur: 1, annualised_eur_per_a: 100_000 }, { period: 2040, capex_eur: 2, annualised_eur_per_a: 150_000 }],
    }), { hub_cost: { basis: 'per_period', per_period: { '2030': 900_000 }, total: 1 } }))
    renderPanel()
    expect((await screen.findByTestId('cost-2030')).textContent).toContain('10.0 %')
    const r = screen.getByTestId('cost-2040')
    expect(r.textContent).toContain('€150,000')
    expect(r.textContent).not.toContain('%')
  })

  it('says why the hub cost is missing and shows the electrical cost alone', async () => {
    api.state.mockResolvedValue(stateWith(results(), { hub_cost: null, hub_cost_reason: 'no cost statistics' }))
    renderPanel()
    const r = await screen.findByTestId('cost-2030')
    expect(r.textContent).toContain('€261,527')
    expect(r.textContent).not.toContain('%')
    expect(screen.getByTestId('campus-investment').textContent).toMatch(/hub system cost is not available: no cost statistics/i)
  })

  it('labels the cost as reported alongside, not part of the hub optimisation', async () => {
    renderPanel()
    expect((await screen.findByTestId('campus-investment')).textContent)
      .toMatch(/reported alongside the hub's cost; the hub's optimisation is unchanged/i)
  })
})

describe('compliance with the investment', () => {
  it('replaces "not re-checked" with the re-solved result and value', async () => {
    renderPanel()
    const v = await screen.findByTestId('compliance-pcc_voltage')
    expect(v.textContent).not.toContain('not re-checked')
    expect(v.textContent).toContain('0.979')                       // value_with_measures
    expect(v.textContent).toContain('worst at MV (intact)')
    const q = screen.getByTestId('compliance-pcc_reactive')
    expect(q.textContent).toContain('11.93')
    expect(q.textContent).toContain('with the assets: inside the band')
    expect(screen.getByTestId('compliance-cable_loading').textContent).toContain('Cable loading')
  })

  it('keeps the recommendation and "not re-checked" when the run did not invest', async () => {
    api.state.mockResolvedValue(stateWith(results({
      investment: null, cost: null, compliance_invested: null, history: null, unresolved: null, scope: null,
    })))
    renderPanel()
    const v = await screen.findByTestId('compliance-pcc_voltage')
    expect(v.textContent).toContain('not re-checked')
    expect(v.textContent).toContain('as is detail')
    await waitFor(() => expect(screen.queryByTestId('compliance-cable_loading')).toBeNull())
  })

  it('says in the header that the with-assets column is an AC re-solve', async () => {
    renderPanel()
    expect((await screen.findByText(/With the assets \(AC re-solved\)/))).toBeTruthy()
  })
})
