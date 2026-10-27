import { useId, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getChatAccess } from '../api/chatAccess'
import { useChatStore } from '../store/chatStore'
import { useUIStore } from '../store/uiStore'
import ChatRestrictions from './ChatRestrictions'

const disposition = { none: 'No approval card', policy: 'Server policy (audited)', user: 'You confirm', blocked: 'Blocked by your rule' }
const tierNames: Record<string, string> = {
  read: 'Read', write: 'Write', destructive: 'Destructive', execution: 'Execute', execution_long_running: 'Long-running work',
}
const toolLabel = (tool: string) => tool.charAt(0).toUpperCase() + tool.slice(1).replace(/_/g, ' ')
const blockers: Record<string, string> = {
  chat_policy_unavailable: 'Chat policy is unavailable. Server configuration must be repaired before starting a turn.',
  project_writes_locked: 'Project writes are locked by another user. Reads remain available.',
  session_output_budget_exhausted: 'This chat has reached its output-token limit.',
  session_unreported_usage_turn_limit: 'This chat has reached its turn limit without reported usage.',
  requested_checks_unavailable: 'Requested answer checks are unavailable on this server.',
}

export default function ChatAccess({ checked, companion = false }: { checked: boolean; companion?: boolean }) {
  const [open, setOpen] = useState(false)
  const regionId = useId()
  const project = useUIStore(s => s.currentProject)
  const mode = useUIStore(s => s.uiMode)
  const dockOpen = useUIStore(s => s.assistantDockOpen)
  const session = useChatStore(s => s.sessionId)
  const chat = useChatStore(s => s.newChatSeq)
  const active = companion ? !dockOpen : open && dockOpen
  const queryClient = useQueryClient()
  const queryKey = ['chat-access', project, session, chat, mode, checked]
  const query = useQuery({ queryKey,
    queryFn: ({ signal }) => getChatAccess(project, session, mode, checked, signal), enabled: active,
    retry: false, staleTime: 0, gcTime: 0, refetchInterval: active ? 15_000 : false,
    refetchOnWindowFocus: 'always', refetchOnReconnect: 'always' })
  const data = query.data
  const matching = data?.version === 1 && data.read_only === true
    && data.scope.project_name === project && data.selection.ui_mode === mode
    && data.selection.checked_requested === checked && (data.session?.id ?? null) === session
  const current = !query.isError && !query.isFetching && matching ? data : undefined
  const ruleNames = { standard: 'Standard', confirm_changes: 'Confirm every change', read_only: 'Read-only tools' }
  const observation = query.isFetching ? <p role="status">Refreshing access…</p>
    : !current ? <p role="alert">Current access is unavailable. Refresh before relying on these rules.</p> : null
  const refresh = <button type="button" className="underline" disabled={query.isFetching}
    onClick={() => { void query.refetch() }}>Refresh access</button>
  return <div className={companion
    ? 'w-72 max-w-full rounded-lg border border-border bg-bg-2 px-3 py-2 text-[11px] break-words'
    : 'border-t border-border px-3 py-1.5 text-[11px] shrink-0'}>
    {companion && <section aria-label="In-page chat access" className="space-y-1">
      {observation}
      {current && <>
        <p>Next turn: {current.scope.project_name ?? 'Unsaved draft'} · {mode === 'guided' ? 'Guided' : 'Expert'}</p>
        <p>Your rule: {current.restrictions ? ruleNames[current.restrictions.mode] ?? 'Unavailable'
          : current.scope.project_id ? 'Unavailable' : 'No saved project rule'}</p>
        <p>{current.checking.effective_for_selection
          ? 'Answer checks on for supported profiles'
          : checked ? 'Requested answer checks unavailable' : 'Answer checks off for this selection'}</p>
        {current.blockers.map(reason => <p key={reason} className="text-warning">
          {blockers[reason] ?? 'An action prerequisite is unavailable.'}</p>)}
        <p className="text-muted">Observed at {new Date(current.observed_at * 1000).toLocaleTimeString()}. Actions are rechecked.</p>
        {current.session?.turn_in_flight && <p>A turn is running; this is the next turn’s selection.</p>}
      </>}
      {refresh}
    </section>}
    <button type="button" className="underline text-muted" aria-expanded={open} aria-controls={regionId}
      onClick={() => setOpen(value => !value)}>Access and approvals</button>
    {open && <section id={regionId} aria-label="Current access and approvals"
      className="mt-2 max-h-64 overflow-auto space-y-2">
      <p>For the next turn’s {mode === 'guided' ? 'Guided' : 'Expert'} selection. Access and prerequisites are rechecked for every action.</p>
      {!companion && refresh}
      {current && <p>Project: <strong>{current.scope.project_name ?? 'Unsaved draft'}</strong></p>}
      <ChatRestrictions key={JSON.stringify(queryKey)} current={current}
        onSaved={() => { void queryClient.invalidateQueries({ queryKey, exact: true }) }} />
      {current ? <>
          <p className="text-muted">Observed at {new Date(current.observed_at * 1000).toLocaleTimeString()}.</p>
          {current.blockers.map(reason => <p key={reason} role="status" className="text-warning">
            {blockers[reason] ?? 'An action prerequisite is unavailable.'}</p>)}
          {current.project_lock === 'yours' && <p>You hold the project write lock.</p>}
          <table className="w-full text-left">
            <caption className="text-left">Confirmation rules</caption>
            <thead><tr><th scope="col">Action</th><th scope="col">Built-in tools</th><th scope="col">External tools</th></tr></thead>
            <tbody>{current.confirmation_rules.map(row => <tr key={row.tier}>
              <th scope="row" className="font-normal pr-1">{tierNames[row.tier] ?? row.tier}</th>
              <td className="pr-1">{disposition[row.native]}</td><td>{disposition[row.external]}</td>
            </tr>)}</tbody>
          </table>
          {current.guided_uncarded.length > 0 && <div>
            <p>In Guided, these Write tools have their own rule: each only stores a pending change, and the step that applies it shows the Write row's card.</p>
            <ul>{current.guided_uncarded.map(row =>
              <li key={row.tool}>{toolLabel(row.tool)} · {disposition[row.native]}</li>)}</ul>
          </div>}
          <p>No approval card does not grant access. Tool availability, locks, checked scope and action-specific validation still apply.
            External non-read calls always need confirmation. Saved context cannot grant permission.</p>
          <p>{current.checking.effective_for_selection
            ? 'Answer checks apply to this selection, within supported profiles only. Unsupported actions are refused.'
            : 'Answer checks do not apply to this selection.'}</p>
          <dl className="grid grid-cols-2 gap-x-2 gap-y-1">
            <dt>Output tokens / turn</dt><dd>{current.limits.output_tokens_per_turn.toLocaleString()}</dd>
            <dt>Tool calls / turn</dt><dd>{current.limits.tool_calls_per_turn}</dd>
            <dt>Output tokens / chat</dt><dd>{current.limits.output_tokens_per_session.toLocaleString()}</dd>
            <dt>Turns without reported usage</dt><dd>{current.limits.turns_without_reported_usage}</dd>
            <dt>Daily project tokens</dt><dd>{current.limits.daily_project_tokens?.toLocaleString() ?? 'No configured daily cap'}</dd>
            <dt>Confirmation lifetime</dt><dd>{current.limits.confirmation_ttl_seconds} seconds</dd>
            {current.session && <><dt>Reported chat output tokens</dt>
              <dd>{current.session.output_tokens_used?.toLocaleString() ?? 'Unavailable'}</dd></>}
          </dl>
          <p>Configured limits are not remaining balances or permission to incur charges. Work-item and job limits are checked separately.
            Pending decisions use the exact cards in chat or Needs you.</p>
          {current.session?.turn_in_flight && <p>A turn is running; these selections do not change that turn’s admitted policy.</p>}
        </> : !companion && observation}
    </section>}
  </div>
}
