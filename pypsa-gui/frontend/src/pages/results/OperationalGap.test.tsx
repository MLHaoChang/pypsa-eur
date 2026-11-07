// DC 07 S4: Results › Summary's "Plan vs operation" card. A fresh gap shows
// the route's own numbers, signed; a missing side reads "—" with its reason,
// never 0; stale, a failed window and an unsolved reference show no table;
// `missing` and a solve in flight render nothing.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { OperationalGapPayload } from '../../api/operationalGap'
import { useUIStore } from '../../store/uiStore'
import OperationalGap, {
  OP_GAP_WORDS, REFERENCE_CONDITION_WORDS, gapText, pctText, valueText,
} from './OperationalGap'
import { ROLLING_WORDS } from '../solver/rollingWords'
import SolverSettingsSource from '../SolverSettings.tsx?raw'

const api = vi.hoisted(() => ({ get: vi.fn() }))
const sim = vi.hoisted(() => ({ getStatus: vi.fn() }))
vi.mock('../../api/operationalGap', async () => {
  const real = await vi.importActual<typeof import('../../api/operationalGap')>('../../api/operationalGap')
  return { ...real, operationalGapApi: api }
})
vi.mock('../../api/simulation', async () => {
  const real = await vi.importActual<typeof import('../../api/simulation')>('../../api/simulation')
  return { ...real, simulationApi: { ...real.simulationApi, getStatus: sim.getStatus } }
})

const row = (operational: number | null, reference: number | null, over = {}) => ({
  definition: 'engine definition',
  operational, reference,
  gap: operational == null || reference == null ? null : operational - reference,
  gap_pct: operational == null || reference == null || reference === 0 ? null : 100 * (operational - reference) / Math.abs(reference),
  ...over,
})

