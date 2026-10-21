// UX-flow #5: section 9 has a Run, and it is the header's gated Run, not a second one.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'
import type { RunGate } from '../hooks/useRunGate'

const gate = vi.hoisted(() => ({ value: { blocked: false, checking: false } as Partial<RunGate> }))
vi.mock('../hooks/useRunGate', () => ({ useRunGate: () => gate.value }))

import { RunFromReadiness } from './RunFromReadiness'

afterEach(() => cleanup())

describe('RunFromReadiness', () => {
  it('asks the header Run to start, through requestRunSolve', () => {
    gate.value = { blocked: false, checking: false }
    const before = useUIStore.getState().runSolveRequest
    render(<RunFromReadiness />)
    fireEvent.click(screen.getByTestId('readiness-run'))
    expect(useUIStore.getState().runSolveRequest).toBe(before + 1)
  })

  it('shows nothing while the gate blocks or is still checking', () => {
    gate.value = { blocked: true, checking: false }
    const { rerender } = render(<RunFromReadiness />)
    expect(screen.queryByTestId('readiness-run')).toBeNull()
    gate.value = { blocked: false, checking: true }
    rerender(<RunFromReadiness />)
    expect(screen.queryByTestId('readiness-run')).toBeNull()
  })
})
