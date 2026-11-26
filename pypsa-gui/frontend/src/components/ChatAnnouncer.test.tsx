import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ChatAnnouncer } from './ChatAnnouncer'
import { useChatAnnouncer } from '../store/chatAnnouncer'

// Assistant-assurance 06 (A5.5, GK G6): one polite, deduplicated status region.
afterEach(cleanup)
beforeEach(() => useChatAnnouncer.setState({ text: '', count: 0 }))

const say = (...lines: string[]) => lines.forEach(line => act(() => useChatAnnouncer.getState().announce(line)))

describe('chat announcer', () => {
  it('A, A is one announcement', () => {
    say('A', 'A')
    expect(useChatAnnouncer.getState().count).toBe(1)
  })

  it('A, B, A is three announcements', () => {
    say('A', 'B', 'A')
    expect(useChatAnnouncer.getState().count).toBe(3)
  })

  it('a blank line is not an announcement', () => {
    say('', '   ')
    expect(useChatAnnouncer.getState().count).toBe(0)
  })

  it('renders one polite atomic status region in the body, and never takes focus', async () => {
    const outside = document.createElement('input')
    document.body.appendChild(outside)
    outside.focus()
    render(<div hidden><ChatAnnouncer /></div>)
    say('Question: Which site?')
    await new Promise(r => setTimeout(r, 0)); await Promise.resolve()
    const region = screen.getByTestId('chat-announcer')
    expect(region.getAttribute('role')).toBe('status')
    expect(region.getAttribute('aria-live')).toBe('polite')
    expect(region.getAttribute('aria-atomic')).toBe('true')
    expect(region.closest('[hidden]')).toBeNull()
    expect(region.textContent).toBe('Question: Which site?')
    expect(document.querySelectorAll('[data-testid="chat-announcer"]')).toHaveLength(1)
    expect(document.activeElement).toBe(outside)
    outside.remove()
  })
})
