import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  dreamApi, dreamStatusWords, errorWords, isDreamLive,
  type DreamJob, type DreamSettings,
} from '../../api/memory'

// "Review my recent work" (assistant-memory plan §6, issue 04b): the head of
// the Proposals tab. A review reads the user's own past chats and runs and
// adds proposals to this tab; each is still confirmed one by one (COPILOT-1).
// The automatic triggers sit under it, both off until the user turns them on.

const POLL_MS = 3000
// While the tab is open, a nightly or after-chats review can start on its own.
const IDLE_POLL_MS = 30_000

export default function DreamReview() {
  const qc = useQueryClient()
  const [refusal, setRefusal] = useState<string | null>(null)

  const jobQ = useQuery({
    queryKey: ['memory', 'dream'],
    queryFn: dreamApi.latest,
    refetchInterval: q => (isDreamLive(q.state.data) ? POLL_MS : IDLE_POLL_MS),
  })
  const job = jobQ.data
  const live = isDreamLive(job)

  // A review that just finished has written proposals: refresh the list.
  const wasLive = useRef(false)
  useEffect(() => {
    if (wasLive.current && !live) {
      void qc.invalidateQueries({ queryKey: ['memory', 'proposals'] })
    }
    wasLive.current = live
  }, [live, qc])

  const start = useMutation({
    mutationFn: dreamApi.start,
    onMutate: () => setRefusal(null),
    onSuccess: (j: DreamJob) => qc.setQueryData(['memory', 'dream'], j),
    onError: err => {
      setRefusal(errorWords(err))
      void qc.invalidateQueries({ queryKey: ['memory', 'dream'] })
    },
  })

  return (
    <div className="px-4 py-2 border-b border-border shrink-0 space-y-1.5" data-testid="dream-review">
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => start.mutate()}
          disabled={live || start.isPending || jobQ.isLoading}
          className="px-2 py-0.5 rounded bg-accent text-white text-[11px] font-semibold disabled:opacity-50">
          Review my recent work
        </button>
        <span role="status" aria-live="polite" className="text-[10.5px] text-muted" data-testid="dream-status">
          {jobQ.error ? `Review status could not be loaded: ${errorWords(jobQ.error)}` : dreamStatusWords(job)}
        </span>
      </div>
      {refusal && (
        <p role="alert" className="text-[10.5px] text-danger">{refusal}</p>
      )}
      <AutomaticReviews />
    </div>
  )
}

function AutomaticReviews() {
  const qc = useQueryClient()
  const settingsQ = useQuery({ queryKey: ['memory', 'dream-settings'], queryFn: dreamApi.settings })
  const save = useMutation({
    mutationFn: dreamApi.setSettings,
    onSuccess: (s: DreamSettings) => qc.setQueryData(['memory', 'dream-settings'], s),
  })
  const s = settingsQ.data
  const busy = !s || save.isPending
  const at = s?.nightly_at.split(' ')[0] ?? '03:00'

  return (
    <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10.5px] text-muted">
      <legend className="sr-only">Automatic reviews</legend>
      <span className="font-semibold text-text">Automatic reviews <span className="font-normal text-muted">(your own setting)</span></span>
      <label className="flex items-center gap-1">
        <input type="checkbox" checked={!!s?.nightly} disabled={busy}
          onChange={e => save.mutate({ nightly: e.target.checked })} />
        Every night at {at} (Berlin time)
      </label>
      <label className="flex items-center gap-1">
        <input type="checkbox" checked={!!s?.after_sessions} disabled={busy}
          onChange={e => save.mutate({ after_sessions: e.target.checked })} />
        After {s?.sessions_needed ?? 10} new chats
      </label>
      <span>
        Off unless you turn them on: a review sends your past chats to
        {s?.analyzer ? ` ${s.analyzer}` : ' the configured model'}.
        {' '}The nightly review runs only while the app is running at {at}; a night it is off is skipped.
      </span>
      {(settingsQ.error || save.error) && (
        <span role="alert" className="text-danger">
          Settings could not be {save.error ? 'saved' : 'loaded'}: {errorWords(save.error ?? settingsQ.error)}
        </span>
      )}
    </fieldset>
  )
}
