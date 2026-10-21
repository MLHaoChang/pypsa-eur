import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { loadShapeApi, type LoadShapePayload } from '../../api/loadShape'
import LoadShapeTab from './LoadShapeTab'
import { WEEK } from './loadShape.test'
import { expectAllButtonsNamed } from '../../test-utils/accessibleName'

vi.mock('../../api/loadShape', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/loadShape')>()
  return { ...actual, loadShapeApi: { get: vi.fn() } }
})

const heat = (peak: number) => ({
  status: 'ok' as const,
  mean_mw: Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => (d === 0 && h === 0 ? peak : d === 0 && h === 1 ? null : 1))),
  snapshots: Array.from({ length: 7 }, (_, d) => Array.from({ length: 24 }, (_, h) => (d === 0 && h === 1 ? 0 : 1))),
})

const OK: LoadShapePayload = {
  status: 'ok', unit: 'MW', period: null, periods: [null],
  window: { from: 0, to: 167, snapshots: 168, start: '2030-03-04 00:00:00', end: '2030-03-10 23:00:00' },
  series_status: {
    grid: { status: 'ok' }, solar: { status: 'ok' }, wind: { status: 'not_modelled', reason: 'no wind generation in this network' },
    ev: { status: 'not_modelled', reason: 'no EV load in this network' }, unserved: { status: 'unavailable', reason: 'this solve recorded no lost-load capture' },
  },
  heatmaps: { import: heat(8), export: { status: 'not_modelled', reason: 'no grid connection in this network' } },
  week: WEEK,
  monthly: { status: 'unavailable', reason: 'the run uses representative snapshots (each weighs 52.1 h of a 1 h step), so calendar months are not modelled' },
  annual: [
    { status: 'ok', period: 2030, load_served: 100, solar: 30, wind: null, genset: 10, grid: 60, export: 0, excess: 5 },
    { status: 'ok', period: 2035, load_served: 120, solar: 50, wind: null, genset: 10, grid: 60, export: 0, excess: 8 },
  ],
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><LoadShapeTab /></QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(loadShapeApi.get).mockReset().mockResolvedValue(OK)
})
afterEach(() => cleanup())

describe('LoadShapeTab', () => {
  it('draws the import heatmap, shows a gap as blank and an unmodelled export as not modelled', async () => {
    const { container } = mount()
    const imp = await screen.findByTestId('ls-heat-import')
    const cells = imp.querySelectorAll('[data-cell]')
    expect(cells).toHaveLength(7 * 24)
    expect(imp.querySelector('[data-cell="0-0"]')!.getAttribute('data-value')).toBe('8')
    expect(imp.querySelector('[data-cell="0-1"]')!.getAttribute('data-value')).toBe('')          // no snapshot
    expect(imp.querySelector('[data-cell="0-1"]')!.getAttribute('title')).toContain('no snapshot')
    expect((imp.querySelector('[data-cell="0-1"]') as HTMLElement).style.background).toContain('repeating-linear-gradient')
    expect((imp.querySelector('[data-cell="0-0"]') as HTMLElement).style.background).not.toContain('repeating')
    expect(within(screen.getByTestId('ls-heat-export')).getByText(/no grid connection/)).toBeTruthy()
    expect(screen.queryByTestId('ls-heat-export')!.querySelector('[data-cell]')).toBeNull()
    expectAllButtonsNamed(container)
  })

  it('names every group that is not drawn, with the reason', async () => {
    mount()
    expect((await screen.findByTestId('ls-missing-wind')).textContent).toContain('not modelled')
    expect(screen.getByTestId('ls-missing-unserved').textContent).toContain('not available for this run')
    expect(screen.getByTestId('ls-missing-unserved').textContent).toContain('no lost-load capture')
  })

  it('says why the monthly view is unavailable instead of drawing it', async () => {
    mount()
    const monthly = await screen.findByTestId('ls-monthly')
    expect(within(monthly).getByText(/representative snapshots/)).toBeTruthy()
    expect(monthly.querySelector('svg')).toBeNull()
    // one line, not a framed empty section
    expect(monthly.tagName).toBe('P')
    expect(within(monthly).queryByRole('heading')).toBeNull()
  })

  it('explains the flags and the picker has the weeks', async () => {
    mount()
    expect((await screen.findByTestId('ls-flags')).textContent).toMatch(/grid import at 40 MW connection.*1 of 4/)
    expect(within(screen.getByTestId('ls-week-select')).getAllByRole('option')).toHaveLength(1)
  })

  it('picks the annual year and shows the supply stack with what is not modelled', async () => {
    mount()
    await screen.findByTestId('ls-annual-stack')
    expect(screen.getByTestId('ls-annual-solar').textContent).toContain('30')
    expect(screen.getByTestId('ls-annual').textContent).toContain('Not modelled here: Wind')
    fireEvent.change(screen.getByTestId('ls-year'), { target: { value: '2035' } })
    await waitFor(() => expect(screen.getByTestId('ls-annual-solar').textContent).toContain('50'))
    expect(screen.getByTestId('ls-annual-load').textContent).toContain('120')
  })

  it('asks for another week and for a narrowed heatmap window', async () => {
    vi.mocked(loadShapeApi.get).mockResolvedValue({
      ...OK, week: { ...WEEK, weeks: [WEEK.weeks[0], { ...WEEK.weeks[0], key: '2030-W11', from: 4, to: 7 }] },
    })
    mount()
    fireEvent.change(await screen.findByTestId('ls-week-select'), { target: { value: '1' } })
    await waitFor(() => expect(loadShapeApi.get).toHaveBeenLastCalledWith(expect.objectContaining({ week: 1 })))
    fireEvent.change(screen.getByLabelText('Window end'), { target: { value: '23' } })
    expect(screen.getByTestId('ls-window-label').textContent).toContain('24 of 168')
    await waitFor(() => expect(loadShapeApi.get).toHaveBeenLastCalledWith(
      expect.objectContaining({ from: 0, to: 23 })), { timeout: 2000 })
  })

  it('shows an unproven run as unavailable with no charts', async () => {
    vi.mocked(loadShapeApi.get).mockResolvedValue({ ...OK, status: 'not_established', reason: 'bus x is in t/h, not an energy unit' })
    mount()
    expect(await screen.findByTestId('ls-unavailable')).toBeTruthy()
    expect(screen.queryByTestId('ls-heatmaps')).toBeNull()
  })

  it('says "run a solve" before there is one', async () => {
    vi.mocked(loadShapeApi.get).mockResolvedValue(null)
    mount()
    expect(await screen.findByText(/Run a solve/)).toBeTruthy()
  })
})
