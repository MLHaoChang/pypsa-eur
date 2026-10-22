import { client } from './client'
import { parseApproval, verifyApprovedText, type ApprovedAnswerFrame } from '../utils/answerAssurance'
import type { ChatMessage } from '../store/chatStore'

const PROFILES = new Set(['generator-dispatch/1', 'generator-cost/1', 'network-linear-cost/1',
  'storage-unit-dispatch/1', 'storage-unit-cost/1', 'carrier-energy/1',
  'branch-loading/1', 'network-loading/1', 'lost-load/1',
  'bus-price/1', 'generator-emissions/1', 'network-emissions/1', 'curtailment/1',
  'capacity/1', 'storage-cycles/1', 'energy-balance/1', 'site-import/1', 'investment-case/1'])

export interface OwnedCheckedAnswer extends ApprovedAnswerFrame {
  text: string
  project_id: string
}

function canonical(value: unknown, depth = 0): unknown {
  if (depth > 8) throw new Error('Receipt too deeply nested')
  if (Array.isArray(value)) return value.map(v => canonical(v, depth + 1))
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map(key => [key, canonical((value as Record<string, unknown>)[key], depth + 1)]),
  )
  return value
}

/** Existing owned GETs recheck actor/ACL, source availability and stored hashes.
 * A receipt in the client is a locator, never permission to publish another text.
 */
export async function getOwnedCheckedAnswer(message: ChatMessage, signal: AbortSignal): Promise<OwnedCheckedAnswer> {
  if (!message.answer_id || !/^[A-Za-z0-9_-]{1,128}$/.test(message.answer_id) || !message.receipt) {
    throw new Error('Checked answer identity missing')
  }
  // Revalidation must reach the owned handlers, including after permission or
  // source changes. A browser/proxy cache cannot supply fresh authorization.
  const options = { signal, skipErrorToast: true, headers: { 'Cache-Control': 'no-cache' } }
  const [answer, context] = await Promise.all([
    client.get(`/chat/answers/${encodeURIComponent(message.answer_id)}`, options),
    client.get('/chat/reliability/context', options),
  ])
  const data = answer.data as Record<string, unknown> | null
  if (!data || typeof data !== 'object' || Array.isArray(data)
    || new TextEncoder().encode(JSON.stringify(data)).byteLength > 128 * 1024) {
    throw new Error('Checked answer record malformed')
  }
  const approved = parseApproval(data)
  if (!approved || data.answer_id !== message.answer_id || data.text !== message.content
    || typeof data.project_id !== 'string' || !data.project_id || data.project_id.length > 128
    || context.data?.version !== 1 || context.data?.project_id !== data.project_id
    || (message.answer_turn_id !== undefined && message.answer_turn_id !== data.turn_id)
    || (approved.receipt.request_scope?.project_id !== undefined
      && approved.receipt.request_scope.project_id !== data.project_id)
    || JSON.stringify(canonical(approved.receipt)) !== JSON.stringify(canonical(message.receipt))) {
    throw new Error('Checked answer binding mismatch')
  }
  const scope = approved.receipt.request_scope
  if ((scope && !PROFILES.has(scope.profile))
    || (approved.receipt.request_complete === true && (!scope
      || !approved.receipt.result_slots?.length
      || approved.receipt.result_slots.some(s => s.disposition !== 'answered')))
    || !(await verifyApprovedText(message.content, approved.receipt.content_sha256)) || signal.aborted) {
    throw new Error('Checked answer contract unavailable')
  }
  return { ...approved, text: message.content, project_id: data.project_id }
}
