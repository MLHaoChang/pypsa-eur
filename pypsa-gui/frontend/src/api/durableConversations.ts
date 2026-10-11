/**
 * Issue 09 — durable parent conversation thread HTTP client.
 *
 * Rebind refreshes scoped bridge credentials and advances generation; the
 * chat store's Live `utteranceGeneration` must follow so stale streams cannot
 * admit.
 */
import { client } from './client'
import { useChatStore } from '../store/chatStore'

export interface DurableThread {
  thread_id: string
  generation: number
  episode_id?: string | null
  goals?: string[]
  constraints?: string[]
  units?: Record<string, string>
  owner_replica_id?: string
  [key: string]: unknown
}

export interface DurableRebindResult extends DurableThread {
  credentials?: {
    token: string
    generation: number
    expires_at: number
    thread_id: string
    project_id: string
    owner_id: string
    episode_id?: string | null
  }
}

/** Apply a durable thread generation into Live utterance admission state. */
export function applyDurableThreadGeneration(
  thread: { generation: number; thread_id?: string },
): void {
  const store = useChatStore.getState()
  if (typeof thread.thread_id === 'string' && thread.thread_id) {
    store.setDurableThreadId(thread.thread_id)
  }
  store.setUtteranceGeneration(thread.generation)
}

export async function createDurableConversation(): Promise<DurableThread> {
  const r = await client.post('/durable-conversations')
  const thread = r.data as DurableThread
  applyDurableThreadGeneration(thread)
  return thread
}

export async function getDurableConversation(threadId: string): Promise<DurableThread> {
  const r = await client.get(`/durable-conversations/${threadId}`)
  const thread = r.data as DurableThread
  applyDurableThreadGeneration(thread)
  return thread
}

export async function rebindDurableConversation(
  threadId: string,
  generation: number,
): Promise<DurableRebindResult> {
  const r = await client.post(`/durable-conversations/${threadId}/rebind`, {
    generation,
  })
  const out = r.data as DurableRebindResult
  applyDurableThreadGeneration(out)
  return out
}

export async function resumeDurableConversation(
  threadId: string,
  generation: number,
): Promise<DurableThread> {
  const r = await client.post(`/durable-conversations/${threadId}/resume`, {
    generation,
  })
  const thread = r.data as DurableThread
  applyDurableThreadGeneration(thread)
  return thread
}

/**
 * Live entry (issue 02/09): create a durable parent thread when none is
 * bound, otherwise rebind so generation and credentials stay current.
 */
export async function ensureLiveDurableThread(): Promise<DurableThread | DurableRebindResult> {
  const { durableThreadId, utteranceGeneration } = useChatStore.getState()
  if (!durableThreadId) {
    return createDurableConversation()
  }
  return rebindDurableConversation(durableThreadId, utteranceGeneration)
}
