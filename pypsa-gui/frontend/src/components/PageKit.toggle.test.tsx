// UX assessment Q9: the Toggle was a clickable <div>, so a keyboard or screen
// reader user could neither reach it nor learn its state.
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Toggle } from './PageKit'

describe('Toggle is a real switch', () => {
  it('exposes role=switch with its state and label', () => {
    render(<Toggle on={true} label="Autosave" />)
    const sw = screen.getByRole('switch', { name: 'Autosave' })
    expect(sw.getAttribute('aria-checked')).toBe('true')
  })

  it('toggles from the keyboard', async () => {
    const onChange = vi.fn()
    render(<Toggle on={false} onChange={onChange} label="Autosave" />)
    await userEvent.tab()
    expect(document.activeElement).toBe(screen.getByRole('switch'))
    await userEvent.keyboard(' ')
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
