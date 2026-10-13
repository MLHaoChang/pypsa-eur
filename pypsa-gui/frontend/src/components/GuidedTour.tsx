// In-app walkthrough guide (plan 2026-09-26 P21).
//
// The tour text is NOT written here: it is the backend catalogue
// (`backend/data/guides/eh_fmea_guide.json`, GET /api/guides/eh_fmea), the
// same text the chat assistant reads through get_feature_guide — so the tour
// and the assistant cannot describe a field differently. Each step anchors to
// a `data-testid`; a step whose target is not on screen tries its `reveal`
// control once, then is shown in the bottom-left corner with a note (an optional step whose
// target is absent when it is reached is skipped) — a renamed test id never
// crashes the tour, and a test pins every target to a real component.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { HelpCircle } from 'lucide-react'
import { create } from 'zustand'
import client from '../api/client'
import { useUIStore } from '../store/uiStore'

export interface GuideStep {
  target: string
  title: string
  body: string
  enter?: string
  reveal?: string
  optional?: boolean
  /** The control only exists once a study has run (report, pipeline, …). */
  after_run?: boolean
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

const sameRect = (a: Rect | null, b: Rect | null) => a === b || (!!a && !!b
  && a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height)

/** A step is shown when it is required, or optional with its target on
 *  screen right now (P30 B7: judged when reached), or optional and on screen
 *  when the tour started (the pre-P30 rule: its `reveal` brings it back
 *  after an earlier step's reveal hid it). */
function available(s: GuideStep, k: number, atStart: ReadonlySet<number>): boolean {
  return !s.optional || atStart.has(k) || findTarget(s.target) !== null
}
const NONE: ReadonlySet<number> = new Set()

export type Placement = 'below' | 'above' | 'right' | 'left' | 'free'

const GAP = 12      // popover ↔ target
const MARGIN = 8    // popover ↔ viewport edge
const RING = 4      // the highlight box grows the target by this on each side
export const POPOVER_W = 320

/** Where the popover goes (P30 B4): the first of below, above, right, left
 *  that fits inside the viewport and does not touch the highlight box; else
 *  `free` — the side with the most room, clamped into the viewport. `size`
 *  is the measured popover (CSS caps it at the viewport minus the margins). */
export function placePopover(t: Rect, size: { w: number; h: number }, vw: number, vh: number):
  { top: number; left: number; placement: Placement } {
  const w = Math.min(size.w, vw - 2 * MARGIN)
  const h = Math.min(size.h, vh - 2 * MARGIN)
  const bottom = t.top + t.height
  const right = t.left + t.width
  const hl = { top: t.top - RING, left: t.left - RING, bottom: bottom + RING, right: right + RING }
  const clampX = (x: number) => Math.min(Math.max(MARGIN, x), vw - MARGIN - w)
  const clampY = (y: number) => Math.min(Math.max(MARGIN, y), vh - MARGIN - h)
  const candidates: [Placement, number, number][] = [
    ['below', bottom + GAP, clampX(t.left)],
    ['above', t.top - GAP - h, clampX(t.left)],
    ['right', clampY(t.top), right + GAP],
    ['left', clampY(t.top), t.left - GAP - w],
  ]
  for (const [placement, top, left] of candidates) {
    const inside = top >= MARGIN && top + h <= vh - MARGIN
      && left >= MARGIN && left + w <= vw - MARGIN
    const apart = left + w <= hl.left || left >= hl.right || top + h <= hl.top || top >= hl.bottom
    if (inside && apart) return { top, left, placement }
  }
  // Nothing fits: the side with the most room for the popover's own size.
  const room: [number, number, number][] = [
    [(vh - MARGIN - (bottom + GAP)) / h, bottom + GAP, clampX(t.left)],
    [(t.top - GAP - MARGIN) / h, t.top - GAP - h, clampX(t.left)],
    [(vw - MARGIN - (right + GAP)) / w, clampY(t.top), right + GAP],
    [(t.left - GAP - MARGIN) / w, clampY(t.top), t.left - GAP - w],
  ]
  const [, top, left] = room.reduce((a, b) => (b[0] > a[0] ? b : a))
  return { top: clampY(top), left: clampX(left), placement: 'free' }
}

export function GuidedTour({ tourId, topic = GUIDE_TOPIC, onClose }: {
  tourId: string
  topic?: string
  onClose: () => void
}) {
  const { data, isError } = useGuide(topic)
  const tour = data?.tours?.[tourId]
  // While a tour is on screen Guided must not auto-open hubDesign over its
  // targets (guided-mode spec §10 addendum, gate P23 B2).
  useEffect(() => {
    useUIStore.getState().holdGuidedTour()
    return () => useUIStore.getState().releaseGuidedTour()
  }, [])
  // Every step is kept; an optional one is judged when it is reached (B7).
  const steps = tour?.steps ?? []
  const [idx, setIdx] = useState<number | null>(null)
  const [rect, setRect] = useState<Rect | null>(null)
  // Which optional targets are on screen now (for the counter and the
  // Next / Done label); refreshed on each move and when the page changes.
  const [avail, setAvail] = useState<string>('')
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const [atStart, setAtStart] = useState<ReadonlySet<number>>(NONE)

  useEffect(() => {
    if (!tour) return
    const start = new Set(tour.steps.flatMap((s, k) =>
      (s.optional && findTarget(s.target) !== null ? [k] : [])))
    setAtStart(start)
    const first = tour.steps.findIndex((s, k) => available(s, k, start))
    setIdx(first >= 0 ? first : null)
  }, [tour])

  const step = idx === null ? undefined : steps[idx]

  // Re-read the target's box and which optional steps are available. Batched
  // per frame; state changes only when something actually moved (spec §5.10).
  const frame = useRef<number | null>(null)
  // Read at frame time: a frame queued before a move must use the new step.
  const live = useRef({ step, steps, atStart })
  live.current = { step, steps, atStart }
  const refresh = useCallback(() => {
    if (frame.current !== null) return
    const run = () => {
      frame.current = null
      const { step: cur, steps: all, atStart: st } = live.current
      if (cur) {
        const r = rectOf(findTarget(cur.target))
        setRect(prev => (sameRect(prev, r) ? prev : r))
      }
      const a = all.map((s, k) => (available(s, k, st) ? '1' : '0')).join('')
      setAvail(prev => (prev === a ? prev : a))
      const el = boxRef.current
      if (el) {
        const b = el.getBoundingClientRect()
        const next = { w: Math.round(b.width), h: Math.round(b.height) }
        setSize(prev => (prev && prev.w === next.w && prev.h === next.h ? prev : next))
      }
    }
    frame.current = typeof requestAnimationFrame === 'function'
      ? requestAnimationFrame(run) : (setTimeout(run, 16) as unknown as number)
  }, [])
  useEffect(() => () => {
    if (frame.current !== null && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(frame.current)
    }
  }, [])

  // Locate (and if needed reveal) the step's target.
  useLayoutEffect(() => {
    if (!step) return
    let el = findTarget(step.target)
    let retry: ReturnType<typeof setTimeout> | undefined
    if (!el && step.reveal) {
      findTarget(step.reveal)?.click()
      // The revealed control renders on React's next commit: look again.
      retry = setTimeout(() => {
        const later = findTarget(step.target)
        later?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
        setRect(rectOf(later))
        setAvail(steps.map((s, k) => (available(s, k, atStart) ? '1' : '0')).join(''))
      }, 60)
    }
    el?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
    setRect(rectOf(el))
    setAvail(steps.map((s, k) => (available(s, k, atStart) ? '1' : '0')).join(''))
    return () => { if (retry) clearTimeout(retry) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step])

  // Follow the page: scroll, resize, the popover's own size, and DOM changes
  // (an optional target appearing, the target moving or going away).
  useEffect(() => {
    if (!step) return
    window.addEventListener('resize', refresh)
    window.addEventListener('scroll', refresh, true)
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(refresh) : null
    if (ro && boxRef.current) ro.observe(boxRef.current)
    const mo = typeof MutationObserver !== 'undefined' ? new MutationObserver(refresh) : null
    mo?.observe(document.body, { childList: true, subtree: true })
    return () => {
      window.removeEventListener('resize', refresh)
      window.removeEventListener('scroll', refresh, true)
      ro?.disconnect()
      mo?.disconnect()
    }
  }, [step, refresh])

  // The popover's measured size (after each commit; state only on change).
  useLayoutEffect(() => {
    const el = boxRef.current
    if (!el) return
    const b = el.getBoundingClientRect()
    const next = { w: Math.round(b.width), h: Math.round(b.height) }
    if (!size || size.w !== next.w || size.h !== next.h) setSize(next)
  })

  const close = useCallback((done: boolean) => {
    if (done) markSeen(tourId)
    onClose()
  }, [onClose, tourId])
  const next = useCallback(() => {
    if (idx === null) return
    // Judged now: an optional step whose target appeared since is shown.
    const j = steps.findIndex((s, k) => k > idx && available(s, k, atStart))
    if (j < 0) close(true)
    else setIdx(j)
  }, [idx, steps, close, atStart])
  const back = useCallback(() => {
    if (idx === null) return
    for (let k = idx - 1; k >= 0; k--) {
      if (available(steps[k], k, atStart)) { setIdx(k); return }
    }
  }, [idx, steps, atStart])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Never hijack typing: the tour points users INTO form fields.
      const t = e.target as HTMLElement | null
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) {
        return
      }
      if (e.key === 'Escape') close(false)
      else if (e.key === 'ArrowRight') next()
      else if (e.key === 'ArrowLeft') back()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close, next, back])

  // Placed by fit around the measured popover (B4); docked bottom-left when
  // the target is not on screen. When nothing fits (`free`), the target is scrolled into
  // view once per step so the reader can still see it.
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1024
  const vh = typeof window !== 'undefined' ? window.innerHeight : 768
  const measured = size && size.w > 0 && size.h > 0 ? size : { w: POPOVER_W, h: 180 }
  const placed = rect ? placePopover(rect, measured, vw, vh) : null
  const placement: Placement = placed?.placement ?? 'free'
  const scrolledFor = useRef<GuideStep | null>(null)
  useEffect(() => {
    if (!step || !placed || placement !== 'free' || scrolledFor.current === step) return
    scrolledFor.current = step
    findTarget(step.target)?.scrollIntoView?.({ block: 'center', inline: 'nearest' })
  }, [step, placed, placement])

  if (isError) {
    return (
      <div role="dialog" aria-label="Guide" data-testid="guide-tour"
           className="fixed bottom-4 right-4 z-[1000] max-w-xs rounded border border-border bg-panel p-3 text-[11px] shadow-lg">
        The guide could not be loaded.
        <button type="button" onClick={() => close(false)} className="ml-2 text-accent">Close</button>
      </div>
    )
  }
  if (!tour || !step || idx === null) return null

  // "n/m" among the steps shown now: required ones, optional ones whose
  // target is on screen, and the current one.
  const shown = steps.map((s, k) => k === idx || !s.optional || avail[k] === '1')
  const position = shown.slice(0, idx + 1).filter(Boolean).length
  const total = shown.filter(Boolean).length
  const isLast = !shown.some((v, k) => v && k > idx)
  const isFirst = !shown.some((v, k) => v && k < idx)

  const style: React.CSSProperties = {
    // No target on screen: docked in the bottom-left corner, not centred —
    // centred, it covered the very control the step asks the user to open.
    ...(placed ? { top: placed.top, left: placed.left } : {
      top: Math.max(8, vh - 8 - Math.min(measured.h, vh - 16)),
      left: 8,
    }),
    width: POPOVER_W, maxWidth: 'calc(100vw - 16px)',
    maxHeight: 'calc(100vh - 16px)', overflowY: 'auto',
  }

  return (
    <>
      {rect && (
        <div aria-hidden data-testid="guide-highlight"
             className="pointer-events-none fixed z-[999] rounded ring-2 ring-accent ring-offset-2"
             style={{ top: rect.top - 4, left: rect.left - 4,
                      width: rect.width + 8, height: rect.height + 8 }} />
      )}
      <div role="dialog" aria-label={tour.title} data-testid="guide-tour" ref={boxRef}
           data-step-target={step.target} data-placement={placement}
           className="fixed z-[1000] rounded border border-border bg-panel p-3 text-[11px] text-text shadow-lg"
           style={style}>
        <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">
          {tour.title} · {position}/{total}
        </div>
        {isFirst && tour.intro && (
          <p className="mb-2 text-muted" data-testid="guide-intro">{tour.intro}</p>
        )}
        <h4 className="font-semibold" data-testid="guide-step-title">{step.title}</h4>
        <p className="mt-1" data-testid="guide-step-body" aria-live="polite">{step.body}</p>
        {step.enter && (
          <p className="mt-1 text-accent" data-testid="guide-step-enter">
            <span className="font-semibold">What to enter: </span>{step.enter}
          </p>
        )}
        {!rect && (
          <p className="mt-1 text-warn" data-testid="guide-step-missing">
            {step.after_run
              ? 'This appears after a study has run — run one, then revisit this step.'
              : 'This control is not on screen right now — open the panel or select the component it belongs to, then continue.'}
          </p>
        )}
        <div className="mt-2 flex items-center gap-2">
          <button type="button" onClick={() => close(false)}
                  data-testid="guide-skip" className="text-muted hover:text-text">
            Skip tour
          </button>
          <span className="flex-1" />
          <button type="button" onClick={back} disabled={isFirst}
                  data-testid="guide-back"
                  className="rounded border border-border px-2 py-0.5 disabled:opacity-40">
            Back
          </button>
          <button type="button" onClick={next} data-testid="guide-next" autoFocus
                  className="rounded bg-accent px-2 py-0.5 font-semibold text-white">
            {isLast ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </>
  )
}

