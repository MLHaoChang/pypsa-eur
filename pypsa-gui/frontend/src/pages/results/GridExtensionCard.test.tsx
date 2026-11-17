// "Grid extension vs island" (homer-parity 03d): each state of the card, in
// words. Hidden unless the site is an island; outside local mode it keeps its
// title with the reason; the first press creates (or reuses) the bare study and
// runs the 03a comparison; running, unsaved, ranked, out of date; every result
// status of the break-even route, and a refusal in words, never a code.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { DecisionStudy, GridExtensionResult, SupplyArchitecturesRecord } from '../../api/decisionStudies'
import { useUIStore } from '../../store/uiStore'
import { ERROR_COPY } from '../../utils/decisionVocabulary'

const authMode = vi.hoisted(() => ({ authEnabled: false }))
vi.mock('../../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
const goal = vi.hoisted(() => ({ archetype: 'off_grid', isPending: false }))
vi.mock('../../hooks/useReliabilityGoal', () => ({ useReliabilityGoal: () => goal }))
const api = vi.hoisted(() => ({
  list: vi.fn(), create: vi.fn(), get: vi.fn(), library: vi.fn(), patchSettings: vi.fn(), putLedger: vi.fn(),
  startSupplyArchitectures: vi.fn(), supplyArchitectures: vi.fn(), abortSupplyArchitectures: vi.fn(),
  gridExtensionBreakEven: vi.fn(),
}))
vi.mock('../../api/decisionStudies', async (orig) => ({
  ...(await orig<typeof import('../../api/decisionStudies')>()), decisionStudiesApi: api,
}))
const undoInfo = vi.hoisted(() => vi.fn())
vi.mock('../../api/network', async (orig) => {
  const actual = await orig<typeof import('../../api/network')>()
  return { ...actual, networkApi: { ...actual.networkApi, undoInfo } }
})

import GridExtensionCard, { GRID_EXTENSION_WORDS as W } from './GridExtensionCard'

function axErr(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } })
}

const LEDGER = { ledger_version: 'v', honesty_notes: [], rows: [
  { key: 'discount_rate', value: 0.07 }, { key: 'battery_storage_lifetime_years', value: 25 },
] }
const STUDY = {
  study_id: 'ge1', name: 'Grid extension vs island', question_id: 'grid_extension',
  currency_year: 2025, ledger: LEDGER,
} as unknown as DecisionStudy
const BARE = { ...STUDY, currency_year: null, ledger: null } as unknown as DecisionStudy
const BASIS = { currency: 'EUR', currency_year: 2025, discount_rate: 0.07, horizon_years: 25 }

const DONE: SupplyArchitecturesRecord = {
  status: 'done', stale: false, best_architecture_id: 'pv+genset',
  rows: [
    { architecture_id: 'pv', label: 'PV', groups: ['pv'], status: 'infeasible', total_cost_eur_per_year: null,
      capex_eur: null, renewable_fraction: null, sizes: [] },
    { architecture_id: 'genset', label: 'Genset', groups: ['genset'], status: 'ok',
      total_cost_eur_per_year: 3_500_000, capex_eur: 0, renewable_fraction: 0, sizes: [] },
    { architecture_id: 'pv+genset', label: 'PV + Genset', groups: ['pv', 'genset'], status: 'ok',
      total_cost_eur_per_year: 14_000, capex_eur: 0, renewable_fraction: 0.3, sizes: [] },
  ],
}

function result(over: Partial<GridExtensionResult>): GridExtensionResult {
  return { status: 'break_even', km: 8.1075, architecture_id: 'pv+genset', total_cost_eur_per_year: 14_000,
    offgrid_npc: 163_150, basis: BASIS, approximation: 'annualised approximation: …', ...over }
}

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><GridExtensionCard /></QueryClientProvider>)
}

beforeEach(() => {
  authMode.authEnabled = false
  goal.archetype = 'off_grid'
  goal.isPending = false
  for (const f of Object.values(api)) f.mockReset()
  undoInfo.mockReset()
  api.list.mockResolvedValue([STUDY])
  api.supplyArchitectures.mockResolvedValue(DONE)
  useUIStore.setState({ currentProject: 'Island A' })
})
afterEach(() => cleanup())

describe('GridExtensionCard: when it shows', () => {
  it('is hidden for a site that is not an island, and asks nothing', async () => {
    goal.archetype = 'strong_grid'
    const { container } = mount()
    await new Promise(r => setTimeout(r, 20))
    expect(container.textContent).toBe('')
    expect(api.list).not.toHaveBeenCalled()
  })

  it('keeps its title with the visible reason outside local mode', async () => {
    authMode.authEnabled = true
    mount()
    const card = await screen.findByTestId('grid-extension-card')
    expect(within(card).getByText(W.title)).toBeTruthy()
    expect(within(card).getByTestId('grid-ext-needs-local').textContent).toBe(W.needsLocalMode)
    expect((within(card).getByRole('button', { name: W.compare }) as HTMLButtonElement).disabled).toBe(true)
    expect(api.list).not.toHaveBeenCalled()
  })

  it('reads the routes’ own refusal as needing local mode too', async () => {
    api.list.mockRejectedValue(axErr(404, { code: 'decision_studies_unavailable', message: 'x' }))
    mount()
    expect((await screen.findByTestId('grid-ext-needs-local')).textContent).toBe(W.needsLocalMode)
  })
})

