import { useEffect, useRef, useState } from 'react'
import { getAssistantActivity, type ActivityFilter, type ActivitySnapshot } from '../api/assistantActivity'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'

const FILTERS: [ActivityFilter, string][] = [
  ['all', 'All'], ['running', 'Running'], ['needs_you', 'Needs you'],
  ['scheduled', 'Scheduled'], ['completed', 'Completed'],
]

function observationTime(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? date : null
}

/** A read-only observer: closing it changes no job, task or decision. */
export default function AssistantActivity({ onConversation }: { onConversation: () => void }) {
  const project = useUIStore(s => s.currentProject)
  const session = useChatStore(s => s.sessionId)
  const [filter, setFilter] = useState<ActivityFilter>('all')
  const [view, setView] = useState<{ key: string; data: ActivitySnapshot } | null>(null)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const key = JSON.stringify([project, filter, refresh])
  const currentKey = useRef(key)
  currentKey.current = key
  const latest = useRef<ActivitySnapshot | null>(null)
  const moreController = useRef<AbortController | null>(null)
  const data = view?.key === key ? view.data : null

  useEffect(() => {
    latest.current = null
    setError(false)
    setLoadingMore(false)
    if (!project) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()
    let fetching = false
    const poll = async () => {
      if (stopped || fetching) return
      fetching = true
      clearTimeout(timer)
      try {
        const result = await getAssistantActivity(project, filter, {
          cursor: latest.current?.cursor, signal: controller.signal,
        })
        if (stopped || currentKey.current !== key) return
        const previous = latest.current
        const next = previous?.cursor === result.cursor && !result.resnapshot_required
          ? { ...result, items: previous.items, next_page: previous.next_page } : result
        latest.current = next
        setView({ key, data: next })
        setError(false)
      } catch {
        if (!stopped && currentKey.current === key) setError(true)
      } finally {
        fetching = false
        if (!stopped) timer = setTimeout(poll, 3000)
      }
    }
    const reconnect = () => { if (document.visibilityState !== 'hidden') void poll() }
    void poll()
    window.addEventListener('online', reconnect)
    document.addEventListener('visibilitychange', reconnect)
    return () => {
      stopped = true
      controller.abort()
      moreController.current?.abort()
      clearTimeout(timer)
      window.removeEventListener('online', reconnect)
      document.removeEventListener('visibilitychange', reconnect)
    }
  }, [project, filter, key])

  async function more() {
    const previous = latest.current
    if (!project || !previous?.next_page || loadingMore) return
    setLoadingMore(true)
    const controller = new AbortController()
    moreController.current = controller
    try {
      const result = await getAssistantActivity(project, filter, { page_token: previous.next_page, signal: controller.signal })
      if (controller.signal.aborted || currentKey.current !== key || latest.current?.cursor !== previous.cursor) return
      if (result.cursor !== previous.cursor || result.resnapshot_required) {
        setRefresh(n => n + 1)
        return
      }
      const items = [...new Map([...latest.current.items, ...result.items].map(r => [r.id, r])).values()]
      const next = { ...result, items }
      latest.current = next
      setView({ key, data: next })
      setError(false)
    } catch {
      if (!controller.signal.aborted && currentKey.current === key) setRefresh(n => n + 1)
    } finally {
      if (!controller.signal.aborted && currentKey.current === key) setLoadingMore(false)
    }
  }

  return <section aria-label="Assistant activity" className="flex flex-col flex-1 min-h-0 text-sm">
    <div className="p-3 border-b border-border space-y-2">
      <p className="text-muted text-xs">Activity in {project ?? 'no saved project'}. Observing work does not start or cancel it.</p>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Activity filters">
        {FILTERS.map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value}
          className={`px-2 py-1 rounded border border-border ${filter === value ? 'bg-panel text-accent' : 'text-muted'}`}
          onClick={() => setFilter(value)}>{label}</button>)}
      </div>
      <button type="button" className="text-accent text-xs" onClick={() => setRefresh(n => n + 1)}>Refresh activity</button>
    </div>
    <div className="overflow-y-auto min-h-0 p-3 space-y-3">
      {!project && <p>Activate a saved project to view its activity.</p>}
      {error && <p role="alert">Activity could not be refreshed. Previously displayed states may be stale; no job was restarted.</p>}
      {project && !data && !error && <p role="status">Loading activity…</p>}
      {data?.resnapshot_required && <p role="status">Activity recovered from a fresh snapshot.</p>}
      {filter === 'scheduled' && data && <p>Scheduled work is not available yet.</p>}
      {data && data.items.length === 0 && filter !== 'scheduled' && <p>No matching work in this snapshot.</p>}
      {data?.items.map(item => {
        const recorded = observationTime(item.observed_at)
        return <article key={item.id} className="border border-border rounded p-3 space-y-1">
        <p className="font-medium break-words">{item.title}</p>
        <p>{item.status.replace(/_/g, ' ')} <span className="text-muted">· {item.family.replace(/_/g, ' ')}</span></p>
        <p className="text-xs text-muted">Native status: {item.native_status}{item.stage ? ` · ${item.stage}` : ''}</p>
        {item.observation === 'retained' && item.last_observed_status &&
          <p className="text-xs text-muted">Last recorded native status: {item.last_observed_status}</p>}
        {item.observation && <p className="text-xs text-muted">
          {item.observation === 'retained' ? 'Retained observation' : 'Current native observation'}
          {recorded && <> · Last recorded <time dateTime={recorded.toISOString()}>
            {recorded.toLocaleString()}
          </time></>}
        </p>}
        <p className="text-xs text-muted break-all">Source: {item.source_id}</p>
        {item.reason && <p className="text-xs">{item.reason}</p>}
        {item.status === 'unavailable' && <p className="text-xs">The originating job state cannot be established. Inspect it before retrying.</p>}
        {item.family === 'confirmation' && (item.session_id === session
          ? <button type="button" className="text-accent" onClick={onConversation}>Open originating chat</button>
          : <p className="text-xs">This decision belongs to another conversation. Return there to inspect it; no approval is granted here.</p>)}
      </article>})}
      {data?.next_page && <button type="button" className="text-accent" disabled={loadingMore} onClick={() => void more()}>
        {loadingMore ? 'Loading…' : 'Load more activity'}
      </button>}
      {data && <p className="text-xs text-muted">History covers up to 30 days and the newest 512 job origins per owner/project. Unobserved outcomes remain unknown. Completion does not certify engineering results.</p>}
      {data?.warnings.includes('activity_history_unavailable') && <p role="status" className="text-xs">
        Retained job history could not be read or saved. Live observations may still be shown; missing history does not mean work never ran.
      </p>}
      {data?.warnings.some(w => !['activity_history_is_bounded_observation', 'activity_history_unavailable'].includes(w)) &&
        <p role="status" className="text-xs">Some activity sources are unavailable or bounded. Refresh or inspect their native records for more detail.</p>}
    </div>
  </section>
}
