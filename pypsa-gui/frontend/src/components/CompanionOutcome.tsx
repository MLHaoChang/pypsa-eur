import { useChatStore } from '../store/chatStore'

const UPLOAD_BLOB = /^\/api\/projects\/[^/?#]+\/uploads\/[0-9a-f]{16}\/blob$/

export function downloadHref(result: unknown): { href: string; filename: string | null } | null {
  if (!result || typeof result !== 'object' || !('download_url' in result)) return null
  const record = result as { download_url?: unknown; filename?: unknown }
  const href = record.download_url
  if (typeof href !== 'string' || !UPLOAD_BLOB.test(href)) return null
  if (typeof record.filename === 'string') return { href, filename: record.filename }
  return { href, filename: null }
}

type Props = {
  hidden?: boolean
}

function afterLastUser<T extends { role: string }>(messages: readonly T[]): readonly T[] {
  let lastUser = -1
  for (let i = 0; i < messages.length; i += 1) {
    if (messages[i].role === 'user') lastUser = i
  }
  return messages.slice(lastUser + 1)
}

/**
 * Live is presence-led: the assistant's sentence belongs in the console
 * transcript. This surface keeps only what a closed console cannot offer, the
 * current turn's download. Confirmations ride ConfirmationCard beside it.
 */
export default function CompanionOutcome({ hidden = false }: Props) {
  const messages = useChatStore((s) => s.messages)
  const download = [...afterLastUser(messages)].reverse()
    .map((m) => downloadHref(m.tool_result))
    .find((hit) => hit) ?? null
  if (!download || hidden) return null
  return (
    <div className="z-40 pointer-events-auto max-w-xs" data-no-panel-close>
      <a
        data-testid="companion-download"
        href={download.href}
        {...(typeof download.filename === 'string' ? { download: download.filename } : {})}
        className="inline-flex rounded-full border border-border bg-bg-2 px-3 py-1.5 text-[11px] font-medium text-text shadow-lg hover:text-accent"
      >
        Download
      </a>
    </div>
  )
}
