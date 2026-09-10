// The meter shows exact token counts, not a currency estimate.
//
// The EUR figure was derived from a hardcoded price table plus
// USD_PER_EUR = 1.08. We have no verified pricing for the Claude 5 models,
// and the alternatives were inventing numbers or displaying known-wrong ones.
// Token counts are already accumulated and cannot go stale.
//
// This asserts REAL accumulated values, not merely that a number renders — a
// meter hardwired to zero would satisfy the weaker check.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import ChatPanel from './ChatPanel'

vi.mock('../api/chat', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/chat')>()
  return {
    ...actual,
    createChatStream: vi.fn(),
    getChatHistory: vi.fn().mockResolvedValue({ turns: [], last_session_id: null, bound_project: null }),
    postChatAbort: vi.fn(),
    postChatConfirm: vi.fn(),
    getApiKeySettings: vi.fn().mockResolvedValue({
      configured: true, source: 'settings', hint: '…wxyz',
      overridden_by_environment: false, storage_path: '/tmp/user.env',
    }),
    putApiKeySettings: vi.fn(),
    deleteApiKeySettings: vi.fn(),
  }
})

vi.mock('../api/uploads', () => ({
  deleteUpload: vi.fn(), getUploadBlobUrl: vi.fn(),
  listUploads: vi.fn().mockResolvedValue([]), uploadFile: vi.fn(),
  UploadError: class UploadError extends Error {},
}))

afterEach(() => cleanup())
beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  useChatStore.setState({
    sessionId: null, pending: null, messages: [],
    usage: {
      input_tokens: 12_345, output_tokens: 678,
      cache_read_tokens: 9_000, cache_create_tokens: 0,
    },
  })
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><ChatPanel /></QueryClientProvider>,
  )
}

it('shows exact input, output and cache-read token counts', async () => {
  renderPanel()
  const meter = await screen.findByTestId('chat-usage-meter')
  expect(meter.textContent).toContain('12,345')
  expect(meter.textContent).toContain('678')
  expect(meter.textContent).toContain('9,000')
})

it('shows no currency figure', async () => {
  renderPanel()
  const meter = await screen.findByTestId('chat-usage-meter')
  expect(meter.textContent).not.toMatch(/[€$]/)
})
