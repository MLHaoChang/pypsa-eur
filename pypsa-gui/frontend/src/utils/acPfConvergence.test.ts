import { describe, expect, it } from 'vitest'
import { acPfConvergenceText } from './acPfConvergence'

describe('acPfConvergenceText (AA-12)', () => {
  it('reads a fully known run as k / n', () => {
    const t = acPfConvergenceText({ converged_count: 5, known_converged_count: 5, unknown_count: 0, total_snapshots: 8 })
    expect(t).toEqual({ kpi: '5 / 8', short: '5/8 converged', note: null })
  })

  it('keeps the known count beside the unknown suffix in the mixed case', () => {
    const t = acPfConvergenceText({ converged_count: null, known_converged_count: 5, unknown_count: 2, total_snapshots: 8 })
    expect(t.kpi).toBe('5 / 8 · 2 unknown')
    expect(t.short).toBe('convergence unknown for 2/8')
    expect(t.note).toBe('PyPSA did not report convergence for 2 of 8 snapshots: their AC values are not checked. Re-run Stage 2, or use the LOPF results.')
  })

  it('reads an all-unknown run as Unknown / n, never as converged or 0 of n', () => {
    const t = acPfConvergenceText({ converged_count: null, known_converged_count: 0, unknown_count: 8, total_snapshots: 8 })
    expect(t.kpi).toBe('Unknown / 8')
    expect(t.note).toContain('8 of 8 snapshots')
  })

  it('never shows the known count alone when no converged count is recorded', () => {
    expect(acPfConvergenceText({ converged_count: null, known_converged_count: 3, total_snapshots: 4 }).kpi).toBe('Unknown / 4')
  })

  it('reads a pre-fix status without unknown_count as before', () => {
    expect(acPfConvergenceText({ converged_count: 7, total_snapshots: 8 }).kpi).toBe('7 / 8')
  })
})
