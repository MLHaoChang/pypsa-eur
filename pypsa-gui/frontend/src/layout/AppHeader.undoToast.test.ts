// M10's rule, which shipped without a test (IEEE 39-bus review, N-f).
//
// The axios interceptor already toasts a structured 409's own sentence — the
// study-in-flight refusal names the study that is running — so the undo
// button must not add "Nothing to undo" after it. Every other failure is the
// empty-stack case and has no server sentence of its own.
import { describe, expect, it } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { invalidateAfterUndo, undoErrorMessage } from './AppHeader'
import { nk } from '../utils/queryKeys'

const err = (status?: number) => ({ response: { status } })

describe('undoErrorMessage', () => {
  it('says nothing on a 409 — the interceptor already spoke', () => {
    expect(undoErrorMessage(err(409))).toBeNull()
  })

  it('names the empty stack on every other failure', () => {
    expect(undoErrorMessage(err(400))).toBe('Nothing to undo')
    expect(undoErrorMessage(err(500))).toBe('Nothing to undo')
    expect(undoErrorMessage(err(undefined))).toBe('Nothing to undo')
    expect(undoErrorMessage(new Error('network down'))).toBe('Nothing to undo')
    expect(undoErrorMessage(undefined)).toBe('Nothing to undo')
  })
})

describe('invalidateAfterUndo', () => {
  it('refreshes the data-centre set-up an undo step restored, next to the network reads', () => {
    const qc = new QueryClient()
    const seen: unknown[] = []
    qc.invalidateQueries = ((f: { queryKey: unknown }) => { seen.push(f.queryKey); return Promise.resolve() }) as never
    invalidateAfterUndo(qc, ['loads', 'buses'], 'Site A')
    expect(seen).toContainEqual(nk('Site A', 'loads'))
    expect(seen).toContainEqual(nk('Site A', 'buses'))
    expect(seen).toContainEqual(nk('Site A', 'adequacy', 'eh_template'))
  })
})
