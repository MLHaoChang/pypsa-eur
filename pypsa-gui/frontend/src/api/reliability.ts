/** Availability is advisory; the server still admits every checked request. */
import { client } from './client'

export interface CheckedAnswerCapabilities {
  version: 1
  mode: 'disabled' | 'shadow' | 'pilot' | 'strict'
  profiles: Record<string, { enabled: boolean }>
}

export async function getCheckedAnswerCapabilities(): Promise<CheckedAnswerCapabilities> {
  const { data } = await client.get('/chat/reliability/capabilities', { skipErrorToast: true })
  if (!data || data.version !== 1 || !['disabled', 'shadow', 'pilot', 'strict'].includes(data.mode)
    || !data.profiles || typeof data.profiles !== 'object' || Array.isArray(data.profiles)
    || Object.keys(data.profiles).length > 32
    || Object.values(data.profiles).some(p => !p || typeof p !== 'object'
      || typeof (p as { enabled?: unknown }).enabled !== 'boolean')) {
    throw new Error('Answer-check availability could not be verified.')
  }
  return data
}

export const CHECKED_ANSWERS_QUERY_KEY = ['chat', 'checked-answer-capabilities']
