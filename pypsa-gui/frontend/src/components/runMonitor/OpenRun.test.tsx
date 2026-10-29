// Study harness issue 08: a run's attention notice opens the companion on
// its plan card, with "Back to …" (UX condition 6).
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useUIStore } from '../../store/uiStore'
import { useChatStore } from '../../store/chatStore'
import { getAssistantActivity, type ActivitySnapshot } from '../../api/assistantActivity'
import * as api from '../../api/runMonitor'
import AssistantActivity from '../AssistantActivity'
import { STATES, TASK_ID } from './runFixtures'

vi.mock('../../api/assistantActivity', () => ({ getAssistantActivity: vi.fn(), getAssistantDecision: vi.fn() }))
vi.mock('../../api/runMonitor', () => ({
  getRun: vi.fn(), presentRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(),
}))

const NOTICE: ActivitySnapshot = { version: 1, project_id: 'p1', filter: 'all', cursor: 'c:1', revision: 1,
  resnapshot_required: false, next_page: null, total: 1, warnings: [], scheduling_available: false,
  items: [{ id: `run:${TASK_ID}`, source_id: 'r1', project_id: 'p1', family: 'run', title: 'harness run',
    native_status: 'needs_user', status: 'needs_review', needs_you: true, evidence_available: false, tool_use_id: TASK_ID }] }

describe('a run notice', () => {
  beforeEach(() => {
    cleanup()
    vi.mocked(getAssistantActivity).mockResolvedValue(NOTICE)
    vi.mocked(api.getRun).mockResolvedValue(STATES.gate_fail)
    vi.mocked(api.presentRun).mockResolvedValue()
    useUIStore.setState({ currentProject: 'Project A', flowReturn: null, activeSlidePanel: 'results', uiMode: 'guided' })
    useChatStore.setState({ sessionId: 's1' })
  })

  it('opens the conversation on the plan card, with Back to the origin', async () => {
    const card = document.createElement('div')
    card.setAttribute('data-run-task', TASK_ID)
    card.scrollIntoView = vi.fn()
    document.body.append(card)
    const onConversation = vi.fn()
    try {
      render(<AssistantActivity onConversation={onConversation} />)
      fireEvent.click(await screen.findByTestId('activity-open-run'))
      expect(onConversation).toHaveBeenCalledTimes(1)
      expect(useUIStore.getState().flowReturn).not.toBeNull()
    } finally {
      card.remove()
    }
  })

  it('with no plan card in this conversation, the plan card opens in the notice', async () => {
    render(<AssistantActivity onConversation={vi.fn()} />)
    fireEvent.click(await screen.findByTestId('activity-open-run'))
    expect((await screen.findByTestId('run-failure')).textContent).toMatch(/^Check inputs failed/)
  })
})
