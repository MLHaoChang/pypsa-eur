// Characterization of BusAutocomplete, written BEFORE spec D4's three additive
// adaptations (allowUnknown, scroll/resize reposition, arrow stopPropagation).
// Zero coverage today; CreationForm.tsx:514 is its only caller and must behave
// identically after Task 10.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import BusAutocomplete from './BusAutocomplete'

const BUSES = ['Bus A', 'Bus B', 'North', 'north_2', 'South']

afterEach(() => vi.restoreAllMocks())

describe('BusAutocomplete filtering — behaviour as of e8614a35', () => {
  it('filters case-insensitively on a substring', async () => {
    render(<BusAutocomplete value="nor" onChange={() => {}} buses={BUSES} />)
    await userEvent.click(screen.getByRole('textbox'))
    expect(screen.getByText('North')).toBeTruthy()
    expect(screen.getByText('north_2')).toBeTruthy()
    expect(screen.queryByText('South')).toBeNull()
  })

  it('caps the visible suggestions at 60', async () => {
    const many = Array.from({ length: 200 }, (_, i) => `B${i}`)
    render(<BusAutocomplete value="B" onChange={() => {}} buses={many} />)
    await userEvent.click(screen.getByRole('textbox'))
    expect(document.querySelectorAll('li').length).toBe(60)
  })

  it('shows the auto-create warning for a value matching no bus', () => {
    render(<BusAutocomplete value="Nrth" onChange={() => {}} buses={BUSES} />)
    expect(screen.getByText(/created automatically/)).toBeTruthy()
  })

  it('treats a case-differing name as an exact match, so no warning appears', () => {
    // This is the behaviour D4 calls out as too lax for a grid: PyPSA's index
    // lookup is case-sensitive, so "NORTH" is a dangling reference. The widget
    // accepts it today; Task 10 does NOT change this — gridEdit rejects it.
    render(<BusAutocomplete value="NORTH" onChange={() => {}} buses={BUSES} />)
    expect(screen.queryByText(/created automatically/)).toBeNull()
  })

  it('shows no warning for an empty value', () => {
    render(<BusAutocomplete value="" onChange={() => {}} buses={BUSES} />)
    expect(screen.queryByText(/created automatically/)).toBeNull()
  })
})

describe('BusAutocomplete keyboard — behaviour as of e8614a35', () => {
  it('opens the dropdown on focus', () => {
    render(<BusAutocomplete value="" onChange={() => {}} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    expect(document.querySelectorAll('li').length).toBeGreaterThan(0)
  })

  it('ArrowDown stops propagation so it cannot reach the grid', () => {
    // Flipped by Task 10 (spec D4 adaptation 3). With the dropdown open OR
    // closed, an arrow key belongs to this widget: letting it bubble would move
    // the grid's active cell out from under an open editor (criterion 23).
    const outer = vi.fn()
    render(
      <div onKeyDown={outer}>
        <BusAutocomplete value="" onChange={() => {}} buses={BUSES} />
      </div>,
    )
    const input = screen.getByRole('textbox')
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(outer).not.toHaveBeenCalled()
  })

  it('Enter selects the highlighted suggestion', () => {
    const onChange = vi.fn()
    render(<BusAutocomplete value="Bus" onChange={onChange} buses={BUSES} />)
    const input = screen.getByRole('textbox')
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('Bus A')
  })

  it('clicking a suggestion commits it', () => {
    const onChange = vi.fn()
    render(<BusAutocomplete value="Sou" onChange={onChange} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    fireEvent.mouseDown(screen.getByText('South'))
    expect(onChange).toHaveBeenCalledWith('South')
  })
})

describe('BusAutocomplete dropdown geometry — behaviour as of e8614a35', () => {
  it('renders the list fixed-positioned so it escapes a scroll container', () => {
    render(<BusAutocomplete value="Bus" onChange={() => {}} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    const list = document.querySelector('ul') as HTMLElement
    expect(list.style.position).toBe('fixed')
  })

  it('repositions on scroll so it follows the grid body', () => {
    // Flipped by Task 10 (adaptation 2). jsdom reports a zero rect, so this
    // asserts that a recompute HAPPENED, not a pixel value: the listener is
    // capture-phase because a scroll on the table body does not bubble.
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect')
    render(<BusAutocomplete value="Bus" onChange={() => {}} buses={BUSES} />)
    fireEvent.focus(screen.getByRole('textbox'))
    const before = spy.mock.calls.length
    fireEvent.scroll(document, {})
    expect(spy.mock.calls.length).toBeGreaterThan(before)
  })
})

describe('BusAutocomplete allowUnknown — added by Task 10', () => {
  it('still promises auto-creation by default, for the creation form', () => {
    render(<BusAutocomplete value="Nrth" onChange={() => {}} buses={BUSES} />)
    expect(screen.getByText(/created automatically/)).toBeTruthy()
  })

  it('refuses instead of promising when allowUnknown is false', () => {
    render(
      <BusAutocomplete value="Nrth" onChange={() => {}} buses={BUSES} allowUnknown={false} />,
    )
    expect(screen.queryByText(/created automatically/)).toBeNull()
    expect(screen.getByText(/no bus named/i)).toBeTruthy()
  })

  it('says nothing when the value matches a real bus', () => {
    render(
      <BusAutocomplete value="North" onChange={() => {}} buses={BUSES} allowUnknown={false} />,
    )
    expect(screen.queryByText(/no bus named/i)).toBeNull()
  })
})
