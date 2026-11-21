// A refusal that disables Apply is visible text (walkthrough issue 08): never
// behind a disclosure, since the disabled button depends on it.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { WarningList } from './SeriesKit'

afterEach(() => cleanup())

describe('WarningList', () => {
  it('shows a blocking refusal as visible text, not behind a disclosure', () => {
    render(<WarningList warnings={['not_applicable: this load follows the set-up']} testId="w" />)
    const li = screen.getByTestId('w').querySelector('li') as HTMLElement
    expect(li.querySelector('details')).toBeNull()
    expect(li.textContent).toContain('this load follows the set-up')
  })

  it('keeps a non-blocking warning behind its disclosure', () => {
    render(<WarningList warnings={['time_basis_assumed_utc: applied unshifted']} testId="w" />)
    expect(screen.getByTestId('w').querySelector('details')).not.toBeNull()
  })
})
