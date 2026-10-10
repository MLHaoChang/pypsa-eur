import { useEffect, useState } from 'react'
import { Mic, Pencil } from 'lucide-react'

// Clears toasts above the chip (max-w 12rem) plus bottom-6 / right-6 (24px).
export const COMPANION_TOAST_OFFSET = 220

export type CompanionPresence = 'idle' | 'listening' | 'thinking' | 'speaking'

type Props = {
  presence?: CompanionPresence
  profileLabel: string
  onCompose: () => void
  onSpeak: () => void
  onLive: () => void
  onProfiles: () => void
}

function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(() =>
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduce(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduce
}

export default function CompanionLauncher({
  presence = 'idle', profileLabel, onCompose, onSpeak, onLive, onProfiles,
}: Props) {
  const reduce = useReducedMotion()
  return (
    <div
      className="fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2"
      data-testid="companion-launcher"
      data-no-panel-close
      data-presence={presence}
      data-motion={reduce ? 'off' : 'on'}
    >
      <button
        type="button"
        onClick={onProfiles}
        data-testid="companion-profile"
        className="max-w-[12rem] truncate rounded-full border border-border bg-bg-2 px-3 py-1 text-[10px] text-text shadow-lg"
        title={profileLabel}
        aria-label={`Assistant model: ${profileLabel}. Open the model list.`}
      >
        {profileLabel}
      </button>
      <div
        aria-hidden="true"
        data-testid="companion-character"
        className={reduce ? '' : 'companion-idle'}
      >
        <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
          <rect x="16" y="18" width="40" height="36" rx="14" fill="var(--color-accent)" />
          <rect x="24" y="28" width="24" height="12" rx="6" fill="var(--color-bg)" />
          <circle cx="32" cy="34" r="2" fill="var(--color-accent)" />
          <circle cx="40" cy="34" r="2" fill="var(--color-accent)" />
        </svg>
      </div>
      <div className="flex items-center rounded-full border border-border bg-bg-2 shadow-lg">
        <button
          type="button"
          onClick={onCompose}
          data-testid="companion-compose"
          aria-label="Compose a message"
          title="Open the assistant and type"
          className="px-3 py-2 text-text hover:text-accent"
        >
          <Pencil size={16} />
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onSpeak}
          data-testid="companion-speak"
          aria-label="Open reviewed dictation"
          title="Open the assistant and review dictation"
          className="px-3 py-2 text-text hover:text-accent"
        >
          <Mic size={16} />
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onLive}
          data-testid="companion-live"
          aria-label="Start a live conversation on this page"
          aria-pressed={presence === 'listening'}
          title="Talk with the assistant here. This stays on the current page."
          className="px-3 py-2 text-text hover:text-accent"
        >
          Live
        </button>
      </div>
    </div>
  )
}
