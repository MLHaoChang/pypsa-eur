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
    expect(screen.getByTestId('source-run-missing').textContent).toBe('The dispatch solve has not run·Run the solve ›')
    // The "·" is its own flex item, so the gap is even on both sides (no literal space beside it), and so is the one before the link.
    expect(screen.getByTestId('source-run').textContent).toBe('From the reliability studyThe dispatch solve has not run·Run the solve ›')
    fireEvent.click(screen.getByTestId('source-run-action'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('reads an unreadable stamp as none, never "Invalid Date"', () => {
    expect(runStamp('nope')).toBeNull()
    expect(runStamp(undefined)).toBeNull()
    expect(runStamp(Date.parse('2026-11-17T15:42:00Z') / 1000)).toBe(runStamp('2026-11-17T15:42:00Z'))
  })
})

describe('a run action (template-e2e 18)', () => {
  it('is a button that acts in place: no "›", and disabled while a run is queued or going', () => {
    const onClick = vi.fn()
    render(<SourceRunLine source="study" missing={{ run: 'dispatch', action: { label: 'Run the solve', onClick, kind: 'run', disabled: true } }} />)
    const btn = screen.getByTestId('source-run-action') as HTMLButtonElement
    expect(btn.textContent).toBe('Run the solve')
    expect(btn.disabled).toBe(true)
    fireEvent.click(btn)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('puts the missing run on its own row and binds each "·" to the item after it, so no line starts or ends in one (UX 34)', () => {
    render(<SourceRunLine source="study" missing={{ run: 'dispatch', action: { label: 'Run the solve', onClick: vi.fn(), kind: 'run' } }} />)
    for (const dot of screen.getByTestId('source-run').querySelectorAll('[aria-hidden="true"]')) {
      expect(dot.nextElementSibling).not.toBeNull()
      expect(dot.parentElement?.className).toContain('whitespace-nowrap')
      expect(dot.previousElementSibling).toBeNull()
      expect(dot.parentElement?.previousElementSibling).not.toBeNull()
    }
    expect(screen.getByTestId('source-run-missing').className).toContain('basis-full')
  })
})
