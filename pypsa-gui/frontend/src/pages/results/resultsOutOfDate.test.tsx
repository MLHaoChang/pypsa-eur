// A results reader's 204 says why there are no figures: with `X-Results-State: out_of_date` the inputs changed
// after the last run; without it nothing has run. The shared empty states say each in one way.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import NotSolved from './NotSolved'
import { OUT_OF_DATE_TEXT } from '../../components/ResultsOutOfDate'
import { noteResultsResponse, useResultsOutOfDate } from '../../store/resultsOutOfDate'
import { useUIStore } from '../../store/uiStore'

const hdr = (v?: string) => ({ get: (k: string) => (k.toLowerCase() === 'x-results-state' ? v : undefined) })

beforeEach(() => { useResultsOutOfDate.setState({ outOfDate: false }); useUIStore.setState({ uiMode: 'expert' } as never) })
afterEach(cleanup)

describe('results 204 states', () => {
  it('204 with the header: the out-of-date sentence and the Run control', () => {
    const run = vi.fn()
    useUIStore.setState({ requestRunSolve: run } as never)
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
    expect(useResultsOutOfDate.getState().outOfDate).toBe(true)
  })
})
