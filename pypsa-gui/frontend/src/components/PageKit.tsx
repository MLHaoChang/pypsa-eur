// PageKit — the "Portfolio Optimizer" design-system primitives, shared by
// every full-page / slide-panel tab so they read as one consistent family.
//
// Mirrors the design handoff's pages.jsx primitives (PageHeader, PageSection,
// StatCard, Field, Toggle, Btn, Seg) recreated in React + Tailwind against the
// design tokens in index.css. Translate the design's raw CSS classes
// (.page-head / .page-sec / .stat-card / .btn …) into these components.

import { createContext, useContext, useLayoutEffect } from 'react'
import type { ReactNode, CSSProperties, ButtonHTMLAttributes } from 'react'

// ── Panel chrome ─────────────────────────────────────────────────────────────
// A panel opened from the sidebar sits in App's FullPageTab, which draws a
// breadcrumb row (eyebrow / title / Close). A page that also draws a
// PageHeader used to stack the two, ~60 px of chrome before any content (UX
// assessment Q12). Inside a FullPageTab the PageHeader takes over: it draws
// ONE compact row carrying the Close button and tells the frame to drop its
// breadcrumb. Outside one (the admin pages) nothing changes.
export interface PanelChrome {
  eyebrow: ReactNode
  onClose: () => void
  /** A PageHeader calls this on mount (true) and unmount (false). */
  claimHeader: (claimed: boolean) => void
}
export const PanelChromeContext = createContext<PanelChrome | null>(null)

