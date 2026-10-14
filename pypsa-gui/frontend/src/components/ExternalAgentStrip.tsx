// External agents' cards (chat harness issue 11, MCP build step 4): a call
// from an agent on this machine (Claude Code, Codex, …) through /api/mcp that
// needs approval waits here, whichever chat is open, until a person answers
// it or its time runs out. Local mode only: there is no MCP endpoint on a
// web deployment, so nothing is polled there.
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getAuthEnabled } from '../auth/config'
import { getExternalPending, postChatConfirm, type ExternalCard } from '../api/chat'

export const EXTERNAL_PENDING_QUERY_KEY = ['chat', 'external-pending'] as const
const POLL_MS = 2000

function summary(args: Record<string, unknown>): string {
  const text = JSON.stringify(args)
  return text.length > 160 ? `${text.slice(0, 157)}…` : text
}

export default function ExternalAgentStrip() {
  const local = !getAuthEnabled()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const { data } = useQuery<ExternalCard[]>({
    queryKey: EXTERNAL_PENDING_QUERY_KEY,
    queryFn: getExternalPending,
    enabled: local,
    refetchInterval: POLL_MS,
  })
  if (!local || !data || data.length === 0) return null
  const answer = async (card: ExternalCard, decision: 'approve' | 'deny') => {
    setBusy(card.confirmation_token)
    try {
      await postChatConfirm(card.session_id, { token: card.confirmation_token, decision })
    } finally {
      setBusy(null)
      void qc.invalidateQueries({ queryKey: EXTERNAL_PENDING_QUERY_KEY })
    }
  }
  const button = 'px-1.5 py-0.5 rounded border border-border text-muted hover:text-text hover:border-accent/40 disabled:opacity-50'
  return (
    <div className="mx-3 mt-2 flex flex-col gap-1" data-testid="chat-external-strip">
      {data.map((card) => (
        <div key={card.confirmation_token}
          className="px-2.5 py-1.5 text-[11px] rounded border border-amber-500/60 bg-amber-500/5 flex items-center gap-2"
          data-testid="chat-external-card">
          <span className="text-muted whitespace-nowrap">External agent</span>
          <span className="text-text font-medium whitespace-nowrap" data-testid="chat-external-client">{card.client}</span>
          <span className="text-muted">wants to run</span>
          <span className="text-text font-mono truncate" data-testid="chat-external-tool"
            title={summary(card.args)}>
            {card.tool_name}
          </span>
          <span className="text-muted whitespace-nowrap">({card.safety_tier})</span>
          <span className="ml-auto flex gap-1">
            <button type="button" className={button} disabled={busy === card.confirmation_token}
              onClick={() => { void answer(card, 'approve') }} data-testid="chat-external-approve">
              Approve
            </button>
            <button type="button" className={button} disabled={busy === card.confirmation_token}
              onClick={() => { void answer(card, 'deny') }} data-testid="chat-external-deny">
              Deny
            </button>
          </span>
        </div>
      ))}
    </div>
  )
}
