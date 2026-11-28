// A results reader's 204 says why there are no figures: with `X-Results-State: out_of_date` the inputs changed
// after the last run; without it nothing has run. The shared empty states say each in one way.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import NotSolved from './NotSolved'
import { OUT_OF_DATE_TEXT } from '../../components/ResultsOutOfDate'
import { noteResultsResponse, useResultsOutOfDateFlag } from '../../store/resultsOutOfDate'
import { useUIStore } from '../../store/uiStore'

const kinds = vi.hoisted(() => ({ stale: ['Optimisation'] as string[] }))
vi.mock('../../hooks/useStaleRunKinds', () => ({ useStaleRunKinds: () => kinds.stale }))

const hdr = (v?: string) => ({ get: (k: string) => (k.toLowerCase() === 'x-results-state' ? v : undefined) })

beforeEach(() => {
  kinds.stale = ['Optimisation']
  useResultsOutOfDateFlag.setState({ project: null })
  useUIStore.setState({ uiMode: 'expert', currentProject: 'A' } as never)
})
afterEach(cleanup)

describe('results 204 states', () => {
  it('204 with the header: the out-of-date sentence and the Run control', () => {
    const run = vi.fn()
    useUIStore.setState({ requestDispatchSolve: run } as never)
    noteResultsResponse('/results/generators', 204, hdr('out_of_date'))
    render(<NotSolved what="Dispatch" />)
    expect(screen.getByTestId('results-out-of-date').textContent).toContain(
      'Out of date: the inputs changed after the last run. Run again to see the figures.')
    expect(OUT_OF_DATE_TEXT).toBe('Out of date: the inputs changed after the last run. Run again to see the figures.')
    expect(screen.queryByText('Not run yet')).toBeNull()
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(1)
    fireEvent.click(buttons[0])
    expect(run).toHaveBeenCalledOnce()
  })
  it('204 without the header: the old Not run yet state', () => {
    noteResultsResponse('/results/generators', 204, hdr('out_of_date'))
    noteResultsResponse('/results/generators', 204, hdr(undefined))
    render(<NotSolved what="Dispatch" />)
    expect(screen.getByText('Not run yet')).toBeTruthy()
    expect(screen.queryByText(/Out of date/)).toBeNull()
  })
  it('a 200, or a 204 outside /results, leaves the flag alone', () => {
    noteResultsResponse('/results/generators', 204, hdr('out_of_date'))
    noteResultsResponse('/results/lines', 200, hdr(undefined))
    noteResultsResponse('/network/foo', 204, hdr(undefined))
    expect(useResultsOutOfDateFlag.getState().project).toBe('A')
  })
  it('another project, or a flow that no longer reads out of date, never shows the sentence', () => {
    noteResultsResponse('/results/generators', 204, hdr('out_of_date'))
    useUIStore.setState({ currentProject: 'B' } as never)
    const { unmount } = render(<NotSolved what="Dispatch" />)
    expect(screen.getByText('Not run yet')).toBeTruthy()
    expect(screen.queryByTestId('results-out-of-date')).toBeNull()
    unmount()
    useUIStore.setState({ currentProject: 'A' } as never)
    kinds.stale = []
    render(<NotSolved what="Dispatch" />)
    expect(screen.getByText('Not run yet')).toBeTruthy()
  })
})
