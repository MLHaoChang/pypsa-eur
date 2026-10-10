import { useEffect, useState, type FormEvent } from 'react'
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
  liveOpen?: boolean
  liveCaption?: string
  modelKeyMissing?: boolean
  onLiveSubmit?: (text: string) => void
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
  liveOpen = false, liveCaption = '', modelKeyMissing = false, onLiveSubmit,
}: Props) {
  const reduce = useReducedMotion()
  const [hovered, setHovered] = useState(false)
  const [draft, setDraft] = useState('')
  const lit = presence !== 'idle'
  return (
    <div
      className="companion-launcher fixed bottom-6 right-6 z-40 flex flex-col items-end gap-2"
      data-testid="companion-launcher"
      data-no-panel-close
      data-presence={presence}
      data-motion={reduce ? 'off' : 'on'}
      data-hovered={hovered ? 'true' : 'false'}
      data-live={liveOpen ? 'open' : 'closed'}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHovered(false)
      }}
    >
      {liveOpen && (
        <form
          className="w-72 rounded-2xl border border-border bg-bg-2 p-3 shadow-lg"
          data-testid="companion-live-composer"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            const text = draft.trim()
            if (!text) return
            onLiveSubmit?.(text)
            setDraft('')
          }}
        >
          <p className="text-[11px] leading-snug text-text" data-testid="companion-live-caption">
            {liveCaption}
          </p>
          {modelKeyMissing && (
            <p className="mt-1 text-[10px] text-muted" data-testid="companion-live-key">
              Model key is not configured. Tab changes still use the assistant&apos;s panel tool.
            </p>
          )}
          <div className="mt-2 flex items-center gap-2">
            <input
              data-testid="companion-live-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Walk me through the key results in each tab"
              aria-label="Say something to the assistant"
              className="min-w-0 flex-1 rounded-full border border-border bg-bg px-3 py-1.5 text-[12px] text-text"
            />
            <button
              type="submit"
              data-testid="companion-live-send"
              className="rounded-full bg-accent px-3 py-1.5 text-[11px] font-medium text-bg"
            >
              Send
            </button>
          </div>
        </form>
      )}
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
      <div className="companion-character-wrap" data-lit={lit ? 'true' : 'false'}>
        <div
          aria-hidden="true"
          data-testid="companion-character"
          className={reduce ? '' : 'companion-idle'}
        >
          <svg width="112" height="112" viewBox="0 0 72 72" fill="none">
            <rect x="16" y="18" width="40" height="36" rx="14" fill="var(--color-accent)" />
            <rect x="24" y="28" width="24" height="12" rx="6" fill="var(--color-bg)" />
            <circle cx="32" cy="34" r="2" fill="var(--color-accent)" />
            <circle cx="40" cy="34" r="2" fill="var(--color-accent)" />
          </svg>
        </div>
      </div>
      <div
        className="companion-actions flex items-center rounded-full border border-border bg-bg-2 shadow-lg"
        data-testid="companion-actions"
      >
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
          aria-pressed={lit}
          title="Talk with the assistant here. This stays on the current page."
          className={`px-3 py-2 hover:text-accent ${lit ? 'text-accent' : 'text-text'}`}
        >
          Live
        </button>
      </div>
    </div>
  )
}
