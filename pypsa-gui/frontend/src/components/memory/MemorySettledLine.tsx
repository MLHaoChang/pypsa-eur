import { toolOutcomeOf } from '../toolOutcome'
import { useMemoryStore } from '../../store/memoryStore'
import { MEMORY_TOOL } from './MemoryCard'

// A memory card's call, once answered, reads as ONE line in the transcript
// (UX M4), in both modes. Every word comes from the transcript itself (the
// tool's result or refusal), so it stays true after a reload or Stop (#1220).
// The one exception is the "not suggested again" clause: it is shown only
// when this tab recorded the dismissal, and a reload drops it, never adds it.

interface Row {
  role: string
  content: string
  tool_use_id?: string
  tool_name?: string
  tool_result?: unknown
}

export type MemorySettled =
  | { state: 'remembered'; id: string | null }
  | { state: 'not_now' }
  | { state: 'expired' }
  | { state: 'not_saved' }
  | { state: 'no_answer' }
  | { state: 'waiting' }

/** Is this transcript row part of a `propose_memory` call? */
export function isMemoryRow(m: Row): boolean {
  return m.role === 'tool' && !!m.tool_use_id
    && (m.tool_name === MEMORY_TOOL || m.content === `→ ${MEMORY_TOOL}`)
}

/**
 * The settled state of the memory call at `index`, or null when a LATER row of
 * the same call will carry the line (one line per call).
 */
export function memorySettledAt(
  messages: ReadonlyArray<Row>, index: number, live: { streaming: boolean; pending: boolean },
): MemorySettled | null {
  const id = messages[index].tool_use_id
  let last = -1
  messages.forEach((m, i) => { if (m.role === 'tool' && m.tool_use_id === id) last = i })
  if (last !== index) return null
  const rows = messages.filter(m => m.role === 'tool' && m.tool_use_id === id)
  for (const r of rows) {
    const block = toolOutcomeOf(r.tool_result, r.tool_name)
    if (/^denied: /.test(r.content) || (block?.outcome === 'denied' && block.reason === 'confirmation_denied')) {
      return { state: 'not_now' }
    }
    if (block?.outcome === 'denied' && block.reason === 'confirmation_expired') return { state: 'expired' }
  }
  for (const r of rows) {
    const res = r.tool_result as { id?: unknown; status?: unknown } | null | undefined
    if (res && typeof res === 'object' && typeof res.id === 'string' && res.status === 'confirmed') {
      return { state: 'remembered', id: res.id }
    }
  }
  if (rows.some(r => r.content.startsWith('✗') || toolOutcomeOf(r.tool_result, r.tool_name) != null)) {
    return { state: 'not_saved' }
  }
  if (rows.some(r => r.tool_result != null)) return { state: 'remembered', id: null }
  if (!live.streaming && !live.pending
    && messages.slice(index + 1).some(o => o.role === 'user' || o.role === 'assistant')) {
    return { state: 'no_answer' }
  }
  return { state: 'waiting' }
}

export default function MemorySettledLine({ settled, toolUseId }: {
  settled: MemorySettled
  toolUseId: string
}) {
  const recorded = useMemoryStore(s => s.dismissed.has(toolUseId))
  const openMemory = useMemoryStore(s => s.openMemory)
  const words = settled.state === 'remembered' ? 'Remembered'
    : settled.state === 'not_now'
      ? (recorded ? 'Not now: not suggested again for 30 days' : 'Not now: not remembered')
      : settled.state === 'expired' ? 'Not remembered: the card timed out'
        : settled.state === 'not_saved' ? 'Not remembered: the save was refused'
          : settled.state === 'no_answer' ? 'Not remembered: the turn ended first'
            : 'Remember this? Answer on the card below'
  return (
    <span className="font-sans text-[12px] text-text" data-testid="memory-settled"
      data-state={settled.state}>
      {words}
      {settled.state === 'remembered' && (
        <>
          {' · '}
          <button type="button" className="text-accent hover:underline"
            onClick={() => openMemory({ editId: settled.id })}
            data-testid="memory-settled-edit">
            {settled.id ? 'Edit in Memory ›' : 'Open Memory ›'}
          </button>
        </>
      )}
    </span>
  )
}
