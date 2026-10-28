// TSG-07: the refinement list offers "Move the maintenance window" on a
// maintenance series only. One window shows its dates; several need a pick
// (nothing preselected). The days field takes a signed whole number other
// than 0, and the op list reads the move in words.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RefinePanel, windowDates } from './SeriesKit'
import type { PendingSeries } from '../../api/profiles'

const api = vi.hoisted(() => ({ refine: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: { ...real.seriesApi, refine: api.refine } }
})

const MAINTENANCE: PendingSeries = {
  job_id: 'job-1', family: 'availability', kind: 'maintenance', points: ['CHP'], stats: {},
  windows: [{ from: '07-01', to: '07-07', available: 0, dated: false }],
}
const TWO = { ...MAINTENANCE, windows: [
  { from: '06-28', to: '07-04', available: 0, dated: false },
  { from: '09-01', to: '09-03', available: 0.5, dated: false },
] }

function renderPanel(pending: PendingSeries) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <RefinePanel project="Site A" pending={pending} power={false} onRefined={() => {}} />
    </QueryClientProvider>,
  )
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('windowDates', () => {
  it('reads a window in day-month words', () => {
    expect(windowDates({ from: '07-01', to: '07-07' })).toBe('1–7 Jul')
    expect(windowDates({ from: '06-28', to: '07-04' })).toBe('28 Jun–4 Jul')
    expect(windowDates({ from: '2025-12-27', to: '01-04' })).toBe('27 Dec–4 Jan')
  })
})

describe('RefinePanel move_window', () => {
  it('is offered only on a maintenance series', () => {
    renderPanel({ ...MAINTENANCE, kind: 'derating', windows: undefined })
    expect(within(screen.getByLabelText('Refinement')).queryByText('Move the maintenance window')).toBeNull()
  })

  it('moves the one window by signed days and lists it in words', async () => {
    const user = userEvent.setup()
    api.refine.mockResolvedValue({ ...MAINTENANCE, job_id: 'job-2' })
    renderPanel(MAINTENANCE)
    await user.selectOptions(screen.getByLabelText('Refinement'), 'move_window')
    expect(screen.getByTestId('move-window-dates').textContent).toContain('Window 1–7 Jul')
    expect(screen.getByTestId('refine-add-reason').textContent).toContain('Enter a whole number of days other than 0')
    await user.type(screen.getByLabelText('By days (+ later, − earlier)'), '0')
    expect(screen.getByTestId('refine-add-reason').textContent).toContain('Enter a whole number of days other than 0')
    await user.clear(screen.getByLabelText('By days (+ later, − earlier)'))
    await user.type(screen.getByLabelText('By days (+ later, − earlier)'), '3')
    await user.click(screen.getByRole('button', { name: /Add/ }))
    expect(screen.getByTestId('refine-ops').textContent).toContain('Move maintenance 1–7 Jul by +3 days')
    await user.click(screen.getByRole('button', { name: /Refine/ }))
    await waitFor(() => expect(api.refine).toHaveBeenCalledWith('Site A', 'job-1', [{ op: 'move_window', by: '+3d' }]))
  })

  it('asks for a window when there are several, and sends its index', async () => {
    const user = userEvent.setup()
    api.refine.mockResolvedValue({ ...TWO, job_id: 'job-2' })
    renderPanel(TWO)
    await user.selectOptions(screen.getByLabelText('Refinement'), 'move_window')
    await user.type(screen.getByLabelText('By days (+ later, − earlier)'), '-14')
    expect(screen.getByTestId('refine-add-reason').textContent).toContain('Pick a window to move')
    expect((screen.getByRole('button', { name: /Add/ }) as HTMLButtonElement).disabled).toBe(true)
    await user.selectOptions(screen.getByLabelText('Window'), '28 Jun–4 Jul')
    await user.click(screen.getByRole('button', { name: /Add/ }))
    expect(screen.getByTestId('refine-ops').textContent).toContain('Move maintenance 28 Jun–4 Jul by −14 days')
    await user.click(screen.getByRole('button', { name: /Refine/ }))
    await waitFor(() => expect(api.refine).toHaveBeenCalledWith('Site A', 'job-1',
      [{ op: 'move_window', window: 0, by: '-14d' }]))
  })
})
