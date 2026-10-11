import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Mic, Pencil, Radio } from 'lucide-react'

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

/** Closed companion: a small studio character. Live morphs this into LiveNode. */
function RobotCharacter() {
  return (
    <svg width="112" height="112" viewBox="0 0 112 112" fill="none" aria-hidden="true">
      <ellipse cx="56" cy="96" rx="22" ry="5" fill="var(--color-accent)" fillOpacity="0.12" />
      <rect x="38" y="78" width="36" height="14" rx="7" fill="var(--color-accent)" fillOpacity="0.35" />
      <path
        d="M34 82c4-10 12-16 22-16s18 6 22 16"
        stroke="var(--color-accent)"
        strokeOpacity="0.45"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <line x1="56" y1="22" x2="56" y2="32" stroke="var(--color-accent)" strokeWidth="2.5" strokeLinecap="round" />
      <circle cx="56" cy="18" r="4" fill="var(--color-accent)" />
      <rect x="24" y="34" width="64" height="48" rx="22" fill="var(--color-accent)" />
      <rect x="32" y="44" width="48" height="24" rx="12" fill="var(--color-bg)" fillOpacity="0.92" />
      <circle className="companion-robot-eye" cx="46" cy="56" r="3.2" fill="var(--color-accent)" />
      <circle className="companion-robot-eye" cx="66" cy="56" r="3.2" fill="var(--color-accent)" />
      <path
        d="M48 66c2.2 2.4 5.2 3.6 8 3.6s5.8-1.2 8-3.6"
        stroke="var(--color-bg)"
        strokeOpacity="0.85"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="30" cy="58" r="3" fill="var(--color-accent)" fillOpacity="0.55" />
      <circle cx="82" cy="58" r="3" fill="var(--color-accent)" fillOpacity="0.55" />
    </svg>
  )
}

/** Live session presence: a speaking / listening node. */
function LiveNode({ lit }: { lit: boolean }) {
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
  const shape = liveOpen ? 'live-node' : 'robot'
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
              placeholder="Ask on this page"
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
      {!liveOpen && (
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
      )}
      <div className="companion-character-wrap" data-lit={lit ? 'true' : 'false'}>
        <div
          aria-hidden="true"
          data-testid="companion-character"
          data-shape={shape}
          className={reduce ? '' : 'companion-idle'}
        >
          {liveOpen ? <LiveNode lit={lit} /> : <RobotCharacter />}
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
          aria-label="Write in the assistant console"
          title="Open the console to type, upload, and review history"
          className="flex items-center gap-1.5 px-3 py-2 text-[11px] text-text hover:text-accent"
        >
          <Pencil size={14} />
          Write
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onSpeak}
          data-testid="companion-speak"
          aria-label="Dictate into the assistant console"
          title="Open the console and reviewed dictation"
          className="flex items-center gap-1.5 px-3 py-2 text-[11px] text-text hover:text-accent"
        >
          <Mic size={14} />
          Dictate
        </button>
        <span className="h-4 w-px bg-border" aria-hidden="true" />
        <button
          type="button"
          onClick={onLive}
          data-testid="companion-live"
          aria-label="Start a live conversation on this page"
          aria-pressed={liveOpen}
          title="Talk here. The console stays closed unless you ask to open it."
          className={`flex items-center gap-1.5 px-3 py-2 text-[11px] hover:text-accent ${
            liveOpen ? 'text-accent' : 'text-text'
          }`}
        >
          <Radio size={14} />
          Live
        </button>
      </div>
    </div>
  )
}
