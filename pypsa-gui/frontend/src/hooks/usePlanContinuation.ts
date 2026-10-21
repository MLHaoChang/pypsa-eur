// LC-12: when a chat plan's job ends, the conversation continues by itself,
// once. The server decides what is due (GET /chat/{id}/continuations) and
// accepts each job once; this hook only asks, holds and sends.
//
// Never interrupt the user (UX review): while a turn streams, the composer
// has text, or a card waits for an answer, a due continuation is HELD and the
// task card offers "Continue now" instead.
import { useCallback, useEffect, useRef, useState } from 'react'
import { dismissContinuation, listContinuations, type PlanContinuation } from '../api/chat'

/** While a plan waits on a job in this view; otherwise a slow check covers a reopened chat. */
const WATCH_MS = 3000
const IDLE_MS = 15000

/** The server-written continuation message, as it comes back in the history. */
export const CONTINUATION_MESSAGE = /^job (fg:[0-9a-f]+) finished: (completed|failed|aborted|unknown) \(plan: .*\)$/s

/** The transcript marker: plain words, never a job id or a status enum. */
export function continuationMarker(status: string): string {
  if (status === 'completed') return 'The solve finished · continuing'
  if (status === 'aborted') return 'The solve was cancelled'
  if (status === 'failed') return 'The solve stopped: it failed · continuing'
  return 'The solve ended without a known outcome · continuing'
}

export function usePlanContinuation({ sessionId, watching, hold, onContinue }: {
  sessionId: string | null
  /** A task card in this view is waiting on its job. */
  watching: boolean
  /** Streaming, unsent composer text, or an open card. */
  hold: boolean
  onContinue: (item: PlanContinuation) => void
}) {
  const [due, setDue] = useState<PlanContinuation | null>(null)
  // Jobs this view already continued or stopped waiting for. The server
  // refuses a second claim anyway; this keeps the panel from offering one.
  const [handled, setHandled] = useState<ReadonlyMap<string, 'continued' | 'stopped'>>(new Map())
  const handledRef = useRef(handled)
  handledRef.current = handled

  useEffect(() => {
    setDue(null)
    if (!sessionId) return
    let cancelled = false
    const check = () => {
      listContinuations(sessionId)
        .then((items) => {
          if (!cancelled) setDue(items.find((i) => !handledRef.current.has(i.job_id)) ?? null)
        })
        .catch(() => { /* a failed check retries on the next tick */ })
    }
    check()
    const id = setInterval(check, watching ? WATCH_MS : IDLE_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [sessionId, watching])

  const send = useCallback((item: PlanContinuation) => {
    setHandled((h) => new Map(h).set(item.job_id, 'continued'))
    setDue(null)
    onContinue(item)
  }, [onContinue])

  useEffect(() => {
    if (due && !hold) send(due)
  }, [due, hold, send])

  const continueNow = useCallback(() => { if (due) send(due) }, [due, send])

  const stopWaiting = useCallback((jobId: string) => {
    setHandled((h) => new Map(h).set(jobId, 'stopped'))
    setDue((d) => (d?.job_id === jobId ? null : d))
    if (sessionId) void dismissContinuation(sessionId, jobId).catch(() => {})
  }, [sessionId])

  return { due, held: due != null && hold, handled, continueNow, stopWaiting }
}
