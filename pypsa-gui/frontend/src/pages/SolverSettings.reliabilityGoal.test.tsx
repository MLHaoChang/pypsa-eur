// Expert's home of the reliability goal (W3b-R): the one editor replaces the
// ENS target input in Solver settings › Network; edits go into the draft and
// the panel's Save sends only what changed.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { SolverConfig } from '../api/types'
import { goal } from '../test/fixtures/reliabilityGoal'
import { ReliabilityAssumptions, solverSettingsSaveBody } from './SolverSettings'

afterEach(() => cleanup())

const DRAFT = {
  voll: 3000, ens_cap_permyriad: null, ens_zone_cap_multiple: null, reserve_margin: null,
  target_lole_h: null, mc_draws: null, site_archetype: null,
  dsr_price_eur_per_mwh: 0, dsr_share_of_load: 0, dsr_buses: [],
} as unknown as SolverConfig

describe('Solver settings — the reliability goal', () => {
  it('is the anchor "Change in Scenarios & optimisation ›" scrolls to', () => {
    const { container } = render(<ReliabilityAssumptions draft={DRAFT} patch={vi.fn()} />)
    expect(container.querySelector('#solver-reliability-goal')).toBeTruthy()
    // one ENS input: the editor's
    expect(screen.getAllByText(/ENS target/).length).toBe(1)
  })

  it('shows the stored goal\'s values, the same the Goal card shows', () => {
    render(<ReliabilityAssumptions draft={DRAFT} patch={vi.fn()}
      goal={goal({ target_lole_h: [2, 'template'], mc_draws: [800, 'stored'] })}
      goalEdits={{}} onGoalEdits={vi.fn()} />)
    expect((screen.getByTestId('solver-goal-lole') as HTMLInputElement).value).toBe('2')
    expect((screen.getByTestId('solver-goal-draws') as HTMLInputElement).value).toBe('800')
  })

  it('a valid edit goes into the draft; Save then sends only that field', () => {
    const patch = vi.fn()
    render(<ReliabilityAssumptions draft={DRAFT} patch={patch} />)
    fireEvent.change(screen.getByTestId('solver-goal-lole'), { target: { value: '3' } })
    expect(patch).toHaveBeenLastCalledWith({ target_lole_h: 3 })
    const next = { ...DRAFT, ...patch.mock.calls.at(-1)![0] } as SolverConfig
    expect(solverSettingsSaveBody(next, DRAFT)).toEqual({ target_lole_h: 3 })
  })

  it('an invalid edit never reaches the draft and is named', () => {
    const patch = vi.fn()
    render(<ReliabilityAssumptions draft={DRAFT} patch={patch} />)
    fireEvent.change(screen.getByTestId('solver-goal-draws'), { target: { value: '2.5' } })
    expect(patch).not.toHaveBeenCalled()
    expect(screen.getByTestId('solver-goal-error').textContent).toMatch(/MC draws/)
  })

  it('keeps the ENS warning for a generous target', () => {
    render(<ReliabilityAssumptions draft={{ ...DRAFT, ens_cap_permyriad: 500 }} patch={vi.fn()} />)
    expect(screen.getByText(/500‱ = 5% of demand deliberately unserved/)).toBeTruthy()
  })
})
