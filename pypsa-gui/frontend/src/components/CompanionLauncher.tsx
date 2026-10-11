import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
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
  liveEcho?: string
  modelKeyMissing?: boolean
  onLiveSubmit?: (text: string) => void
  children?: ReactNode
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

function GridNode({ lit }: { lit: boolean }) {
  return (
    <svg width="96" height="96" viewBox="0 0 96 96" fill="none" aria-hidden="true">
      <circle
        className="companion-node-halo"
        cx="48"
        cy="48"
        r="34"
        stroke="var(--color-accent)"
        strokeOpacity={lit ? 0.35 : 0.18}
        strokeWidth="1.5"
      />
      <circle
        className="companion-node-ring"
        cx="48"
        cy="48"
        r="26"
        stroke="var(--color-accent)"
        strokeOpacity={lit ? 0.75 : 0.4}
        strokeWidth="2"
      />
      <circle
        cx="48"
        cy="48"
        r="14"
        fill="var(--color-accent)"
        fillOpacity={lit ? 0.95 : 0.82}
      />
      <g className="companion-node-sector" opacity={lit ? 0.9 : 0.55}>
        <path
          d="M48 34 A14 14 0 0 1 62 48"
          stroke="var(--color-bg)"
          strokeWidth="2.5"
          strokeLinecap="round"
          fill="none"
        />
      </g>
      <circle cx="48" cy="48" r="3.5" fill="var(--color-bg)" fillOpacity="0.9" />
    </svg>
  )
}

export default function CompanionLauncher({
  presence = 'idle', profileLabel, onCompose, onSpeak, onLive, onProfiles,
  liveOpen = false, liveCaption = '', liveEcho = '', modelKeyMissing = false, onLiveSubmit,
  children,
}: Props) {
  const reduce = useReducedMotion()
  const [hovered, setHovered] = useState(false)
  const [draft, setDraft] = useState('')
  const lit = presence !== 'idle'
  const echo = liveEcho.trim()
  const caption = liveCaption.trim()
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
      {children}
      {liveOpen && (
        <form
          className="w-64 rounded-2xl border border-border/80 bg-bg-2/95 px-3 py-2.5 backdrop-blur-sm"
          data-testid="companion-live-composer"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            const text = draft.trim()
            if (!text) return
            onLiveSubmit?.(text)
            setDraft('')
          }}
        >
          {echo ? (
            <p
              className="mb-2 text-[12px] leading-snug text-muted companion-live-echo"
              data-testid="companion-live-echo"
            >
              {echo}
            </p>
          ) : null}
          {caption ? (
            <p className="mb-2 text-[11px] leading-snug text-muted" data-testid="companion-live-caption">
              {caption}
            </p>
          ) : null}
          {modelKeyMissing && (
            <p className="mb-2 text-[10px] text-muted" data-testid="companion-live-key">
              Model key is not configured. Tab changes still use the assistant&apos;s panel tool.
            </p>
          )}
          <div className="flex items-center gap-2">
            <input
              data-testid="companion-live-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Say something on this page"
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
        className="max-w-[12rem] truncate rounded-full border border-border bg-bg-2 px-3 py-1 text-[10px] text-text"
        title={profileLabel}
        aria-label={`Assistant model: ${profileLabel}. Open the model list.`}
      >
        {profileLabel}
      </button>
      <div className="companion-character-wrap" data-lit={lit ? 'true' : 'false'}>
        <div
          aria-hidden="true"
          data-testid="companion-character"
          data-shape="grid-node"
          className={reduce ? '' : 'companion-idle'}
        >
          <GridNode lit={lit} />
        </div>
      </div>
      <div
        className="companion-actions flex items-center rounded-full border border-border bg-bg-2"
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
