import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { useChatStore } from '../store/chatStore'
import CompanionOutcome from './CompanionOutcome'

describe('CompanionOutcome', () => {
  it('shows the latest assistant sentence and a download from the tool result', () => {
    useChatStore.setState({
      messages: [
        { id: '1', role: 'assistant', content: 'The network file is ready.', ts: 1 },
        {
          id: '2', role: 'tool', content: '→ build_delivery', tool_name: 'build_delivery', ts: 2,
          tool_result: { download_url: '/api/files/abc' },
        },
      ],
    })
    render(<CompanionOutcome />)
    const root = screen.getByTestId('companion-outcome').parentElement
    expect(root?.className.split(/\s+/)).toContain('fixed')
    expect(screen.getByTestId('companion-outcome').textContent).toBe('The network file is ready.')
    expect(screen.getByTestId('companion-download').getAttribute('href')).toBe('/api/files/abc')
  })

  it('ignores a download url that is not an api path', () => {
    useChatStore.setState({
      messages: [
        { id: '1', role: 'assistant', content: 'Done.', ts: 1 },
        {
          id: '2', role: 'tool', content: '→ build_delivery', ts: 2,
          tool_result: { download_url: 'https://example.invalid/file' },
        },
      ],
    })
    render(<CompanionOutcome />)
    expect(screen.queryByTestId('companion-download')).toBeNull()
  })
})
