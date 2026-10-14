// External agents' cards (chat harness issue 11): polled in local mode only,
// answered through the ordinary confirm endpoint of the agent's session.
import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return { ...actual, getExternalPending: vi.fn(), postChatConfirm: vi.fn() }
})

import ExternalAgentStrip from './ExternalAgentStrip'
import { getExternalPending, postChatConfirm, type ExternalCard } from '../api/chat'
import { setAuthEnabled } from '../auth/config'

const CARD: ExternalCard = {
  session_id: 'mcp-abc', client: 'claude-code', tool_name: 'update_component',
  args: { name: 'Gen1' }, safety_tier: 'write', confirmation_token: 'tok-1', ttl_seconds: 280,
}
const pending = vi.mocked(getExternalPending)
const confirm = vi.mocked(postChatConfirm)

function renderStrip() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ExternalAgentStrip /></QueryClientProvider>)
}

afterAll(() => setAuthEnabled(true))

beforeEach(() => {
  cleanup()
  pending.mockReset()
  confirm.mockReset()
})

describe('ExternalAgentStrip', () => {
  it('polls nothing and renders nothing on a web deployment', async () => {
    setAuthEnabled(true)
    renderStrip()
    await new Promise((r) => setTimeout(r, 20))
    expect(pending).not.toHaveBeenCalled()
    expect(screen.queryByTestId('chat-external-strip')).toBeNull()
  })

  it('renders nothing locally when no agent is waiting', async () => {
    setAuthEnabled(false)
    pending.mockResolvedValue([])
    renderStrip()
    await waitFor(() => expect(pending).toHaveBeenCalled())
    expect(screen.queryByTestId('chat-external-strip')).toBeNull()
  })

  it.each([['approve', 'chat-external-approve'], ['deny', 'chat-external-deny']] as const)(
    'shows the waiting call and answers it: %s', async (decision, testid) => {
      setAuthEnabled(false)
      pending.mockResolvedValue([CARD])
      confirm.mockResolvedValue({ ok: true })
      renderStrip()
      await waitFor(() => expect(screen.getByTestId('chat-external-card')).toBeTruthy())
      expect(screen.getByTestId('chat-external-client').textContent).toBe('claude-code')
      expect(screen.getByTestId('chat-external-tool').textContent).toBe('update_component')
      fireEvent.click(screen.getByTestId(testid))
      await waitFor(() => expect(confirm).toHaveBeenCalledWith('mcp-abc', { token: 'tok-1', decision }))
    })
})
