// The goal strip (chat harness issue 17): the session's goal and its round
// budget. Pause and Stop act on the server; only a person resumes, and a
// resume sends the turn that continues the goal.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return { ...actual, postChatGoal: vi.fn() }
})

import { GoalStrip } from './ChatPanel'
import { postChatGoal, type GoalState } from '../api/chat'
import { useChatStore } from '../store/chatStore'

const ACTIVE: GoalState = {
  objective: 'make the network solve', max_rounds: 5, rounds_used: 2, phase: 'active', reason: null,
}
const post = vi.mocked(postChatGoal)

beforeEach(() => {
  cleanup()
  post.mockReset()
  useChatStore.setState({ goal: null, sessionId: 'sess-1', requestQueue: [], lastRequest: null })
})

describe('GoalStrip', () => {
  it('renders nothing without a goal or once it is complete', () => {
    render(<GoalStrip />)
    expect(screen.queryByTestId('chat-goal-strip')).toBeNull()
    useChatStore.setState({ goal: { ...ACTIVE, phase: 'complete' } })
    render(<GoalStrip />)
    expect(screen.queryByTestId('chat-goal-strip')).toBeNull()
  })

  it('shows the objective and the rounds, and Pause asks the server', async () => {
    useChatStore.setState({ goal: ACTIVE })
    post.mockResolvedValue({ ok: true, goal: { ...ACTIVE, phase: 'paused', reason: 'user' } })
    render(<GoalStrip />)
    expect(screen.getByTestId('chat-goal-objective').textContent).toBe('make the network solve')
    expect(screen.getByTestId('chat-goal-rounds').textContent).toBe('round 2 of 5')
    expect(screen.queryByTestId('chat-goal-resume')).toBeNull()
    fireEvent.click(screen.getByTestId('chat-goal-pause'))
    await waitFor(() => expect(useChatStore.getState().goal?.phase).toBe('paused'))
    expect(post).toHaveBeenCalledWith('sess-1', 'pause')
    expect(useChatStore.getState().requestQueue).toHaveLength(0)
  })

  it('says why a goal stopped, and Resume grants rounds and sends the turn', async () => {
    useChatStore.setState({ goal: { ...ACTIVE, phase: 'blocked', reason: 'budget' } })
    post.mockResolvedValue({ ok: true, goal: { ...ACTIVE, rounds_used: 0 } })
    render(<GoalStrip />)
    expect(screen.getByTestId('chat-goal-phase').textContent).toBe('blocked — a usage limit was reached')
    fireEvent.click(screen.getByTestId('chat-goal-resume'))
    await waitFor(() => expect(useChatStore.getState().requestQueue).toHaveLength(1))
    expect(post).toHaveBeenCalledWith('sess-1', 'resume')
    expect(useChatStore.getState().requestQueue[0].text).toBe('Continue toward the goal.')
    expect(useChatStore.getState().goal?.phase).toBe('active')
  })

  it('Stop clears the goal', async () => {
    useChatStore.setState({ goal: ACTIVE })
    post.mockResolvedValue({ ok: true, goal: null })
    render(<GoalStrip />)
    fireEvent.click(screen.getByTestId('chat-goal-stop'))
    await waitFor(() => expect(useChatStore.getState().goal).toBeNull())
    expect(post).toHaveBeenCalledWith('sess-1', 'stop')
  })
})
