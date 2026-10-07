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

  // ── issue 15: detail, multi-select, plan review ─────────────────────────
  it('renders the detail as Markdown under the question', () => {
    useChatStore.setState({ choice: { ...CHOICE, detail: '**Cost** first\n\n- one\n- two' } })
    render(<ChoiceCard />)
    const detail = screen.getByTestId('chat-choice-detail')
    expect(detail.querySelector('strong')?.textContent).toBe('Cost')
    expect(detail.querySelectorAll('li')).toHaveLength(2)
  })

  it('shows no detail block when there is none', () => {
    useChatStore.setState({ choice: CHOICE })
    render(<ChoiceCard />)
    expect(screen.queryByTestId('chat-choice-detail')).toBeNull()
  })

  it('multi-select toggles options and sends the picks as one message', () => {
    const multi = { ...CHOICE, multi_select: true,
      options: [{ label: 'N-1', recommended: true }, { label: 'Short circuit' }, { label: 'Harmonics' }] }
    useChatStore.setState({ choice: multi })
    render(<ChoiceCard />)
    const send = screen.getByTestId('chat-choice-send')
    expect(send.hasAttribute('disabled')).toBe(true)
    const options = screen.getAllByTestId('chat-choice-option')
    fireEvent.click(options[2])
    fireEvent.click(options[0])
    expect(options[0].getAttribute('aria-pressed')).toBe('true')
    expect(options[1].getAttribute('aria-pressed')).toBe('false')
    expect(useChatStore.getState().requestQueue).toHaveLength(0)   // toggling sends nothing
    fireEvent.click(options[2])                                    // un-pick
    expect(send.hasAttribute('disabled')).toBe(false)
    fireEvent.click(send)
    const q = useChatStore.getState().requestQueue
    expect(q).toHaveLength(1)
    expect(q[0].text).toBe('N-1')
    expect(useChatStore.getState().choice).toBeNull()
  })

  it('multi-select joins several picks in option order', () => {
    const multi = { ...CHOICE, multi_select: true,
      options: [{ label: 'N-1' }, { label: 'Short circuit' }, { label: 'Harmonics' }] }
    useChatStore.setState({ choice: multi })
    render(<ChoiceCard />)
    const options = screen.getAllByTestId('chat-choice-option')
    fireEvent.click(options[2])
    fireEvent.click(options[0])
    fireEvent.click(screen.getByTestId('chat-choice-send'))
    const q = useChatStore.getState().requestQueue
    expect(q[0].text).toBe('N-1; Harmonics')
    expect(q[0].label).toBe('Q1 — Which storage option?: N-1; Harmonics')
  })

  it('a plan review is marked as one and shows the plan', () => {
    useChatStore.setState({ choice: { ...CHOICE, intent: 'plan_review', detail: '# Plan\n1. Add a battery',
      options: [{ label: 'Approve', recommended: true }, { label: 'Revise' }] } })
    render(<ChoiceCard />)
    expect(screen.getByTestId('chat-choice-card').getAttribute('data-intent')).toBe('plan_review')
    expect(screen.getByTestId('chat-choice-intent').textContent).toBe('Plan review')
    expect(screen.getByTestId('chat-choice-detail').textContent).toContain('Add a battery')
    fireEvent.click(screen.getAllByTestId('chat-choice-option')[0])
    expect(useChatStore.getState().requestQueue[0].text).toBe('Approve')
  })
})

