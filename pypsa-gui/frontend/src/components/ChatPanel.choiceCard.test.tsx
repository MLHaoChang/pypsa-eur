// The Choice card (chat harness issue 04): what `ask_user` renders, and what
// a pick does — it SENDS the option label as the next user message through
// the request queue, labelled "<title>: <option>" (owner decision Q4: the
// turn does not block on the card).
import { beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ChoiceCard } from './ChatPanel'
import { useChatStore } from '../store/chatStore'

const CHOICE = {
  tool_use_id: 'tu_1',
  title: 'Q1 — Which storage option?',
  question: 'The verdict flips on this one.',
  options: [
    { label: 'Battery', description: 'Cheapest for a 2 h peak.', recommended: true },
    { label: 'Thermal store' },
  ],
  allow_free_text: true,
}

beforeEach(() => {
  cleanup()
  useChatStore.setState({ choice: null, requestQueue: [], lastRequest: null, streaming: false })
})

describe('ChoiceCard', () => {
  it('renders nothing without a pending choice', () => {
    render(<ChoiceCard />)
    expect(screen.queryByTestId('chat-choice-card')).toBeNull()
  })

  it('shows the title, the question, the options and the recommendation', () => {
    useChatStore.setState({ choice: CHOICE })
    render(<ChoiceCard />)
    expect(screen.getByTestId('chat-choice-title').textContent).toBe(CHOICE.title)
    expect(screen.getByTestId('chat-choice-question').textContent).toBe(CHOICE.question)
    const options = screen.getAllByTestId('chat-choice-option')
    expect(options.map((o) => o.getAttribute('data-recommended'))).toEqual(['true', null])
    expect(screen.getByTestId('chat-choice-recommended')).toBeTruthy()
    expect(screen.getByTestId('chat-choice-free-text')).toBeTruthy()
  })

  it('a pick queues the label as the next message and clears the card', () => {
    useChatStore.setState({ choice: CHOICE })
    render(<ChoiceCard />)
    fireEvent.click(screen.getAllByTestId('chat-choice-option')[1])
    const q = useChatStore.getState().requestQueue
    expect(q).toHaveLength(1)
    expect(q[0].text).toBe('Thermal store')
    expect(q[0].label).toBe('Q1 — Which storage option?: Thermal store')
    expect(q[0].source).toBe('choice-card')
    expect(useChatStore.getState().choice).toBeNull()
  })

  it('hides the free-text hint when the question forbids it', () => {
    useChatStore.setState({ choice: { ...CHOICE, allow_free_text: false } })
    render(<ChoiceCard />)
    expect(screen.queryByTestId('chat-choice-free-text')).toBeNull()
  })
})
