import { useEffect, useRef, useState } from 'react'
import { getAssistantActivity, getAssistantDecision, type ActivityFilter, type ActivitySnapshot,
  type ActivityItem, type DecisionObservation } from '../api/assistantActivity'
import { useUIStore } from '../store/uiStore'
import { useChatStore } from '../store/chatStore'
import { withReturnFrom } from '../layout/flowNavigation'

const FILTERS: [ActivityFilter, string][] = [
  ['all', 'All'], ['running', 'Running'], ['needs_you', 'Needs you'],
  ['scheduled', 'Scheduled'], ['completed', 'Completed'],
]

function observationTime(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? date : null
}

/** Study harness 08: a run's attention notice opens the companion on its
 *  plan card (with "Back to …"). With no plan card in this conversation, a
 *  local turn carrying the run is added there first, so "Open the run ›"
 *  stays the only run link in Activity (UX plan verdict 9). */
const runCard = (taskId: string) =>
  [...document.querySelectorAll('[data-run-task]')].find(e => e.getAttribute('data-run-task') === taskId)

function OpenRun({ taskId, title, onConversation }: { taskId: string; title: string; onConversation: () => void }) {
  const open = () => {
    if (!runCard(taskId)) {
      useChatStore.getState().appendMessage({ role: 'tool', content: '', tool_use_id: `run-open-${taskId}`,
        tool_name: 'get_task', tool_result: { task_id: taskId, title, run: {} } })
    }
    withReturnFrom(onConversation)
    // The companion renders the card on its next frames: scroll once it is there.
    let tries = 0
    const scroll = () => {
      const card = runCard(taskId)
      if (card) card.scrollIntoView({ block: 'nearest' })
      else if (++tries < 40) window.setTimeout(scroll, 50)
    }
    window.setTimeout(scroll, 0)
  }
  return <button type="button" className="text-accent" data-testid="activity-open-run" onClick={open}>Open the run ›</button>
}

