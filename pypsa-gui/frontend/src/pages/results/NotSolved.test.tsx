import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import NotSolved from './NotSolved'
import { useUIStore } from '../../store/uiStore'

afterEach(cleanup)

describe('NotSolved — a result tab before the solve (UX template audit H2)', () => {
  it('says what is missing and why, with no header instructions', () => {
    render(<NotSolved what="Dispatch" />)
    expect(screen.getByText('Not run yet')).toBeTruthy()
    expect(screen.getByText(/Dispatch comes from the hour-by-hour solve/)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/header/i)
  })

  it('runs the solve from right here', () => {
    const before = useUIStore.getState().runSolveRequest
    render(<NotSolved what="Prices" />)
    fireEvent.click(screen.getByRole('button', { name: 'Run the solve' }))
    expect(useUIStore.getState().runSolveRequest).toBe(before + 1)
  })

  it('warns in Guided, and only there, that solving makes a finished study out of date (UX review N4)', () => {
    useUIStore.setState({ uiMode: 'guided' })
    const { unmount } = render(<NotSolved what="Dispatch" />)
    expect(screen.getByText(/solving makes its results out of date/)).toBeTruthy()
    unmount()
    useUIStore.setState({ uiMode: 'expert' })
    render(<NotSolved what="Dispatch" />)
    expect(document.body.textContent).not.toMatch(/out of date/)
  })
})
