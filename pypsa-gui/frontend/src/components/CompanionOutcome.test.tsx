import { beforeEach, describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useChatStore } from '../store/chatStore'
import CompanionOutcome from './CompanionOutcome'
import { useUIStore } from '../store/uiStore'

const BLOB = '/api/projects/Demo/uploads/0123456789abcdef/blob'
const LATER_BLOB = '/api/projects/Demo/uploads/fedcba9876543210/blob'

describe('CompanionOutcome', () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [], streaming: false, error: null })
    useUIStore.setState({ currentProject: 'Demo', projectMismatch: null })
  })
  it('shows the canonical ordinary answer with the current download', () => {
    useChatStore.setState({
      messages: [
        { id: '1', role: 'assistant', content: 'The network file is ready.', ts: 1 },
        {
          id: '2', role: 'tool', content: '→ build_delivery', tool_name: 'build_delivery', ts: 2,
          tool_result: { download_url: BLOB, filename: 'chart.png' },
        },
      ],
    })
    render(<CompanionOutcome />)
    expect(screen.getByText('The network file is ready.')).toBeTruthy()
    expect(screen.getByText('Answer not checked')).toBeTruthy()
    const anchor = screen.getByTestId('companion-download')
    expect(anchor.getAttribute('href')).toBe(BLOB)
    expect(anchor.getAttribute('download')).toBe('chart.png')
  })

  it('rejects a download url outside the project upload blob shape', () => {
    for (const download_url of ['https://example.invalid/file', '/api/files/abc']) {
      useChatStore.setState({
        messages: [
          { id: '1', role: 'assistant', content: 'Done.', ts: 1 },
          {
            id: '2', role: 'tool', content: '→ build_delivery', ts: 2,
            tool_result: { download_url },
          },
        ],
      })
      const view = render(<CompanionOutcome />)
      expect(screen.queryByTestId('companion-download')).toBeNull()
      view.unmount()
    }
  })

  it('shows a download from the current turn and hides one from an older turn', () => {
    useChatStore.setState({
      messages: [
        {
          id: '1', role: 'tool', content: '→ build_delivery', ts: 1,
          tool_result: { download_url: BLOB, filename: 'old.png' },
        },
        { id: '2', role: 'assistant', content: 'The first file is ready.', ts: 2 },
        { id: '3', role: 'user', content: 'Send another.', ts: 3 },
        { id: '4', role: 'assistant', content: 'The new file is ready.', ts: 4 },
        {
          id: '5', role: 'tool', content: '→ build_delivery', ts: 5,
          tool_result: { download_url: LATER_BLOB, filename: 'chart.png' },
        },
      ],
    })
    const shown = render(<CompanionOutcome />)
    const anchor = screen.getByTestId('companion-download')
    expect(anchor.getAttribute('href')).toBe(LATER_BLOB)
    expect(anchor.getAttribute('download')).toBe('chart.png')
    shown.unmount()

    useChatStore.setState({
      messages: [
        {
          id: '1', role: 'tool', content: '→ build_delivery', ts: 1,
          tool_result: { download_url: BLOB, filename: 'old.png' },
        },
        { id: '2', role: 'user', content: 'Again.', ts: 2 },
        { id: '3', role: 'assistant', content: 'Nothing to download.', ts: 3 },
      ],
    })
    render(<CompanionOutcome />)
    expect(screen.queryByTestId('companion-download')).toBeNull()
    expect(screen.getByText('Nothing to download.')).toBeTruthy()
  })

  it('renders nothing when hidden', () => {
    useChatStore.setState({
      messages: [
        { id: '1', role: 'assistant', content: 'Ready.', ts: 1 },
        {
          id: '2', role: 'tool', content: '→ build_delivery', ts: 2,
          tool_result: { download_url: BLOB, filename: 'chart.png' },
        },
      ],
    })
    render(<CompanionOutcome hidden />)
    expect(screen.queryByTestId('companion-download')).toBeNull()
  })

  it('renders an answer even when the turn has no downloadable result', () => {
    useChatStore.setState({
      messages: [{ id: '1', role: 'assistant', content: 'Ready.', ts: 1 }],
    })
    render(<CompanionOutcome />)
    expect(screen.getByText('Ready.')).toBeTruthy()
    expect(screen.queryByTestId('companion-download')).toBeNull()
  })
})
