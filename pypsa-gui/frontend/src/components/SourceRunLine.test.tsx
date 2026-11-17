import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import SourceRunLine, { runStamp } from './SourceRunLine'

afterEach(cleanup)

describe('SourceRunLine (control-strategy 06, improvement 2)', () => {
  it('names the run and when it ran, as an ISO date in local time', () => {
    const at = '2026-11-17T15:42:00Z'
    render(<SourceRunLine source="dispatch" at={at} />)
    expect(screen.getByTestId('source-run-from').textContent).toBe(`From the dispatch solve, ${runStamp(at)}`)
    expect(runStamp(at)).toMatch(/^2026-11-1[78] \d\d:\d\d$/)
  })

  it('names no time for a run with no stamp (a project saved before it)', () => {
    render(<SourceRunLine source="study" at={null} />)
    expect(screen.getByTestId('source-run-from').textContent).toBe('From the reliability study')
  })

  it('marks an out-of-date run with the shared pill', () => {
    render(<SourceRunLine source="dispatch" at={null} outOfDate />)
    expect(screen.getByTestId('source-run-stale').textContent).toContain('Out of date')
  })

  it('says the other run has not run, with one link', () => {
    const onClick = vi.fn()
    render(<SourceRunLine source="study" missing={{ run: 'dispatch', action: { label: 'Run the solve', onClick } }} />)
    expect(screen.getByTestId('source-run-missing').textContent).toBe('The dispatch solve has not run · Run the solve ›')
    // The "·" is its own flex item, so the gap is even on both sides (no literal space beside it).
    expect(screen.getByTestId('source-run').textContent).toBe('From the reliability study·The dispatch solve has not run · Run the solve ›')
    fireEvent.click(screen.getByTestId('source-run-action'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('reads an unreadable stamp as none, never "Invalid Date"', () => {
    expect(runStamp('nope')).toBeNull()
    expect(runStamp(undefined)).toBeNull()
    expect(runStamp(Date.parse('2026-11-17T15:42:00Z') / 1000)).toBe(runStamp('2026-11-17T15:42:00Z'))
  })
})
