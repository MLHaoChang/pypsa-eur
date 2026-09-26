// In-app walkthrough guide (plan 2026-09-26 P21).
//
// The tour text is NOT written here: it is the backend catalogue
// (`backend/data/guides/eh_fmea_guide.json`, GET /api/guides/eh_fmea), the
// same text the chat assistant reads through get_feature_guide — so the tour
// and the assistant cannot describe a field differently. Each step anchors to
// a `data-testid`; a step whose target is not on screen tries its `reveal`
// control once, then is shown centred with a note (optional steps are
// skipped) — a renamed test id never crashes the tour, and a test pins every
// target to a real component.
import { useCallback, useEffect, useLayoutEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { HelpCircle } from 'lucide-react'
import client from '../api/client'

export interface GuideStep {
  target: string
  title: string
  body: string
  enter?: string
  reveal?: string
  optional?: boolean
}

export interface GuideTour {
  title: string
  intro?: string
  steps: GuideStep[]
}

export interface GuideCatalogue {
  version: number
  title?: string
  tours: Record<string, GuideTour>
  fields: Record<string, string>
}

export const GUIDE_TOPIC = 'eh_fmea'

export const guidesApi = {
  getGuide: (topic: string = GUIDE_TOPIC) =>
    client.get(`/guides/${encodeURIComponent(topic)}`)
      .then(r => r.data as GuideCatalogue),
}

/** The catalogue (fetched once per session; static content). */
export function useGuide(topic: string = GUIDE_TOPIC) {
  return useQuery({
    queryKey: ['guides', topic],
    queryFn: () => guidesApi.getGuide(topic),
    staleTime: Infinity,
    retry: false,
  })
}

/** Hover text for one field: the catalogue's wording, else `fallback`. */
export function useGuideField(key: string, fallback: string): string {
  const { data } = useGuide()
  return data?.fields?.[key] ?? fallback
}

const SEEN_PREFIX = 'pypsa-guide-seen:'

export function tourSeen(tourId: string): boolean {
  try {
    return window.localStorage.getItem(SEEN_PREFIX + tourId) === '1'
  } catch {
    return false
  }
}

function markSeen(tourId: string): void {
  try {
    window.localStorage.setItem(SEEN_PREFIX + tourId, '1')
  } catch {
    /* private window / blocked storage: the tour still works */
  }
}

function cssEscape(s: string): string {
  // CSS.escape is missing in some environments (jsdom, older WebViews).
  return typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
    ? CSS.escape(s) : s.replace(/["\\]/g, '\\$&')
}

function findTarget(id: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-testid="${cssEscape(id)}"]`)
}

interface Rect { top: number; left: number; width: number; height: number }

function rectOf(el: HTMLElement | null): Rect | null {
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (r.width === 0 && r.height === 0) return null
  return { top: r.top, left: r.left, width: r.width, height: r.height }
}

/** Resolve the steps to show: optional steps whose target is absent drop. */
function visibleSteps(steps: GuideStep[]): GuideStep[] {
  return steps.filter(s => !s.optional || findTarget(s.target) !== null)
}

export function GuidedTour({ tourId, topic = GUIDE_TOPIC, onClose }: {
  tourId: string
  topic?: string
  onClose: () => void
}) {
  const { data, isError } = useGuide(topic)
  const tour = data?.tours?.[tourId]
  const [steps, setSteps] = useState<GuideStep[]>([])
  const [idx, setIdx] = useState(0)
  const [rect, setRect] = useState<Rect | null>(null)

  useEffect(() => {
    if (tour) setSteps(visibleSteps(tour.steps))
  }, [tour])

  const step = steps[idx]

  // Locate (and if needed reveal) the step's target.
  useLayoutEffect(() => {
    if (!step) return
    let el = findTarget(step.target)
    let retry: ReturnType<typeof setTimeout> | undefined
    const onMove = () => setRect(rectOf(findTarget(step.target)))
    if (!el && step.reveal) {
      findTarget(step.reveal)?.click()
      // The revealed control renders on React's next commit: look again.
      retry = setTimeout(() => {
        const later = findTarget(step.target)
        later?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
        setRect(rectOf(later))
      }, 60)
    }
    el?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
    setRect(rectOf(el))
    window.addEventListener('resize', onMove)
    window.addEventListener('scroll', onMove, true)
    return () => {
      if (retry) clearTimeout(retry)
      window.removeEventListener('resize', onMove)
      window.removeEventListener('scroll', onMove, true)
    }
  }, [step])

  const close = useCallback((done: boolean) => {
    if (done) markSeen(tourId)
    onClose()
  }, [onClose, tourId])
  const next = useCallback(() => {
    if (idx >= steps.length - 1) close(true)
    else setIdx(i => i + 1)
  }, [idx, steps.length, close])
  const back = useCallback(() => setIdx(i => Math.max(0, i - 1)), [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false)
      else if (e.key === 'ArrowRight') next()
      else if (e.key === 'ArrowLeft') back()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close, next, back])

  if (isError) {
    return (
      <div role="dialog" aria-label="Guide" data-testid="guide-tour"
           className="fixed bottom-4 right-4 z-[1000] max-w-xs rounded border border-border bg-panel p-3 text-[11px] shadow-lg">
        The guide could not be loaded.
        <button type="button" onClick={() => close(false)} className="ml-2 text-accent">Close</button>
      </div>
    )
  }
  if (!tour || !step) return null

  // Popover below the target when there is room, else above; centred when
  // the target is not on screen.
  const W = 320
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
  const vh = typeof window !== 'undefined' ? window.innerHeight : 768
  let style: React.CSSProperties
  if (rect) {
    const below = rect.top + rect.height + 12
    const top = below + 180 < vh ? below : Math.max(8, rect.top - 192)
    const left = Math.min(Math.max(8, rect.left), vw - W - 8)
    style = { top, left, width: W }
  } else {
    style = { top: vh / 2 - 90, left: vw / 2 - W / 2, width: W }
  }

  return (
    <>
      {rect && (
        <div aria-hidden data-testid="guide-highlight"
             className="pointer-events-none fixed z-[999] rounded ring-2 ring-accent ring-offset-2"
             style={{ top: rect.top - 4, left: rect.left - 4,
                      width: rect.width + 8, height: rect.height + 8 }} />
      )}
      <div role="dialog" aria-label={tour.title} data-testid="guide-tour"
           data-step-target={step.target}
           className="fixed z-[1000] rounded border border-border bg-panel p-3 text-[11px] text-text shadow-lg"
           style={style}>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">
          {tour.title} · {idx + 1}/{steps.length}
        </div>
        {idx === 0 && tour.intro && (
          <p className="mb-2 text-muted" data-testid="guide-intro">{tour.intro}</p>
        )}
        <h4 className="font-semibold" data-testid="guide-step-title">{step.title}</h4>
        <p className="mt-1" data-testid="guide-step-body">{step.body}</p>
        {step.enter && (
          <p className="mt-1 text-accent" data-testid="guide-step-enter">
            <span className="font-semibold">What to enter: </span>{step.enter}
          </p>
        )}
        {!rect && (
          <p className="mt-1 text-warn" data-testid="guide-step-missing">
            This control is not on screen right now — open the panel or select
            the component it belongs to, then continue.
          </p>
        )}
        <div className="mt-2 flex items-center gap-2">
          <button type="button" onClick={() => close(false)}
                  data-testid="guide-skip" className="text-muted hover:text-text">
            Skip tour
          </button>
          <span className="flex-1" />
          <button type="button" onClick={back} disabled={idx === 0}
                  data-testid="guide-back"
                  className="rounded border border-border px-2 py-0.5 disabled:opacity-40">
            Back
          </button>
          <button type="button" onClick={next} data-testid="guide-next"
                  className="rounded bg-accent px-2 py-0.5 font-semibold text-white">
            {idx >= steps.length - 1 ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </>
  )
}

/** "Guide" button that starts a tour; dotted until the tour was finished. */
export function GuideButton({ tourId, testId, label = 'Guide' }: {
  tourId: string
  testId: string
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const [seen, setSeen] = useState(() => tourSeen(tourId))
  return (
    <>
      <button type="button" data-testid={testId}
              onClick={() => setOpen(true)}
              title="Step-by-step walkthrough of this panel: what each control does and what to enter."
              className="relative inline-flex items-center gap-1 px-2 py-0.5 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent">
        <HelpCircle size={11} /> {label}
        {!seen && (
          <span aria-label="not yet viewed"
                className="absolute -right-1 -top-1 h-1.5 w-1.5 rounded-full bg-accent" />
        )}
      </button>
      {open && (
        <GuidedTour tourId={tourId} onClose={() => {
          setOpen(false)
          setSeen(tourSeen(tourId))
        }} />
      )}
    </>
  )
}
