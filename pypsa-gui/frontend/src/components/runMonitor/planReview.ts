// Study harness issue 08: which run a plan-review card decides. The run's
// start (`start_task`, `get_task`) returns its envelope; the latest run in
// the conversation whose envelope is not granted is the one under review.
import type { ChatMessage } from '../../store/chatStore'
import type { Envelope } from '../../api/runMonitor'

const object = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null

export function runUnderReview(messages: ChatMessage[]): { taskId: string; envelope: Envelope } | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const result = object(messages[i].tool_result)
    const task = object(result?._task) ?? result
    if (!task || !object(task.run) || typeof task.task_id !== 'string') continue
    const envelope = object(task.envelope) as Envelope | null
    if (!envelope || !Array.isArray(envelope.edits) || !Array.isArray(envelope.runs) || !object(envelope.budget)) return null
    return envelope.state === 'granted' ? null : { taskId: task.task_id, envelope }
  }
  return null
}
