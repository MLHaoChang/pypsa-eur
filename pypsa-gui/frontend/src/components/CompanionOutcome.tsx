import { useChatStore } from '../store/chatStore'

export function downloadHref(result: unknown): string | null {
  if (!result || typeof result !== 'object' || !('download_url' in result)) return null
  const href = (result as { download_url?: unknown }).download_url
  if (typeof href !== 'string' || !href.startsWith('/api/')) return null
  return href
}

type Props = {
  hidden?: boolean
}

export default function CompanionOutcome({ hidden = false }: Props) {
  const messages = useChatStore((s) => s.messages)
  const latest = [...messages].reverse().find((m) => m.role === 'assistant')
  const download = [...messages].reverse().map((m) => downloadHref(m.tool_result)).find((href) => href)
  if (!latest || hidden) return null
  return (
    <div className="fixed bottom-44 right-6 z-40 pointer-events-auto max-w-xs rounded-xl bg-surface px-3 py-2 text-sm text-text shadow-lg" data-no-panel-close>
      <p data-testid="companion-outcome">{latest.content}</p>
      {download ? (
        <a data-testid="companion-download" href={download}>Download</a>
      ) : null}
    </div>
  )
}