// ── PageHeader ───────────────────────────────────────────────────────────────
// Red mono eyebrow → 22px display title → subtitle → right-aligned actions,
// over a subtle bg→bg-2 gradient with a 1px accent hairline along the bottom.
export function PageHeader({
  eyebrow, title, subtitle, actions,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  const chrome = useContext(PanelChromeContext)
  // Layout effect, so the frame drops its breadcrumb before the first paint.
  useLayoutEffect(() => {
    if (!chrome) return
    chrome.claimHeader(true)
    return () => chrome.claimHeader(false)
  }, [chrome])
  if (chrome) {
    return (
      <header
        className="relative flex items-center gap-4 px-6 py-2.5 border-b border-border shrink-0"
        style={{ background: 'linear-gradient(180deg, var(--color-bg) 0%, var(--color-bg-2) 100%)' }}
        data-testid="page-header-compact"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 min-w-0">
            <span className="font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-accent shrink-0">
              {eyebrow ?? chrome.eyebrow}
            </span>
            <span className="text-border-3 shrink-0">/</span>
            <h1 className="text-[16px] font-semibold text-text tracking-[-0.01em] leading-tight m-0 truncate">
              {title}
            </h1>
          </div>
          {subtitle && (
            <p className="text-[12px] text-muted leading-snug max-w-[720px] m-0 mt-0.5">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        <button
          type="button"
          onClick={chrome.onClose}
          title="Close (Esc)"
          className="shrink-0 flex items-center gap-1.5 text-[11px] text-muted hover:text-text px-2 py-1 rounded hover:bg-panel transition-colors"
        >
          Close
          <span aria-hidden className="text-[15px] leading-none">×</span>
        </button>
        <span
          aria-hidden
          className="absolute left-6 right-6 -bottom-px h-px pointer-events-none"
          style={{ background: 'linear-gradient(90deg, var(--color-accent) 0%, transparent 32%)' }}
        />
      </header>
    )
  }
  return (
    <header
      className="relative flex items-start gap-6 px-8 pt-7 pb-5 border-b border-border shrink-0"
      style={{ background: 'linear-gradient(180deg, var(--color-bg) 0%, var(--color-bg-2) 100%)' }}
    >
      <div className="flex-1 min-w-0">
        {eyebrow && (
          <div className="font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-accent mb-2">
            {eyebrow}
          </div>
        )}
        <h1 className="text-[22px] font-semibold text-text tracking-[-0.02em] leading-tight m-0 mb-1.5">
          {title}
        </h1>
        {subtitle && (
          <p className="text-[12.5px] text-muted leading-relaxed max-w-[640px] m-0">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      <span
        aria-hidden
        className="absolute left-8 right-8 -bottom-px h-px pointer-events-none"
        style={{ background: 'linear-gradient(90deg, var(--color-accent) 0%, transparent 32%)' }}
      />
    </header>
  )
}

// ── PageBody ─────────────────────────────────────────────────────────────────
// The scrollable content area below a PageHeader — 32px horizontal padding,
// 20px vertical rhythm between sections.
export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={`flex-1 min-h-0 overflow-y-auto px-8 py-5 flex flex-col gap-5 ${className ?? ''}`}>
      {children}
    </div>
  )
}

// ── PageSection ──────────────────────────────────────────────────────────────
// A 10px-radius card. Optional header bar (title + mono count + hint + a
// right-aligned slot for controls) sits on the subtle bg-2 surface.
export function PageSection({
  title, hint, count, right, children, className, bodyClassName,
}: {
  title?: ReactNode
  hint?: ReactNode
  count?: number | string
  right?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section
      className={`rounded-[10px] border border-border bg-bg overflow-hidden
        shadow-[0_1px_0_rgba(10,14,20,0.04)] ${className ?? ''}`}
    >
      {title != null && (
        <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border bg-bg-2">
          <div className="flex items-baseline gap-2 min-w-0">
            <h2 className="text-[12.5px] font-semibold text-text tracking-[-0.005em] truncate">{title}</h2>
            {count != null && <span className="font-mono text-[11px] text-muted shrink-0">{count}</span>}
            {hint && <span className="text-[11px] text-muted truncate">{hint}</span>}
          </div>
          {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
        </header>
      )}
      <div className={bodyClassName ?? 'p-4'}>{children}</div>
    </section>
  )
}

// ── RowGrid ──────────────────────────────────────────────────────────────────
// Equal-width grid for a row of StatCards (or any equal cards).
export function RowGrid({
  cols = 4, children, className,
}: {
  cols?: 2 | 3 | 4 | 5
  children: ReactNode
  className?: string
}) {
  const c = cols === 2 ? 'grid-cols-2'
    : cols === 3 ? 'grid-cols-3'
    : cols === 5 ? 'grid-cols-5'
    : 'grid-cols-4'
  return <div className={`grid gap-3.5 ${c} ${className ?? ''}`}>{children}</div>
}

// ── StatCard ─────────────────────────────────────────────────────────────────
// 9px uppercase eyebrow → 24px mono tnum value (+ faint unit) → optional
// state-coloured delta → optional mono sub-line. `accent` adds the 3px red
// gradient left rail for a strip's headline metric.
export function StatCard({
  eyebrow, value, unit, delta, deltaState, sub, accent, title,
}: {
  eyebrow: ReactNode
  value: ReactNode
  unit?: ReactNode
  delta?: ReactNode
  deltaState?: 'ok' | 'warn' | 'err'
  sub?: ReactNode
  accent?: boolean
  title?: string
}) {
  const deltaColor =
    deltaState === 'ok' ? 'text-success' :
    deltaState === 'warn' ? 'text-warn' :
    deltaState === 'err' ? 'text-danger' : 'text-muted'
  return (
    <div
      title={title}
      className={`relative overflow-hidden rounded-[10px] border bg-bg px-4 py-4
        shadow-[0_1px_0_rgba(10,14,20,0.04)] ${accent ? 'border-accent-100' : 'border-border'}`}
    >
      {accent && (
        <span
          aria-hidden
          className="absolute left-0 inset-y-0 w-[3px]"
          style={{ background: 'linear-gradient(180deg, var(--color-accent) 0%, var(--color-accent-700) 100%)' }}
        />
      )}
      <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted mb-2">{eyebrow}</div>
      <div className="flex items-baseline gap-1">
        <span className="font-mono text-[24px] font-semibold leading-none text-text tracking-[-0.025em] tabular-nums">
          {value}
        </span>
        {unit && <span className="text-[13px] text-muted">{unit}</span>}
      </div>
      {delta && (
        <div className={`mt-2 inline-flex items-center gap-1 font-mono text-[10.5px] font-medium ${deltaColor}`}>
          {delta}
        </div>
      )}
      {sub && <div className="mt-1 font-mono text-[10px] text-muted">{sub}</div>}
    </div>
  )
}

// ── Field ────────────────────────────────────────────────────────────────────
// Stacked (or inline) label / control / mono hint.
export function Field({
  label, hint, children, row,
}: {
  label: ReactNode
  hint?: ReactNode
  children: ReactNode
  row?: boolean
}) {
  return (
    <label className={`flex ${row ? 'flex-row items-center gap-2' : 'flex-col gap-1'}`}>
      <span className="text-[10.5px] font-medium text-muted">{label}</span>
      <span className="block">{children}</span>
      {hint && <span className="font-mono text-[9.5px] text-ink-400 mt-0.5">{hint}</span>}
    </label>
  )
}

// ── Toggle ───────────────────────────────────────────────────────────────────
// 26×15 pill, success-green when on. The whole row is the click target, and
// it is a real switch (a <button role="switch">), so it takes keyboard focus,
// toggles on Space/Enter and announces its state.
export function Toggle({
  on, onChange, label,
}: {
  on: boolean
  onChange?: (v: boolean) => void
  label: ReactNode
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange?.(!on)}
      className="inline-flex items-center gap-2.5 w-full py-2.5 px-3 rounded-lg cursor-pointer
        select-none transition-colors hover:bg-panel text-left"
    >
      <span
        className={`relative w-[26px] h-[15px] rounded-full shrink-0 transition-colors
          ${on ? 'bg-success' : 'bg-border-2'}`}
      >
        <span
          className={`absolute left-[1.5px] top-[1.5px] w-3 h-3 bg-white rounded-full
            shadow-[0_1px_2px_rgba(0,0,0,0.2)] transition-transform
            ${on ? 'translate-x-[11px]' : ''}`}
        />
      </span>
      <span className="text-[12px] text-ink-700">{label}</span>
    </button>
  )
}

// ── Btn ──────────────────────────────────────────────────────────────────────
// 30px-tall, 7px-radius button. `ghost` = white + ink-700 + strong hairline;
// `primary` = accent gradient + inset highlight.
export function Btn({
  variant = 'ghost', children, className, style, ...props
}: { variant?: 'ghost' | 'primary' } & ButtonHTMLAttributes<HTMLButtonElement>) {
  const base =
    'inline-flex items-center gap-1.5 h-[30px] px-3 text-[11.5px] rounded-[7px] ' +
    'transition-all active:scale-[0.97] whitespace-nowrap tracking-[-0.005em] ' +
    'disabled:opacity-40 disabled:cursor-not-allowed'
  if (variant === 'primary') {
    return (
      <button
        {...props}
        className={`${base} font-semibold hover:brightness-105 ${className ?? ''}`}
        style={{
          color: 'var(--color-on-accent)',
          background: 'linear-gradient(180deg, var(--color-accent) 0%, var(--color-accent-700) 100%)',
          boxShadow: '0 1px 0 rgba(0,0,0,0.18), 0 1px 2px rgba(0,0,0,0.14), inset 0 1px 0 rgba(255,255,255,0.18)',
          ...style,
        }}
      >
        {children}
      </button>
    )
  }
  return (
    <button
      {...props}
      className={`${base} font-medium bg-bg text-ink-700 border border-border-2
        hover:text-text hover:border-border-3 hover:bg-bg-2 ${className ?? ''}`}
      style={style}
    >
      {children}
    </button>
  )
}

// ── Seg ──────────────────────────────────────────────────────────────────────
// Segmented control — panel-tinted pill container, white softly-shadowed
// active segment.
export function Seg<T extends string>({
  value, onChange, options, title, className,
}: {
  value: T
  onChange: (v: T) => void
  options: Array<{ value: T; label: ReactNode; disabled?: boolean; title?: string }>
  title?: string
  className?: string
}) {
  return (
    <div
      title={title}
      className={`inline-flex items-center rounded-[7px] bg-panel border border-border p-0.5 text-[10.5px] ${className ?? ''}`}
    >
      {options.map(o => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          disabled={o.disabled}
          title={o.title}
          className={`px-2.5 h-[22px] rounded-[5px] font-medium transition-colors
            disabled:opacity-40 disabled:cursor-not-allowed
            ${value === o.value
              ? 'bg-bg text-text shadow-[0_1px_0_rgba(10,14,20,0.04)]'
              : 'text-muted hover:text-text'}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── Tag ──────────────────────────────────────────────────────────────────────
// Small status/category pill used in card lists (current / scenario / stress …).
const TAG_TONES: Record<string, string> = {
  accent: 'bg-accent-50 text-accent border-accent-100',
  ok: 'bg-success/10 text-success border-success/20',
  warn: 'bg-warn/10 text-warn border-warn/20',
  err: 'bg-danger/10 text-danger border-danger/20',
  purple: 'bg-purple/10 text-purple border-purple/25',
  neutral: 'bg-panel text-muted border-border',
}
export function Tag({
  tone = 'neutral', children,
}: {
  tone?: 'accent' | 'ok' | 'warn' | 'err' | 'purple' | 'neutral'
  children: ReactNode
}) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9.5px] font-semibold
      uppercase tracking-wide border ${TAG_TONES[tone]}`}>
      {children}
    </span>
  )
}

// ── BarList ──────────────────────────────────────────────────────────────────
// The design system's `.bars` list — [label][track][mono value] rows. Bar
// widths scale to the largest absolute value in the set. Used for the
// "Capacity mix" / "Cost breakdown" style horizontal roll-ups.
export interface BarItem {
  label: string
  value: number          // magnitude that drives the bar width
  display: string        // formatted text shown at the row's right edge
  color: string
}
export function BarList({ items }: { items: BarItem[] }) {
  if (items.length === 0) {
    return <div className="py-6 text-center text-[11.5px] text-muted">No data</div>
  }
  const max = Math.max(...items.map(i => Math.abs(i.value)), 1e-9)
  return (
    <div className="flex flex-col gap-2.5">
      {items.map(it => (
        <div
          key={it.label}
          className="grid items-center gap-3 text-[11.5px]"
          style={{ gridTemplateColumns: '120px 1fr 88px' }}
        >
          <span className="text-ink-700 truncate" title={it.label}>{it.label}</span>
          <span className="h-1.5 rounded-full bg-panel overflow-hidden">
            <span
              className="block h-full rounded-full transition-[width] duration-200 ease-out"
              style={{ width: `${Math.min(100, (Math.abs(it.value) / max) * 100)}%`, background: it.color }}
            />
          </span>
          <span className="font-mono text-text text-right tabular-nums">{it.display}</span>
        </div>
      ))}
    </div>
  )
}

// ── Sparkline ────────────────────────────────────────────────────────────────
// 100×28 SVG polyline — the design's tiny inline trend glyph.
export function Sparkline({
  data, color = 'var(--color-accent)', className,
}: {
  data: number[]
  color?: string
  className?: string
}) {
  if (data.length < 2) return null
  const w = 100, h = 28
  const min = Math.min(...data), max = Math.max(...data)
  const span = max - min || 1
  const pts = data
    .map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / span) * (h - 4) - 2}`)
    .join(' ')
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={className}
      style={{ width: '100%', height: '100%', display: 'block' } as CSSProperties}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
