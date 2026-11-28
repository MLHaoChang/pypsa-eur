// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { goal } from '../../test/fixtures/reliabilityGoal'
import type { ReliabilityGoal } from '../../api/flow'

const state: { goal: ReliabilityGoal | null } = { goal: null }
vi.mock('../../hooks/useReliabilityGoal', async (orig) => ({
  ...(await orig<typeof import('../../hooks/useReliabilityGoal')>()),
  useReliabilityGoal: () => ({
    goal: state.goal, target: state.goal?.target_lole_h.value ?? null, isPending: false, isError: false,
  }),
}))

const { ReliabilityGoalSummary } = await import('./ReliabilityGoalSummary')

const withAssumed = (a: unknown, over = goal()): ReliabilityGoal =>
  ({ ...over, target_lole_h: { ...over.target_lole_h, assumed: a as number } })

afterEach(cleanup)

describe('ReliabilityGoalSummary: an assumed goal (walkthrough asset-investment 01)', () => {
  it('reads the site type\'s goal, marked with the shared AssumedChip', () => {
    state.goal = withAssumed(3)
    render(<ReliabilityGoalSummary items={['target']} testId="g" />)
    expect(screen.getByTestId('g-values').textContent).toBe('allowed shortfall 3 h/yr')
    expect(screen.getByTestId('g-assumed').dataset.state).toBe('assumed')
    expect(screen.getByTestId('g-assumed-source').textContent).toBe('site type default')
  })

  it('names the site type by its short word, with no second colon', () => {
    state.goal = goal({ site_archetype: ['off_grid', 'stored'] })
    render(<ReliabilityGoalSummary items={['site']} testId="g" />)
    const t = screen.getByTestId('g-values').textContent ?? ''
    expect(t).toContain('grid connection: none, an island')
    expect(t.match(/:/g)).toHaveLength(1)
  })

  it('has no chip for a set goal, nor for a non-number assumed value (GK B 65 C9)', () => {
    state.goal = withAssumed(3, goal({ target_lole_h: [1.5, 'stored'] }))
    render(<ReliabilityGoalSummary items={['target']} testId="g" />)
    expect(screen.getByTestId('g-values').textContent).toBe('allowed shortfall 1.5 h/yr')
    expect(screen.queryByTestId('g-assumed')).toBeNull()
    cleanup()
    state.goal = withAssumed('3')
    render(<ReliabilityGoalSummary items={['target']} testId="g" />)
    expect(screen.getByTestId('g-values').textContent).toBe('allowed shortfall not set')
    expect(screen.queryByTestId('g-assumed')).toBeNull()
  })
})

describe('ReliabilityGoalSummary: the verdict\'s own goal and draws', () => {
  it('shows the applied goal as assumed when nothing else names one', () => {
    state.goal = goal({ target_lole_h: [null, 'default'] })
    render(<ReliabilityGoalSummary items={['target']} testId="g" appliedTarget={3} />)
    expect(screen.getByTestId('g-values').textContent).toBe('allowed shortfall 3 h/yr')
    expect(screen.getByTestId('g-assumed').dataset.state).toBe('assumed')
  })

  it('names the last run\'s draws beside the goal\'s first batch', () => {
    state.goal = goal()
    render(<ReliabilityGoalSummary items={['draws']} testId="g" ranDraws={2000} />)
    expect(screen.getByTestId('g-values').textContent).toBe('MC draws 2,000 in the last run · first batch 500 (default)')
  })
})
