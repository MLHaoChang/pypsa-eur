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
