import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatStore } from '../store/chatStore'
import {
  applyDurableThreadGeneration,
  ensureLiveDurableThread,
  rebindDurableConversation,
} from './durableConversations'
import { client } from './client'

vi.mock('./client', () => ({
  client: { post: vi.fn(), get: vi.fn() },
}))

beforeEach(() => {
  useChatStore.setState({
    durableThreadId: null,
    utteranceGeneration: 1,
  })
  vi.mocked(client.post).mockReset()
})

describe('applyDurableThreadGeneration', () => {
  it('stores thread id and bumps utteranceGeneration', () => {
    applyDurableThreadGeneration({ thread_id: 't-1', generation: 4 })
    expect(useChatStore.getState().durableThreadId).toBe('t-1')
    expect(useChatStore.getState().utteranceGeneration).toBe(4)
  })
})

describe('rebindDurableConversation', () => {
  it('syncs utteranceGeneration from the rebind response', async () => {
    vi.mocked(client.post).mockResolvedValue({
      data: {
        thread_id: 't-rebind',
        generation: 3,
        credentials: { token: 'x', generation: 3 },
      },
    })
    const out = await rebindDurableConversation('t-rebind', 2)
    expect(client.post).toHaveBeenCalledWith(
      '/durable-conversations/t-rebind/rebind',
      { generation: 2 },
    )
    expect(out.generation).toBe(3)
    expect(useChatStore.getState().utteranceGeneration).toBe(3)
    expect(useChatStore.getState().durableThreadId).toBe('t-rebind')
  })
})

describe('ensureLiveDurableThread', () => {
  it('creates a durable thread when Live has none bound', async () => {
    vi.mocked(client.post).mockResolvedValue({
      data: { thread_id: 't-new', generation: 1 },
    })
    const out = await ensureLiveDurableThread()
    expect(client.post).toHaveBeenCalledWith('/durable-conversations')
    expect(out.thread_id).toBe('t-new')
    expect(useChatStore.getState().durableThreadId).toBe('t-new')
    expect(useChatStore.getState().utteranceGeneration).toBe(1)
  })

  it('rebinds an existing Live durable thread', async () => {
    useChatStore.setState({ durableThreadId: 't-existing', utteranceGeneration: 2 })
    vi.mocked(client.post).mockResolvedValue({
      data: { thread_id: 't-existing', generation: 3 },
    })
    const out = await ensureLiveDurableThread()
    expect(client.post).toHaveBeenCalledWith(
      '/durable-conversations/t-existing/rebind',
      { generation: 2 },
    )
    expect(out.generation).toBe(3)
    expect(useChatStore.getState().utteranceGeneration).toBe(3)
  })
})