function payload(over: Partial<OperationalGapPayload> = {}): OperationalGapPayload {
  return {
    label: 'full-horizon dispatch on the same sizes',
    flag: 'fresh',
    opex_basis: 'PyPSA statistics.opex',
    excluded_terms: ['capital costs'],
    claim_limit: 'claim',
    gap_definition: 'gap = operational - reference',
    windows_definition: 'windows',
    definitions: {},
    metrics: {
      opex_eur: row(1_250_000, 1_200_000),
      unserved_energy_mwh: row(0, 0),
      curtailed_energy_mwh: row(null, null, {
        operational_reason: 'curtailment is off (no dc_curtailment generator)',
        reference_reason: 'curtailment is off (no dc_curtailment generator)',
      }),
      storage_discharge_mwh: row(40, 52),
      storage_end_mwh: row(3, 0),
    },
    windows: { total: 12, solved: 12, failed: 0, failed_index: null },
    reference: { status: 'ok', condition: 'optimal' },
    horizon: 24,
    overlap: 6,
    ...over,
  }
}

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/ /g, ' ')

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><OperationalGap /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'p', uiMode: 'expert', flowReturn: null, solverBlockRequest: null })
  sim.getStatus.mockResolvedValue({ running: false, status: 'ok', condition: 'optimal', objective: 1, solve_time: 1 })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Plan vs operation card', () => {
  it('shows the route numbers with signs, the window settings in the Solve strategy words and the claim limit', async () => {
    api.get.mockResolvedValue(payload())
    renderIt()
    const card = await screen.findByTestId('summary-operational-gap')
    expect(text(screen.getByTestId('operational-gap-lead'))).toBe(
      `Operation (rolling, 24 ${ROLLING_WORDS.horizon}, 6 ${ROLLING_WORDS.overlap}) against a full-horizon dispatch on the same sizes · 12 of 12 windows solved`)
    const opex = text(screen.getByTestId('operational-gap-row-opex_eur'))
    expect(opex).toContain('€ 1.3 m')
    expect(opex).toContain('€ 1.2 m')
    expect(opex).toContain('+€ 50 k')
    expect(opex).toContain('+4.2 %')
    const storage = text(screen.getByTestId('operational-gap-row-storage_discharge_mwh'))
    expect(storage).toContain('−12.00 MWh')
    expect(storage).toContain('−23.1 %')
    // A zero reference has no gap %: "—", never a division.
    expect(text(screen.getByTestId('operational-gap-row-storage_end_mwh'))).toContain('—')
    expect(text(screen.getByTestId('operational-gap-sign'))).toMatch(/positive means operation costs more/)
    expect(text(screen.getByTestId('operational-gap-claim'))).toBe(
      'What the missing look-ahead costs on these sizes. Both sides see the same prices and loads, so this is not forecast error.')
    expect(card.dataset.flag).toBe('fresh')
  })

  it('a metric the network lacks reads "—" in every column with its reason in plain words, never 0', async () => {
    api.get.mockResolvedValue(payload())
    renderIt()
    const r = await screen.findByTestId('operational-gap-row-curtailed_energy_mwh')
    const cells = Array.from(r.querySelectorAll('td')).slice(1).map(td => text(td as HTMLElement))
    expect(cells).toEqual(['—', '—', '—', '—'])
    const reason = text(screen.getByTestId('operational-gap-reason-curtailed_energy_mwh'))
    expect(reason).toBe(OP_GAP_WORDS.curtailed_energy_mwh.notModelled)
    // Engine identifiers stay under Source.
    expect(reason).not.toMatch(/dc_curtailment|generator/)
  })

  it('a side the run did not record says so, side by side', async () => {
    api.get.mockResolvedValue(payload({ metrics: {
      ...payload().metrics!,
      opex_eur: row(10, null, { reference_reason: 'not established' }),
    } }))
    renderIt()
    expect(text(await screen.findByTestId('operational-gap-reason-opex_eur'))).toBe('Full horizon: Not recorded for this run.')
  })

  it('stale reads "out of date" with no table, and its action runs the solve', async () => {
    api.get.mockResolvedValue(payload({ flag: 'stale', metrics: null, windows: null, reference: null, horizon: undefined, overlap: undefined }))
    const before = useUIStore.getState().runSolveRequest
    renderIt()
    const card = await screen.findByTestId('summary-operational-gap')
    expect(card.querySelector('[data-state="stale"]')).not.toBeNull()
    expect(screen.queryByTestId('operational-gap-table')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Run the operational dispatch again' }))
    expect(useUIStore.getState().runSolveRequest).toBe(before + 1)
  })

  it('a failed window names it, shows no table, and links Solver settings › Solve strategy with Back to the summary', async () => {
    api.get.mockResolvedValue(payload({
      flag: 'operational_window_failed', metrics: null,
      windows: { total: 12, solved: 8, failed: 1, failed_index: 8 },
    }))
    useUIStore.setState({ activeSlidePanel: 'results' })
    renderIt()
    const line = await screen.findByTestId('operational-gap-window-failed')
    expect(text(line)).toMatch(/^Window 9 of 12 did not solve, so the run stopped there and there is no gap to show\./)
    expect(text(screen.getByTestId('operational-gap-lead'))).toMatch(/· 8 of 12 windows solved$/)
    expect(screen.queryByTestId('operational-gap-table')).toBeNull()
    fireEvent.click(screen.getByTestId('operational-gap-open-strategy'))
    const ui = useUIStore.getState()
    expect(ui.solverBlockRequest).toBe('strategy')
    expect(ui.uiMode).toBe('expert')
    expect(ui.flowReturn?.label).toBe('Results summary')
    expect(ui.flowReturn?.panel).toBe('results')
  })

  it('in Guided the Solver settings link opens in place, never flipping the mode', async () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'results' })
    api.get.mockResolvedValue(payload({
      flag: 'operational_window_failed', metrics: null,
      windows: { total: 4, solved: 1, failed: 1, failed_index: 1 },
    }))
    renderIt()
    fireEvent.click(await screen.findByTestId('operational-gap-open-strategy'))
    const ui = useUIStore.getState()
    expect(ui.uiMode).toBe('guided')
    expect(ui.solverBlockRequest).toBe('strategy')
    expect(ui.flowReturn?.label).toBe('Results summary')
  })

  it('an unsolved full-horizon dispatch is named in plain words', async () => {
    api.get.mockResolvedValue(payload({ flag: 'reference_not_solved', metrics: null, reference: { status: 'warning', condition: 'infeasible' } }))
    renderIt()
    expect(text(await screen.findByTestId('operational-gap-reference-failed'))).toBe(
      `The full-horizon dispatch ${REFERENCE_CONDITION_WORDS.infeasible}, so there is no gap to show.`)
  })

  it('an unknown condition never shows the code', async () => {
    api.get.mockResolvedValue(payload({ flag: 'reference_not_solved', metrics: null, reference: { status: 'warning', condition: 'weird_code' } }))
    renderIt()
    const t = text(await screen.findByTestId('operational-gap-reference-failed'))
    expect(t).toBe('The full-horizon dispatch did not solve, so there is no gap to show.')
  })

  it('missing renders nothing', async () => {
    api.get.mockResolvedValue(payload({ flag: 'missing', metrics: null, windows: null, reference: null }))
    renderIt()
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByTestId('summary-operational-gap')).toBeNull()
  })

  it('a solve in flight renders nothing, never an earlier table', async () => {
    sim.getStatus.mockResolvedValue({ running: true, status: 'running', condition: null, objective: null, solve_time: null })
    api.get.mockResolvedValue(payload())
    renderIt()
    await waitFor(() => expect(sim.getStatus).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 0))
    expect(screen.queryByTestId('summary-operational-gap')).toBeNull()
  })
})

describe('formatting', () => {
  it('signs gaps, never values that are positive', () => {
    expect(gapText(0, 'mwh')).toBe('0 MWh')
    expect(gapText(-5, 'eur').replace(/ /g, ' ')).toBe('−€ 5')
    expect(valueText(5, 'eur').replace(/ /g, ' ')).toBe('€ 5')
    expect(pctText(0.01)).toBe('0.0 %')
    expect(pctText(null)).toBe('—')
  })
})

describe('Solve strategy words', () => {
  it('the Solver settings fields read their unit words from ROLLING_WORDS', () => {
    expect(SolverSettingsSource).toContain('{ROLLING_WORDS.horizon}')
    expect(SolverSettingsSource).toContain('{ROLLING_WORDS.overlap}')
    expect(SolverSettingsSource).not.toContain('>snapshots per window<')
  })
})