describe('GridExtensionCard: the comparison', () => {
  it('not run: the first press creates the bare study, sets its basis, and compares', async () => {
    api.list.mockResolvedValue([])
    api.create.mockResolvedValue(BARE)
    api.library.mockResolvedValue({ currency_year: 2025 })
    api.patchSettings.mockResolvedValue({ ...BARE, currency_year: 2025 })
    api.putLedger.mockResolvedValue({})
    api.get.mockResolvedValue(STUDY)
    api.startSupplyArchitectures.mockResolvedValue({ status: 'running' })
    mount()
    const button = await screen.findByRole('button', { name: W.compare })
    expect(screen.getByTestId('grid-ext-saves-note').textContent).toBe(W.savesNote)
    api.list.mockResolvedValue([STUDY])
    fireEvent.click(button)
    await waitFor(() => expect(api.startSupplyArchitectures).toHaveBeenCalledWith('Island A', 'ge1', {}))
    expect(api.create).toHaveBeenCalledWith('Island A',
      { question_id: 'grid_extension', name: 'Grid extension vs island', intake: {} })
    expect(api.patchSettings).toHaveBeenCalledWith('Island A', 'ge1', { currency_year: 2025 })
    expect(api.putLedger).toHaveBeenCalledWith('Island A', 'ge1', { reseed: true })
  })

  it('reuses the study it finds by its question id', async () => {
    api.supplyArchitectures.mockRejectedValue(axErr(404, { error_kind: 'supply_architectures_never_run', message: 'x' }))
    api.startSupplyArchitectures.mockResolvedValue({ status: 'running' })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: W.compare }))
    await waitFor(() => expect(api.startSupplyArchitectures).toHaveBeenCalledWith('Island A', 'ge1', {}))
    expect(api.create).not.toHaveBeenCalled()
    expect(api.putLedger).not.toHaveBeenCalled()
  })

  it('unsaved project: says save first with the header’s Save on the same line, until it is saved', async () => {
    api.supplyArchitectures.mockRejectedValue(axErr(404, { error_kind: 'supply_architectures_never_run', message: 'x' }))
    api.startSupplyArchitectures.mockRejectedValue(axErr(409, { error_kind: 'baseline_not_saved', message: 'x' }))
    undoInfo.mockResolvedValue({ depth: 1, unsaved: true })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: W.compare }))
    const line = await screen.findByTestId('grid-ext-save-first')
    expect(line.textContent).toContain(W.saveFirst)
    expect(within(line).getByRole('button', { name: W.saveInHeader })).toBeTruthy()
    expect(screen.queryByTestId('grid-ext-saves-note')).toBeNull()
    expect(line.textContent).not.toContain('baseline_not_saved')
    undoInfo.mockResolvedValue({ depth: 0, unsaved: false })
    await waitFor(() => expect(screen.queryByTestId('grid-ext-save-first')).toBeNull(), { timeout: 5000 })
  }, 10_000)

  it('running: names the architectures and solves, and Stop aborts', async () => {
    api.supplyArchitectures.mockResolvedValue({ status: 'running', architectures: ['pv', 'genset', 'pv+genset'],
      solves_estimated: 3, solves_charged: 1, current: 'genset' })
    api.abortSupplyArchitectures.mockResolvedValue({ status: 'running', aborting: true })
    mount()
    const run = await screen.findByTestId('grid-ext-running')
    expect(run.textContent).toContain('Comparing 3 architectures… (1 of 3 solves)')
    fireEvent.click(within(run).getByRole('button', { name: W.stop }))
    await waitFor(() => expect(api.abortSupplyArchitectures).toHaveBeenCalledWith('Island A', 'ge1'))
  })

  it('done: every row, cheapest first, money in the € m/k style, status in words', async () => {
    mount()
    const table = await screen.findByTestId('grid-ext-table')
    expect(within(table).getByText(W.colCost)).toBeTruthy()
    const rows = within(table).getAllByRole('row').slice(1) as HTMLTableRowElement[]
    expect(rows.map(r => r.cells[0].textContent)).toEqual(['PV + Genset', 'Genset', 'PV'])
    expect(rows[0].cells[1].textContent).toBe('€ 14 k')
    expect(rows[1].cells[1].textContent).toBe('€ 3.5 m')
    expect(rows[2].cells[1].textContent).toBe(W.noCost)
    expect(rows[0].cells[2].textContent).toBe('Solved')
    expect(rows[2].cells[2].textContent).toBe('No design meets the load')
    expect(table.textContent).not.toMatch(/infeasible|per year\s*€/)
    expect(screen.getByTestId('grid-ext-basis').textContent)
      .toBe('Basis: EUR 2025 · 7 % discount rate · 25 years · annualised approximation')
  })

  it('out of date: says so in ResultState’s words, hides the form, and Re-run compares again', async () => {
    api.supplyArchitectures.mockResolvedValue({ ...DONE, stale: true })
    api.startSupplyArchitectures.mockResolvedValue({ status: 'running' })
    mount()
    const stale = await screen.findByTestId('grid-ext-stale')
    expect(stale.textContent).toContain('Out of date: the network changed since this comparison')
    expect(screen.queryByTestId('grid-ext-form')).toBeNull()
    fireEvent.click(within(stale).getByRole('button', { name: W.rerun }))
    await waitFor(() => expect(api.startSupplyArchitectures).toHaveBeenCalledWith('Island A', 'ge1', {}))
  })
})

