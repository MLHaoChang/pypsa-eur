// The workflow strip (chat harness issue 06): shows the session's step and
// lets the user ask to leave; the pair also rides `ui_context.workflow`.
import { beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { WorkflowStrip } from './ChatPanel'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import { buildUiContext } from '../utils/uiContext'

const STATE = {
  id: 'build-network', title: 'Build a network',
  step: 'buses', step_title: 'Buses and carriers', step_index: 2, step_count: 6,
}

beforeEach(() => {
  cleanup()
  useChatStore.setState({ workflow: null, requestQueue: [], lastRequest: null })
  useUIStore.setState({ activeSlidePanel: null, selectedComponent: null, compareRailOpen: false,
    resultsSnapshotIdx: 0, canvasView: 'blank', uiMode: 'expert' })
})

describe('WorkflowStrip', () => {
  it('renders nothing without a workflow', () => {
    render(<WorkflowStrip />)
    expect(screen.queryByTestId('chat-workflow-strip')).toBeNull()
  })

  it('shows the workflow and the step, and Leave asks the assistant to end it', () => {
    useChatStore.setState({ workflow: STATE })
    render(<WorkflowStrip />)
    expect(screen.getByTestId('chat-workflow-title').textContent).toBe('Build a network')
    expect(screen.getByTestId('chat-workflow-step').textContent).toBe('step 2 of 6: Buses and carriers')
    fireEvent.click(screen.getByTestId('chat-workflow-leave'))
    const q = useChatStore.getState().requestQueue
    expect(q).toHaveLength(1)
    expect(q[0].text).toBe('Please end the current workflow.')
    expect(q[0].label).toBe('Leave the workflow')
  })
})

describe('ui_context.workflow', () => {
  it('is absent without a workflow and carries the pair with one', () => {
    expect(buildUiContext()).toBeNull()
    useChatStore.setState({ workflow: STATE })
    expect(buildUiContext()).toEqual({ workflow: { id: 'build-network', step: 'buses' } })
  })
})
