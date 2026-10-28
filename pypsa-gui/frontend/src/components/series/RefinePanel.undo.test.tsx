// TSG 05: a refined series lists its recorded edits, each with its own Undo.
// Undo asks the backend for a new pending series without that edit (the
// old → new card replaces the current one); a refusal shows as text.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RefinePanel } from './SeriesKit'
import type { PendingSeries } from '../../api/profiles'

const api = vi.hoisted(() => ({ refine: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: { ...real.seriesApi, refine: api.refine } }
})

const REFINED: PendingSeries = {
  job_id: 'job-2', based_on: 'job-1', family: 'load', points: ['plant'], stats: {},
  refinements: [
    { op: 'scale_window', hours: [5, 10], factor: 1.3, weekdays: [0, 1, 2, 3, 4], target: ['loads', 'p_set'] },
    { op: 'scale_peak_to', mw: 60, target: ['loads', 'p_set'] },
  ],
}

function renderPanel(pending: PendingSeries, onRefined = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <RefinePanel project="Site A" pending={pending} power onRefined={onRefined} />
    </QueryClientProvider>,
  )
  return onRefined
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('RefinePanel per-op undo', () => {
  it('lists no edits on an unrefined series', () => {
    renderPanel({ ...REFINED, refinements: [] })
    expect(screen.queryByTestId('refine-edits')).toBeNull()
  })

  it('lists each recorded edit in words with its own Undo', () => {
    renderPanel(REFINED)
    const items = within(screen.getByTestId('refine-edits')).getAllByRole('listitem')
    expect(items.map(i => i.querySelector('span')?.textContent)).toEqual([
      '05–10 h Mon–Fri × 1.3',
      'peak to 60',
    ])
    expect(screen.getByRole('button', { name: 'Undo edit 1' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Undo edit 2' })).toBeTruthy()
  })

  it('undoes one edit into a new pending series', async () => {
    const user = userEvent.setup()
    const next = { ...REFINED, job_id: 'job-3', refinements: [REFINED.refinements![0]], undone: { index: 1, op: 'scale_peak_to' } }
    api.refine.mockResolvedValue(next)
    const onRefined = renderPanel(REFINED)
    await user.click(screen.getByRole('button', { name: 'Undo edit 2' }))
    await waitFor(() => expect(onRefined).toHaveBeenCalledWith(next))
    expect(api.refine).toHaveBeenCalledWith('Site A', 'job-2', [], 1)
  })

  it('shows a refusal as text, naming the step', async () => {
    const user = userEvent.setup()
    api.refine.mockRejectedValue({
      response: { data: { detail: { error_kind: 'refine_replay', op: 'scale_peak_to', index: 1,
        message: 'op 1 (scale_peak_to) no longer fits: op 1: the series has no peak to scale' } } },
    })
    const onRefined = renderPanel(REFINED)
    await user.click(screen.getByRole('button', { name: 'Undo edit 1' }))
    const refusal = await screen.findByTestId('refine-undo-error')
    expect(refusal.textContent).toContain('no longer fits')
    expect(onRefined).not.toHaveBeenCalled()
  })
})
