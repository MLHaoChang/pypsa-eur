import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import AssetSummary from './AssetSummary'
import ScalarTable, { type ScalarRow } from './ScalarTable'
import HorizonFilter from './HorizonFilter'
import type { AssetResultsResponse, HeadlineRow } from './types'
import type { ResultsFilterControls } from '../filterContext'

const headline = (over: Partial<HeadlineRow> = {}): HeadlineRow => ({
  id: 'energy_mwh', label: 'Energy', unit: 'MWh', category: 'dispatch',
  category_label: 'Dispatch', origin: 'derived', status: 'ok', value: 512000,
  ...over,
})

const response = (over: Partial<AssetResultsResponse> = {}): AssetResultsResponse => ({
  asset: { class: 'Generator', name: 'Gas 1', carrier: 'gas', bus: 'B1',
           params: { p_nom: 200, marginal_cost: 50.5 } },
  solve: { source: 'lopf', objective: 1, solve_time: 1, condition: 'optimal' },
  category: 'summary', mode: 'chronological', categories: [], metrics: [],
  scalars: {}, headline: [headline()], index: [], periods: null,
  pct_of_hours: null, columns: [], series: {},
  ...over,
})

describe('AssetSummary', () => {
  it('renders headline KPIs with value, unit and the tab they came from', () => {
    render(<AssetSummary data={response()} />)
    const row = screen.getByText('Energy').closest('tr')!
    expect(within(row).getByText('512,000.00')).toBeTruthy()
    expect(within(row).getByText('MWh')).toBeTruthy()
    // The source tab is what lets a user go read the full result.
    expect(within(row).getByText('Dispatch')).toBeTruthy()
  })

  it('shows a blocked KPI with its reason instead of dropping the row', () => {
    // A summary that silently omits half its rows on an unsolved network
    // reads as if those results do not exist.
    render(<AssetSummary data={response({
      headline: [headline({
        id: 'capture_price', label: 'Capture price', status: 'blocked',
        value: undefined, reason: 'the network has not been solved',
      })],
    })} />)
    const row = screen.getByText('Capture price').closest('tr')!
    expect(within(row).getByText(/has not been solved/)).toBeTruthy()
  })

  it('renders identity and parameters as key/value tables', () => {
    render(<AssetSummary data={response()} />)
    expect(screen.getByText('Identity')).toBeTruthy()
    expect(screen.getByText('Parameters')).toBeTruthy()
    expect(screen.getByText('Gas 1')).toBeTruthy()
    // Parameters go through the same two-decimal formatter as everything else.
    expect(screen.getByText('50.50')).toBeTruthy()
    expect(screen.getByText('200.00')).toBeTruthy()
  })

  it('says so when a class has no headline KPIs defined', () => {
    render(<AssetSummary data={response({ headline: [] })} />)
    expect(screen.getByText(/no headline results are defined/i)).toBeTruthy()
  })
})

describe('ScalarTable', () => {
  const rows: ScalarRow[] = [
    { id: 'a', label: 'Energy', unit: 'MWh', value: 1234.5, status: 'ok' },
    { id: 'b', label: 'Capture price', unit: 'EUR/MWh', status: 'blocked',
      reason: 'LP duals were not captured' },
  ]

  it('renders nothing at all when there are no rows', () => {
    const { container } = render(<ScalarTable rows={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('formats values and hides the unit on an unavailable row', () => {
    render(<ScalarTable rows={rows} />)
    const ok = screen.getByText('Energy').closest('tr')!
    expect(within(ok).getByText('1,234.50')).toBeTruthy()
    expect(within(ok).getByText('MWh')).toBeTruthy()

    const blocked = screen.getByText('Capture price').closest('tr')!
    expect(within(blocked).getByText(/duals were not captured/)).toBeTruthy()
    // A unit next to a reason instead of a number is noise.
    expect(within(blocked).queryByText('EUR/MWh')).toBeNull()
  })

  it('only renders the source column when asked', () => {
    const { rerender } = render(<ScalarTable rows={rows} />)
    expect(screen.queryByText('Source tab')).toBeNull()
    rerender(<ScalarTable rows={rows} showSource />)
    expect(screen.getByText('Source tab')).toBeTruthy()
  })
})

describe('HorizonFilter', () => {
  const controls = (over: Partial<ResultsFilterControls> = {}): ResultsFilterControls => ({
    fromInput: '2026-01-01T00:00', toInput: '2026-12-31T23:00',
    setFromInput: vi.fn(), setToInput: vi.fn(),
    firstSnap: '2026-01-01T00:00', lastSnap: '2026-12-31T23:00',
    periods: [], selectedPeriod: 'all', setSelectedPeriod: vi.fn(),
    isFiltered: false, reset: vi.fn(),
    ...over,
  })

  it('writes a changed bound straight back to the shell filter', async () => {
    const setFromInput = vi.fn()
    render(<HorizonFilter controls={controls({ setFromInput })} />)
    const from = screen.getByLabelText('Horizon from')
    await userEvent.clear(from)
    await userEvent.type(from, '2026-03-01T00:00')
    expect(setFromInput).toHaveBeenCalled()
  })

  it('clamps the inputs to the network span', () => {
    render(<HorizonFilter controls={controls()} />)
    const from = screen.getByLabelText('Horizon from') as HTMLInputElement
    expect(from.min).toBe('2026-01-01T00:00')
    expect(from.max).toBe('2026-12-31T23:00')
  })

  it('offers a reset only once the horizon is actually narrowed', async () => {
    const reset = vi.fn()
    const { rerender } = render(<HorizonFilter controls={controls({ reset })} />)
    expect(screen.queryByText(/full horizon/i)).toBeNull()

    rerender(<HorizonFilter controls={controls({ reset, isFiltered: true })} />)
    await userEvent.click(screen.getByText(/full horizon/i))
    expect(reset).toHaveBeenCalledOnce()
  })

  it('hides the period chips on a flat network and shows them otherwise', () => {
    const { rerender } = render(<HorizonFilter controls={controls()} />)
    expect(screen.queryByText('Period')).toBeNull()

    rerender(<HorizonFilter controls={controls({ periods: [2030, 2040] })} />)
    expect(screen.getByText('Period')).toBeTruthy()
    expect(screen.getByText('2030')).toBeTruthy()
    expect(screen.getByText('All')).toBeTruthy()
  })
})
