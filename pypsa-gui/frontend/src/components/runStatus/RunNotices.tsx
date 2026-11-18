import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { solveQueueApi } from '../../api/solveQueue'
import { QUEUE_KEY } from '../../hooks/useSolveQueue'
import { useRunNotifications } from '../../hooks/useRunNotifications'
import { useUIStore } from '../../store/uiStore'
import { runAgainError } from '../../utils/runNotify'
import { openNoticeResults, openNoticeWhy } from './noticeActions'
import { useRunNotices, type RunNotice } from './runNotices'
import { useAuthUserId } from '../../auth/AuthProvider'

/** At most this many notices show; the rest fold (UX 2 GO 4). */
export const MAX_SHOWN = 3

const TOAST_ID = 'run-notices'

/**
 * Mounts the run notifications (run progress slice 3) and puts the notice
 * stack in the app's one toaster, as a single toast, so it stacks with the
 * other toasts and moves with the assistant dock like they do.
 */
export default function RunNotices() {
  useRunNotifications(useAuthUserId() ?? 'local')
  const hasNotices = useRunNotices(s => s.notices.length > 0)
  useEffect(() => {
    if (hasNotices) toast.custom(<RunNoticeStack />, { id: TOAST_ID, duration: Infinity })
    else toast.remove(TOAST_ID)
  }, [hasNotices])
  useEffect(() => () => toast.remove(TOAST_ID), [])
  return null
}

/**
 * The notices, newest first. Each is its own `role="status"` line; nothing
 * here takes focus (UX 2 GO 3). Beyond three, "(N more) Show all" folds the
 * rest. No animation of its own; the toaster's honours reduced motion.
 */
export function RunNoticeStack() {
  const notices = useRunNotices(s => s.notices)
  const [all, setAll] = useState(false)
  if (notices.length === 0) return null
  const shown = all ? notices : notices.slice(0, MAX_SHOWN)
  const more = notices.length - MAX_SHOWN
  return (
    <div data-testid="run-notices" className="flex w-[22rem] max-w-[90vw] flex-col gap-1.5">
      {shown.map(n => <NoticeItem key={n.id} notice={n} />)}
      {more > 0 && (
        <button type="button" data-testid="run-notices-more" aria-expanded={all}
          onClick={() => setAll(a => !a)}
          className="self-end text-[11px] text-muted underline underline-offset-2">
          {all ? '▾ Show fewer' : `▸ (${more} more) Show all`}
        </button>
      )}
    </div>
  )
}

const link = 'shrink-0 underline underline-offset-2 font-semibold disabled:no-underline disabled:opacity-70'

function NoticeItem({ notice: n }: { notice: RunNotice }) {
  const expert = useUIStore(s => s.uiMode === 'expert')
  const dismiss = useRunNotices(s => s.dismiss)
  const [open, setOpen] = useState(false)
  const tone = n.kind === 'completed' || n.kind === 'away' ? 'border-border' : 'border-warn/50'
  return (
    <div role="status" data-testid={`run-notice-${n.kind}`}
      className={`rounded-lg border ${tone} bg-panel px-3 py-2 text-[12px] text-text shadow-lg`}>
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1">{n.text}</span>
        <button type="button" aria-label={`Close: ${n.text}`} data-testid="run-notice-close"
          onClick={() => dismiss(n.id)} className="shrink-0 text-muted hover:text-text">
          <X size={13} aria-hidden />
        </button>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px]">
        {n.kind === 'completed' && (
          <button type="button" data-testid="run-notice-open" className={link} onClick={() => openNoticeResults(n)}>
            Open results
          </button>
        )}
        {n.kind === 'failed' && (
          <button type="button" data-testid="run-notice-why" className={link}
            aria-expanded={expert ? undefined : open}
            onClick={() => (expert ? openNoticeWhy() : setOpen(o => !o))}>
            See why
          </button>
        )}
        {n.kind === 'interrupted' && <RunAgain notice={n} />}
        {n.kind === 'away' && (
          <button type="button" data-testid="run-notice-show" className={link} aria-expanded={open}
            onClick={() => setOpen(o => !o)}>
            {open ? '▾ Hide' : '▸ Show'}
          </button>
        )}
      </div>
      {open && n.kind === 'failed' && n.reason && (
        <p data-testid="run-notice-reason" className="mt-1 text-[11px] text-muted">{n.reason}</p>
      )}
      {open && n.kind === 'away' && (
        <ul data-testid="run-notice-lines" className="mt-1 flex flex-col gap-0.5 text-[11px] text-muted">
          {n.lines.map((l, i) => <li key={i}>{l}</li>)}
        </ul>
      )}
    </div>
  )
}

/**
 * "Run again: Campus study". One request however often it is clicked, with
 * "Starting…" as visible text while it goes (UX 7, UX 2 GO 5); a refusal says
 * why in plain words, in place (GK B3 N3). Once the run is queued again the
 * notice's condition is over and it goes.
 */
function RunAgain({ notice: n }: { notice: RunNotice }) {
  const qc = useQueryClient()
  const dismiss = useRunNotices(s => s.dismiss)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)
  const run = async () => {
    if (inFlight.current || !n.jobId) return
    inFlight.current = true
    setBusy(true)
    setError(null)
    try {
      await solveQueueApi.requeue(n.jobId)
      void qc.invalidateQueries({ queryKey: QUEUE_KEY })
      dismiss(n.id)
    } catch (err) {
      setError(runAgainError(err, n.project ?? 'the project'))
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" data-testid="run-notice-again" className={link} disabled={busy} onClick={run}>
        {busy ? 'Starting…' : `Run again: ${n.runName}`}
      </button>
      {error && <span data-testid="run-notice-error" className="basis-full text-warn">{error}</span>}
    </>
  )
}