describe('GridExtensionCard: the break-even distance', () => {
  async function ask(over: Partial<GridExtensionResult> | Error, maxKm?: string) {
    if (over instanceof Error) api.gridExtensionBreakEven.mockRejectedValue(over)
    else api.gridExtensionBreakEven.mockResolvedValue(result(over))
    mount()
    await screen.findByTestId('grid-ext-form')
    fireEvent.change(screen.getByLabelText(W.eurPerKm), { target: { value: '20000' } })
    fireEvent.change(screen.getByLabelText(W.gridSupplyNpc), { target: { value: '1000' } })
    if (maxKm) fireEvent.change(screen.getByLabelText(W.maxKm), { target: { value: maxKm } })
    fireEvent.click(screen.getByRole('button', { name: W.find }))
    await waitFor(() => expect(api.gridExtensionBreakEven).toHaveBeenCalled())
  }

  it('sends the chosen values with the study’s basis, best architecture by default', async () => {
    await ask({})
    expect(api.gridExtensionBreakEven).toHaveBeenCalledWith('Island A', 'ge1', {
      architecture_id: 'pv+genset', eur_per_km: 20000, fixed_eur: 0, grid_supply_npc: 1000, max_km: 10000,
      grid_basis: BASIS,
    })
  })

  it.each([
    [{ status: 'break_even', km: 8.1075 }, undefined,
      'Grid extension pays off within 8.1 km of the grid; beyond that, the island design is cheaper.'],
    [{ status: 'offgrid_cheaper_everywhere', km: null }, undefined, 'The island design is cheaper at any distance.'],
    [{ status: 'grid_cheaper_everywhere', km: null }, undefined, 'Extending the grid is cheaper at any distance.'],
    [{ status: 'beyond_max_km', km: 81.3 }, '50', 'Break-even at 81 km, beyond the 50 km searched.'],
    [{ status: 'break_even_not_established', km: null, reason: 'input_not_finite_or_out_of_range',
       invalid: ['max_km'], message: 'Not computed.' }, undefined,
      'No break-even distance: a value is out of range (Maximum distance (km)).'],
  ] as const)('%o reads as words', async (over, maxKm, line) => {
    await ask(over as Partial<GridExtensionResult>, maxKm)
    const res = await screen.findByTestId('grid-ext-result')
    expect(screen.getByTestId('grid-ext-result-line').textContent).toBe(line)
    expect(res.textContent).toContain('Island net present cost: € 163 k')
    expect(res.textContent).toContain('Grid supply net present cost: € 1 k')
    expect(res.textContent).toContain(W.approximation)
    expect(res.textContent).not.toMatch(/\b0 km|NPC|break_even|_/)
  })

  it('a refusal reads in words, never its code', async () => {
    await ask(axErr(409, { error_kind: 'supply_architectures_stale', message: 'the base project changed' }))
    const err = await screen.findByTestId('grid-ext-error')
    const c = ERROR_COPY.supply_architectures_stale
    expect(err.textContent).toBe(`${c.title} ${c.action}`)
    expect(err.textContent).not.toContain('supply_architectures_stale')
  })

  it('a missing amount opens with its field label and asks nothing', async () => {
    mount()
    await screen.findByTestId('grid-ext-form')
    fireEvent.change(screen.getByLabelText(W.eurPerKm), { target: { value: '20000' } })
    fireEvent.click(screen.getByRole('button', { name: W.find }))
    expect((await screen.findByTestId('grid-ext-input-error')).textContent)
      .toBe(`${W.gridSupplyNpc}: enter an amount.`)
    expect(api.gridExtensionBreakEven).not.toHaveBeenCalled()
  })
})