function DecisionInspector({ item, close, onConversation, projectName }: {
  item: ActivityItem; close: () => void; onConversation: () => void; projectName: string | null
}) {
  const [detail, setDetail] = useState<DecisionObservation | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    getAssistantDecision(item, controller.signal).then(result => {
      if (!controller.signal.aborted) setDetail(result)
    }).catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => controller.abort()
  }, [item.decision_id, item.decision_revision, item.decision_digest, item.project_id])
  return <section aria-label="Decision inspection" className="border border-border rounded p-2 space-y-2">
    <p className="font-medium">{item.family === 'source_gap' ? 'Recorded source gap' : 'Recorded decision'}</p>
    <p className="text-xs">Read-only inspection. This cannot answer a question, revive an approval or start work.</p>
    {failed ? <p role="alert">This record changed or is unavailable. Refresh Activity and inspect it again.</p>
      : !detail ? <p role="status">Loading decision…</p> : <>
        <p className="text-xs">Recorded native status: {detail.native_status}</p>
        {detail.question && <>
          <p className="text-xs">{detail.question.intent === 'plan_review' ? 'Plan review' : 'Question'}
            {detail.question.multi_select ? ' · Multiple choices were allowed' : ' · Single choice'}
            {detail.question.allow_free_text ? ' · Free text was allowed' : ''}</p>
          <p className="whitespace-pre-wrap break-words">{detail.question.question}</p>
          {detail.question.detail && <p className="whitespace-pre-wrap break-words">{detail.question.detail}</p>}
          <ul className="list-disc pl-4">{detail.question.options.map((option, i) => <li key={i} className="break-words">
            {option.label}{option.recommended ? ' (recommended)' : ''}{option.description ? ` — ${option.description}` : ''}
          </li>)}</ul>
        </>}
        {detail.source_gap && <>
          <p className="text-xs break-all">Answer: {detail.source_gap.answer_id} · Turn: {detail.source_gap.turn_id}</p>
          <p className="text-xs break-words">Requested profile: {detail.source_gap.request_scope.profile} · Entity: {detail.source_gap.request_scope.entity_id ?? 'not selected'}</p>
          <p className="text-xs break-all">Requested project: {detail.source_gap.request_scope.project_id}</p>
          {detail.source_gap.request_scope.metrics && <p className="text-xs break-words">Requested metrics: {detail.source_gap.request_scope.metrics.join(', ')}</p>}
          <p className="text-xs break-words">Window: {detail.source_gap.request_scope.start
            ? `${detail.source_gap.request_scope.start} to ${detail.source_gap.request_scope.end}` : 'All current snapshots at that turn'}</p>
          {detail.source_gap.request_scope.cost_basis && <p className="text-xs">Requested cost basis: {detail.source_gap.request_scope.cost_basis.currency}, price year {detail.source_gap.request_scope.cost_basis.price_year}, annual fixed cost fraction {detail.source_gap.request_scope.cost_basis.annual_fixed_cost_fraction}</p>}
          {detail.source_gap.request_scope.evidence_id && <p className="text-xs break-all">Requested retained evidence: {detail.source_gap.request_scope.evidence_id}</p>}
          {detail.source_gap.request_scope.comparison_evidence_id && <p className="text-xs break-all">Requested baseline evidence: {detail.source_gap.request_scope.comparison_evidence_id}</p>}
          {detail.source_gap.request_scope.retain !== undefined && <p className="text-xs">Retention requested: {detail.source_gap.request_scope.retain ? 'Yes' : 'No'}. This observation does not establish that evidence was retained.</p>}
          <ul className="list-disc pl-4">{detail.source_gap.slots.map(slot => <li key={slot.metric} className="break-words">
            {slot.metric}: {slot.disposition.replace(/_/g, ' ')} ({slot.reason})
          </li>)}</ul>
          {detail.followups?.length ? <>
            <p className="text-xs">Linked publications describe their own checked turn. They do not resolve this historical gap or certify current evidence.</p>
            <ul className="list-disc pl-4">{detail.followups.map(link => <li key={link.answer_id} className="text-xs break-all">
              Follow-up answer: {link.answer_id} · Turn: {link.turn_id} · Chat: {link.session_id} ·
              {link.request_complete ? ' All requested metrics were answered at publication.' : ' The request was still limited at publication.'}
            </li>)}</ul>
          </> : null}
          {detail.prepared_followup ? <button type="button" className="text-accent" onClick={() => {
            if (!projectName || useUIStore.getState().currentProject !== projectName || !detail.prepared_followup) return
            useChatStore.getState().setSourceGapDraft({ ...detail.prepared_followup, projectName,
              chatSequence: useChatStore.getState().newChatSeq })
            onConversation()
          }}>Prepare checked follow-up</button> : <p className="text-xs">{detail.followup_unavailable_reason
            ?? 'Preparation is unavailable for this record. Use an ordinary checked request.'}</p>}
          {detail.prepared_followup && <p className="text-xs">Preparation opens a separate request card in your current chat. Review and send it there.</p>}
        </>}
        <p className="text-xs">{detail.reason}</p>
        <p className="text-xs break-all">Project: {detail.project_id} · Chat: {detail.session_id} · {detail.source_gap ? 'Turn' : 'Tool call'}: {detail.tool_use_id}</p>
        <p className="text-xs">This observation does not certify results.</p>
      </>}
    <button type="button" className="text-accent" onClick={close}>Close inspection</button>
  </section>
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
  const [inspecting, setInspecting] = useState<string | null>(null)
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
    setInspecting(null)
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
        {item.family !== 'run' && <p className="text-xs text-muted">Native status: {item.native_status}{item.stage ? ` · ${item.stage}` : ''}</p>}
        {item.family !== 'run' && item.observation === 'retained' && item.last_observed_status &&
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
        {item.decision_id && <button type="button" className="text-accent" onClick={() => setInspecting(item.id)}>{item.family === 'source_gap' ? 'Inspect source gap' : 'Inspect decision'}</button>}
        {item.decision_id && inspecting === item.id && <DecisionInspector
          key={JSON.stringify([key, item.decision_id, item.decision_revision, item.decision_digest])}
          item={item} close={() => setInspecting(null)} onConversation={onConversation} projectName={project} />}
        {item.family === 'run' && item.tool_use_id && <OpenRun taskId={item.tool_use_id} title={item.title} onConversation={onConversation} />}
        {item.family === 'confirmation' && item.status === 'waiting_for_approval' && (item.session_id === session && item.live_approval_available !== false
          ? <button type="button" className="text-accent" onClick={onConversation}>Open originating chat</button>
          : <p className="text-xs">This decision belongs to another conversation. Return there to inspect it; no approval is granted here.</p>)}
      </article>})}
      {data?.next_page && <button type="button" className="text-accent" disabled={loadingMore} onClick={() => void more()}>
        {loadingMore ? 'Loading…' : 'Load more activity'}
      </button>}
      {data && <p className="text-xs text-muted">History covers up to 30 days and the newest 512 job origins per owner/project. Unobserved outcomes remain unknown. Completion does not certify engineering results.</p>}
      {data && <p className="text-xs text-muted">Decision history covers up to 30 days and the newest 256 questions, confirmations and source gaps per owner/project, within a bounded store. Recorded observations do not certify completed work.</p>}
      {data?.warnings.includes('decision_history_unavailable') && <p role="status" className="text-xs">
        Decision history could not be read or saved. Missing history does not establish that no question, confirmation or source gap occurred.
      </p>}
      {data?.warnings.includes('activity_history_unavailable') && <p role="status" className="text-xs">
        Retained job history could not be read or saved. Live observations may still be shown; missing history does not mean work never ran.
      </p>}
      {data?.warnings.includes('activity_completion_observation_incomplete') && <p role="status" className="text-xs">
        Some registered jobs could not be observed through completion. Inspect their native records before retrying; missing history does not establish whether work finished.
      </p>}
      {data?.warnings.some(w => !['activity_history_is_bounded_observation', 'activity_history_unavailable', 'activity_completion_observation_incomplete'].includes(w)) &&
        <p role="status" className="text-xs">Some activity sources are unavailable or bounded. Refresh or inspect their native records for more detail.</p>}
    </div>
  </section>
}
