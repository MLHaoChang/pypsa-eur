// TSG 05: a refined preview lists its recorded steps, each with its own
// Remove. Remove asks the backend for a new pending series without that step
// (the old → new card replaces the current one in place); a refusal shows as
// text with the next action. A new preview needs its own Apply.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PendingCard, RefinePanel } from './SeriesKit'
import type { PendingSeries } from '../../api/profiles'

const api = vi.hoisted(() => ({ refine: vi.fn(), apply: vi.fn() }))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: { ...real.seriesApi, refine: api.refine, apply: api.apply } }
})

const REFINED: PendingSeries = {
  job_id: 'job-2', based_on: 'job-1', family: 'load', points: ['plant'], stats: {},
  refinements: [
    { op: 'scale_window', hours: [5, 10], factor: 1.3, weekdays: [0, 1, 2, 3, 4], target: ['loads', 'p_set'] },
    { op: 'scale_peak_to', mw: 60, target: ['loads', 'p_set'] },
  ],
}

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })

function renderPanel(pending: PendingSeries, onRefined = vi.fn()) {
  const qc = client()
  const ui = (p: PendingSeries) => (
    <QueryClientProvider client={qc}>
      <RefinePanel project="Site A" pending={p} power onRefined={onRefined} />
    </QueryClientProvider>
  )
  const { rerender } = render(ui(pending))
  return { onRefined, rerender: (p: PendingSeries) => rerender(ui(p)) }
}

const refusal = (detail: Record<string, unknown>) => ({ response: { data: { detail } } })

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('RefinePanel per-step removal', () => {
  it('lists no steps on an unrefined preview', () => {
    renderPanel({ ...REFINED, refinements: [] })
    expect(screen.queryByTestId('refine-steps')).toBeNull()
  })

  it('lists each recorded step in words under its own heading, each with Remove', () => {
    renderPanel(REFINED)
    const steps = screen.getByTestId('refine-steps')
    expect(within(steps).getByText('Refinements in this preview')).toBeTruthy()
    expect(steps.textContent).toContain('nothing changes until you apply it')
    const items = within(screen.getByTestId('refine-edits')).getAllByRole('listitem')
    expect(items.map(i => i.querySelector('span')?.textContent)).toEqual([
      '05–10 h Mon–Fri × 1.3',
      'peak to 60',
    ])
    expect(screen.getByRole('button', { name: 'Remove step 1 (scale an hour window)' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove step 2 (scale the peak to)' })).toBeTruthy()
  })

  it('removes one step into a new preview and keeps focus on the list', async () => {
    const user = userEvent.setup()
    const next = { ...REFINED, job_id: 'job-3', refinements: [REFINED.refinements![0]], undone: { index: 1, op: 'scale_peak_to' } }
    api.refine.mockResolvedValue(next)
    const { onRefined, rerender } = renderPanel(REFINED)
    await user.click(screen.getByRole('button', { name: 'Remove step 2 (scale the peak to)' }))
    await waitFor(() => expect(onRefined).toHaveBeenCalledWith(next))
    expect(api.refine).toHaveBeenCalledWith('Site A', 'job-2', [], 1)
    rerender(next)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('refine-steps')))
    expect(within(screen.getByTestId('refine-edits')).getAllByRole('listitem')).toHaveLength(1)
  })

  it('disables every Remove and says why while a removal runs', async () => {
    const user = userEvent.setup()
    api.refine.mockReturnValue(new Promise(() => {}))
    renderPanel(REFINED)
    await user.click(screen.getByRole('button', { name: 'Remove step 1 (scale an hour window)' }))
    expect(screen.getByTestId('refine-undo-pending').textContent)
      .toBe('Removing step 1: building the new preview.')
    for (const b of within(screen.getByTestId('refine-edits')).getAllByRole('button')) {
      expect((b as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it('removing the last step leaves an unrefined preview: no list, focus on the panel', async () => {
    const user = userEvent.setup()
    const one = { ...REFINED, refinements: [REFINED.refinements![1]] }
    const bare = { ...REFINED, job_id: 'job-1', refinements: [], provenance: 'synthetic/assumed' }
    api.refine.mockResolvedValue(bare)
    const { rerender } = renderPanel(one)
    await user.click(screen.getByRole('button', { name: 'Remove step 1 (scale the peak to)' }))
    rerender(bare)
    expect(screen.queryByTestId('refine-steps')).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('refine-panel')))
  })

  it('names the later step that no longer fits, with the next action', async () => {
    const user = userEvent.setup()
    api.refine.mockRejectedValue(refusal({
      error_kind: 'refine_replay', op: 'scale_peak_to', index: 1,
      message: 'op 1 (scale_peak_to) no longer fits: op 1: the series has no peak to scale',
    }))
    const { onRefined } = renderPanel(REFINED)
    await user.click(screen.getByRole('button', { name: 'Remove step 1 (scale an hour window)' }))
    expect((await screen.findByTestId('refine-undo-error')).textContent).toBe(
      'Step 2 of the refinements (scale the peak to) no longer fits without step 1: '
      + 'remove step 2 first, or keep step 1.')
    expect(onRefined).not.toHaveBeenCalled()
  })

  it('says plainly when the series before the step is no longer kept', async () => {
    const user = userEvent.setup()
    api.refine.mockRejectedValue(refusal({
      error_kind: 'invalid_refine_op',
      message: 'the series before step 1 is no longer kept: generate it again without the refinements, or undo the whole apply',
    }))
    renderPanel(REFINED)
    await user.click(screen.getByRole('button', { name: 'Remove step 1 (scale an hour window)' }))
    expect((await screen.findByTestId('refine-undo-error')).textContent).toBe(
      'The series before step 1 is no longer kept: generate it again without the refinements, or undo the whole apply')
  })
})

describe('PendingCard after a removal', () => {
  it('drops the refined tag and offers Apply again for the new preview', async () => {
    const user = userEvent.setup()
    api.apply.mockResolvedValue({ component: 'plant', component_type: 'loads', attribute: 'p_set', peak: 60, mean: 30, min: 0, warnings: [] })
    const qc = client()
    const ui = (p: PendingSeries) => (
      <QueryClientProvider client={qc}>
        <PendingCard project="Site A" component="plant" pending={p} power attribute="p_set" />
      </QueryClientProvider>
    )
    const { rerender } = render(ui(REFINED))
    expect(screen.getByText('refined')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Apply to plant/ }))
    await screen.findByTestId('applied')
    rerender(ui({ ...REFINED, job_id: 'job-1', refinements: [] }))
    expect(screen.queryByText('refined')).toBeNull()
    expect(screen.queryByTestId('applied')).toBeNull()
    expect(screen.getByRole('button', { name: /Apply to plant/ })).toBeTruthy()
  })
})
