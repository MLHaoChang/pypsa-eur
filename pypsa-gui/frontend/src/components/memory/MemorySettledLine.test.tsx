// UX M4: a memory card's call settles to one line read from the transcript,
// so the words are the same after a reload or Stop.
import { afterEach, beforeEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import MemorySettledLine, { isMemoryRow, memorySettledAt } from './MemorySettledLine'
import { useMemoryStore } from '../../store/memoryStore'

const ask = { role: 'tool', content: '→ propose_memory', tool_use_id: 'call-m', tool_name: 'propose_memory' }
const idle = { streaming: false, pending: false }

beforeEach(() => useMemoryStore.setState({ open: false, editId: null, dismissed: new Set() }))
afterEach(cleanup)

it('shows one line per call, on its last row', () => {
  const rows = [ask, { ...ask, content: '✓ propose_memory', tool_result: { id: 'm1', status: 'confirmed' } }]
  expect(isMemoryRow(ask)).toBe(true)
  expect(isMemoryRow({ ...ask, tool_name: 'set_value', content: '→ set_value' })).toBe(false)
  expect(memorySettledAt(rows, 0, idle)).toBeNull()
  expect(memorySettledAt(rows, 1, idle)).toEqual({ state: 'remembered', id: 'm1' })
})

it('reads every outcome from the transcript', () => {
  const at = (rows: Parameters<typeof memorySettledAt>[0], live = idle) =>
    memorySettledAt(rows, rows.length - 1, live)?.state
  expect(at([ask, { ...ask, content: 'denied: propose_memory' }])).toBe('not_now')
  const refusal = (reason: string) => ({ ...ask, content: 'x',
    tool_result: { _outcome: { version: 1, outcome: 'denied', reason } } })
  expect(at([ask, refusal('confirmation_denied')])).toBe('not_now')
  expect(at([ask, refusal('confirmation_expired')])).toBe('expired')
  expect(at([ask, refusal('memory_invalid')])).toBe('not_saved')
  // Stop or a reload before an answer: never "waiting" once the turn is over.
  expect(memorySettledAt([ask, { role: 'user', content: 'next' }], 0, idle)?.state).toBe('no_answer')
  expect(at([ask], { streaming: false, pending: true })).toBe('waiting')
})

it('says "not suggested again" only when this tab recorded the dismissal', () => {
  const { rerender } = render(<MemorySettledLine settled={{ state: 'not_now' }} toolUseId="call-m" />)
  expect(screen.getByTestId('memory-settled').textContent).toBe('Not now: not remembered')
  useMemoryStore.getState().noteDismissed('call-m')
  rerender(<MemorySettledLine settled={{ state: 'not_now' }} toolUseId="call-m" />)
  expect(screen.getByTestId('memory-settled').textContent).toBe('Not now: not suggested again for 30 days')
})

it('Remembered links to the saved item in Memory', () => {
  render(<MemorySettledLine settled={{ state: 'remembered', id: 'm1' }} toolUseId="call-m" />)
  expect(screen.getByTestId('memory-settled').textContent).toBe('Remembered · Edit in Memory ›')
  fireEvent.click(screen.getByTestId('memory-settled-edit'))
  expect(useMemoryStore.getState()).toMatchObject({ open: true, editId: 'm1' })
})