// A tour launched after a `prepare` step. `prepare` may unmount the button
// that started it (the tagging tour closes the Results panel the button sits
// in), so such a tour is rendered by the app-level GuidedTourHost instead of
// by the button. `seq` remounts the tour when the same one is relaunched;
// `project` is the project it was launched in — a switch closes it (its
// targets belong to the old project's network).
const useLaunchedTour = create<{ tourId: string | null; seq: number; project: string | null }>(
  () => ({ tourId: null, seq: 0, project: null }))

/** Renders the tour a prepared GuideButton launched (mounted once in App). */
export function GuidedTourHost() {
  const { tourId, seq, project } = useLaunchedTour()
  const currentProject = useUIStore(s => s.currentProject)
  const stale = tourId !== null && project !== currentProject
  useEffect(() => {
    if (stale) useLaunchedTour.setState({ tourId: null })
  }, [stale])
  // No host, no tour: a slot left set would make every button think its
  // tour is already showing.
  useEffect(() => () => useLaunchedTour.setState({ tourId: null }), [])
  if (!tourId || stale) return null
  return (
    <GuidedTour key={seq} tourId={tourId}
      onClose={() => useLaunchedTour.setState({ tourId: null })} />
  )
}

/** "Guide" button that starts a tour; dotted until the tour was finished.
 *  `prepare` (optional) runs and is awaited before the tour mounts; a failed
 *  prepare still opens the tour, which then notes the missing target. */
export function GuideButton({ tourId, testId, label = 'Guide', prepare }: {
  tourId: string
  testId: string
  label?: string
  prepare?: () => void | Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [seen, setSeen] = useState(() => tourSeen(tourId))
  const start = async () => {
    // The host already shows this tour (e.g. launched from Results, then the
    // Bus card's own button): don't stack a second identical overlay.
    if (useLaunchedTour.getState().tourId === tourId) return
    if (!prepare) {
      setOpen(true)
      return
    }
    // Held across the prepare step: prepare closes the full-screen panel on
    // purpose, and Guided must not read that as "nothing open yet". The
    // launched tour takes its own hold when it mounts.
    useUIStore.getState().holdGuidedTour()
    try {
      await prepare()
    } catch (e) {
      console.warn(`Guide '${tourId}': prepare failed`, e)
    } finally {
      useLaunchedTour.setState(s => ({
        tourId, seq: s.seq + 1, project: useUIStore.getState().currentProject }))
      useUIStore.getState().releaseGuidedTour()
    }
  }
  return (
    <>
      <button type="button" data-testid={testId}
              onClick={() => void start()}
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
