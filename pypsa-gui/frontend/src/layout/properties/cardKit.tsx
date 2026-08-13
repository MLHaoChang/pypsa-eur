// Shared "card kit" for PropertiesPanel: form-state helpers (toFS/nf/ni/no),
// input primitives, CardShell/EditShell, Row/Section/ExpandedProps/DetailFooter,
// discount-rate / build-year / lifetime helpers, BusSelect/CarrierGroupSelect,
// MiniProfileChart, VintageCloneButton. Extracted verbatim from PropertiesPanel
// (Step 1 of the card-split) — pure leaf helpers; the Edit cards stay in
// PropertiesPanel and import these. No back-dependency on PropertiesPanel.

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useCatalog } from '../../hooks/useCatalog'
import { editScope, loadExtras, saveExtras } from '../../utils/extrasStore'
import type { SolveMode } from '../../utils/attributeCatalog'
import { useQuery, useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  X, Plus, Zap, Battery, Database, GitBranch,
  Pencil, Trash2, Flame, Wind, BatteryCharging, ChevronRight, PlugZap, TrendingUp,
  HelpCircle,
} from 'lucide-react'
import { H2Icon } from '../../components/AssetIcons'
import { AreaChart, Area, ResponsiveContainer, Tooltip } from 'recharts'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { isRenewableCarrier } from '../../utils/carriers'
import { networkApi } from '../../api/network'
import { simulationApi } from '../../api/simulation'
import type { Bus, Carrier, Generator, Line, Link, LinkProfileMeta, Load, StorageUnit, Store, Transformer, LoadProfileMeta, GeneratorProfileMeta } from '../../api/types'
import { safeMax } from '../../utils/numeric'
import toast from 'react-hot-toast'
import { tip as docTip } from '../../utils/propertyDocs'
import { CARRIER_CATALOG_NAMES } from '../../utils/carrierCatalog'
import { vintageCloneToast } from '../../utils/toasts'
import VintagePeriodBoundsModal from '../../components/VintagePeriodBoundsModal'
import { buildYearOptions } from './buildYearOptions'


// ── Hover tooltip primitive ────────────────────────────────────────────────────
// Renders the label as plain text and a small (?) trigger next to it. ONLY
// the (?) glyph activates the tooltip — hovering the label text itself does
// nothing. This keeps the trigger area tight so users don't summon docs
// accidentally while reading the form.
// CSS-only `group-hover` tooltips were getting clipped in the edit form: the
// PropertiesPanel body uses `overflow-y-auto`, which per CSS spec promotes
// `overflow-x: visible` to `auto`, so absolute-positioned children that extend
// outside the panel get cut off. We portal the tooltip to <body> instead,
// position it via getBoundingClientRect() of the trigger, and drive open/close
// from React state. Works in both read and edit modes regardless of any
// scrollable ancestors.
//
// Positioning is viewport-aware: a two-pass render measures the tooltip's
// actual size and flips it leftward / upward when the default below-right
// anchor would push it off-screen. Important on the right property panel,
// where the trigger sits ~290 px from the viewport's right edge — a 224 px
// (w-56) tooltip anchored at trigger.left runs past the screen otherwise.
export const TOOLTIP_VIEWPORT_MARGIN = 8

export function InfoTip({ text, children }: { text?: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  // anchor describes where to put the tooltip relative to the trigger, then
  // the post-mount effect refines it once we can measure the tooltip element.
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLSpanElement | null>(null)
  const tipRef = useRef<HTMLSpanElement | null>(null)

  const reposition = () => {
    const el = triggerRef.current
    if (!el) return
    const t = el.getBoundingClientRect()
    // Use the tooltip's measured size if it's already in the DOM; otherwise
    // fall back to the worst-case Tailwind w-56 (224 px) for the first paint.
    const tip = tipRef.current
    const tw = tip ? tip.offsetWidth  : 224
    const th = tip ? tip.offsetHeight : 80
    const vw = window.innerWidth
    const vh = window.innerHeight

    // Default: align tooltip's left edge with the trigger's left edge.
    let left = t.left
    if (left + tw > vw - TOOLTIP_VIEWPORT_MARGIN) {
      // Flip rightward overflow: prefer aligning the tooltip's RIGHT edge with
      // the trigger's right edge so the arrow-of-attention stays near the (?).
      left = t.right - tw
      // …but if that still overflows the LEFT edge (very narrow viewports),
      // clamp to the margin so the tooltip is at least fully visible.
      if (left < TOOLTIP_VIEWPORT_MARGIN) left = TOOLTIP_VIEWPORT_MARGIN
    }

    // Default: just below the trigger.
    let top = t.bottom + 4
    if (top + th > vh - TOOLTIP_VIEWPORT_MARGIN) {
      // Not enough room below — flip above. Same clamp logic for top edge.
      top = t.top - th - 4
      if (top < TOOLTIP_VIEWPORT_MARGIN) top = TOOLTIP_VIEWPORT_MARGIN
    }
    setPos({ top, left })
  }

  const show = () => {
    reposition()  // initial guess using worst-case width
    setOpen(true)
  }
  const hide = () => setOpen(false)

  // Once the tooltip is mounted, re-measure and reposition. Without this, the
  // first paint uses the worst-case width estimate even when the actual
  // content is much narrower (single-line tooltips).
  useEffect(() => {
    if (!open) return
    reposition()
    // Reposition on resize too — orientation changes, window snap, etc.
    const onResize = () => reposition()
    const onScroll = () => setOpen(false)  // hide on scroll: avoids stale anchor
    window.addEventListener('resize', onResize)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('scroll', onScroll, true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!text) return <>{children}</>
  return (
    <span className="inline-flex items-baseline gap-0.5">
      {children}
      <span
        ref={triggerRef}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        tabIndex={0}
        className="relative inline-flex items-center cursor-help align-baseline"
      >
        <HelpCircle size={11} className={`shrink-0 transition-colors ${open ? 'text-accent' : 'text-muted/70'}`} />
      </span>
      {open && pos && createPortal(
        <span
          ref={tipRef}
          role="tooltip"
          className="fixed z-[1000] max-w-[14rem] rounded-md bg-text px-2 py-1.5 text-[10px] leading-snug text-white shadow-lg whitespace-normal pointer-events-none"
          style={{ top: pos.top, left: pos.left }}
        >
          {text}
        </span>,
        document.body,
      )}
    </span>
  )
}

// ── Read-only row ──────────────────────────────────────────────────────────────
export function Row({ label, value, unit, tip }: { label: string; value: unknown; unit?: string; tip?: string }) {
  if (value == null || value === '' || value === false) return null
  const display = typeof value === 'boolean' ? '✓' : typeof value === 'number' ? (value as number).toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(value)
  return (
    <div className="flex justify-between items-baseline py-0.5 border-b border-border/50 last:border-0">
      <span className="text-xs text-muted shrink-0 mr-2">
        <InfoTip text={tip}>{label}</InfoTip>
      </span>
      <span className="text-xs font-mono text-text text-right">{display}{unit ? <span className="text-muted ml-0.5">{unit}</span> : null}</span>
    </div>
  )
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <p className="text-[10px] font-bold text-accent uppercase tracking-wider mb-1">{title}</p>
      {children}
    </div>
  )
}

// Compact read-only property block rendered inside CardShell's children slot.
// Used by every asset card (Generator / StorageUnit / Store / Load / Link) so
// that each card shows its full parameter list expanded by default — the user
// can hover for descriptions and click the Edit pencil to mutate values.
export type ExpandedItem = { label: string; value: unknown; unit?: string; tip?: string }
export function ExpandedProps({ items }: { items: ExpandedItem[] }) {
  // Filter out empty / false / null values so the layout stays tight when an
  // attribute isn't set on this asset.
  const visible = items.filter(i => i.value != null && i.value !== '' && i.value !== false)
  if (visible.length === 0) return null
  return (
    <div className="mt-1.5 pt-1.5 border-t border-border/40">
      {visible.map(i => (
        <Row key={i.label} label={i.label} value={i.value} unit={i.unit} tip={i.tip} />
      ))}
    </div>
  )
}

// Bus/Line-style action row used at the bottom of every "detail" mode card.
// Includes optional profile-chart toggle + Edit + Delete buttons. Hosts the
// same pattern as BusPanel's "Edit Bus" footer so the layout matches across
// every asset type in the single-asset right panel.
export function DetailFooter({
  editLabel, assetName, onEdit, onDelete, deletePending, hasProfile, showChart, onToggleChart,
}: {
  editLabel: string
  // The asset's own name (gen.name / su.name / …) — used only to disambiguate
  // the Delete button's accessible name when several DetailFooters stack in
  // the "all connected assets" view (PropertiesPanel's per-bus asset list).
  // editLabel itself is per-type ("Edit Generator"), not per-instance, so it
  // can't do this job.
  assetName: string
  onEdit: () => void
  onDelete?: () => void
  deletePending?: boolean
  hasProfile?: boolean
  showChart?: boolean
  onToggleChart?: () => void
}) {
  return (
    <>
      {hasProfile && onToggleChart && (
        <button
          onClick={onToggleChart}
          className={`w-full mt-2 py-1.5 border rounded text-xs flex items-center justify-center gap-1.5 transition-colors
            ${showChart ? 'border-accent text-accent bg-accent/5' : 'border-border text-muted hover:border-accent hover:text-accent'}`}
        >
          <TrendingUp size={11} /> {showChart ? 'Hide profile chart' : 'Show profile chart'}
        </button>
      )}
      <div className="flex gap-2 mt-2">
        <button
          onClick={onEdit}
          className="flex-1 py-1.5 border border-border rounded text-xs text-muted hover:border-accent hover:text-accent transition-colors flex items-center justify-center gap-1.5"
        >
          <Pencil size={11} /> {editLabel}
        </button>
        {onDelete && (
          <button
            onClick={onDelete}
            disabled={deletePending}
            className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:border-danger hover:text-danger transition-colors disabled:opacity-40 flex items-center justify-center"
            title="Delete"
            aria-label={`Delete ${assetName}`}
          >
            <Trash2 size={11} />
          </button>
        )}
      </div>
    </>
  )
}

// ── Vintage clone button ──────────────────────────────────────────────────────
// Shared by GeneratorCard / StorageUnitCard / LinkCard. Renders a slim
// "Clone for future vintage" link below the DetailFooter. Click opens a toast
// with year + overnight_cost inputs; submitting calls the per-card onClone
// callback which spreads the full asset, overrides name/build_year/cost, and
// POSTs to the create endpoint. Used for the canonical PyPSA multi-period
// pattern: one component per build vintage so the LP optimises which vintage
// to construct in each period.
export function VintageCloneButton({
  assetName, defaultYear, defaultCost, onClone,
}: {
  assetName: string
  defaultYear: number
  defaultCost: number | null
  onClone: (year: number, overnightCost: number | null) => Promise<void>
}) {
  return (
    <button
      onClick={() => vintageCloneToast({
        assetName, defaultYear, defaultCost, onSubmit: onClone,
      })}
      className="w-full mt-2 py-1.5 border border-border rounded text-[11px] text-muted hover:border-accent hover:text-accent transition-colors flex items-center justify-center gap-1.5"
      title="Create a future-vintage copy with its own build_year + overnight_cost"
    >
      <Plus size={11} /> Clone for vintage
    </button>
  )
}

// ── Form utilities ─────────────────────────────────────────────────────────────
export type FS = Record<string, string>

export function toFS<T extends object>(obj: T, keys: (keyof T)[]): FS {
  return Object.fromEntries(keys.map(k => {
    const v = obj[k]
    if (v === null || v === undefined) return [k as string, '']
    if (typeof v === 'number' && !isFinite(v)) return [k as string, '']
    if (typeof v === 'boolean') return [k as string, v ? 'true' : 'false']
    return [k as string, String(v)]
  }))
}

export function nf(fs: FS, k: string, fb: number): number {
  const v = parseFloat(fs[k] ?? ''); return isNaN(v) ? fb : v
}
export function ni(fs: FS, k: string, fb: number): number {
  const v = parseInt(fs[k] ?? '', 10); return isNaN(v) ? fb : v
}
export function no(fs: FS, k: string): number | null {
  const s = (fs[k] ?? '').trim(); if (!s) return null; const v = parseFloat(s); return isNaN(v) ? null : v
}

/**
 * Form values for the chosen extra keys, ready to Object.assign onto a card's
 * payload (spec D20's save layer).
 *
 * Rendering an extras field alone is a lie: the value would land in `form`,
 * never reach `payload`, and be overwritten by the `...current` spread at
 * PropertiesPanel.tsx:144. This is the function that closes that gap.
 *
 * Conventions match what the cards already do: a blank is null (Pydantic's
 * Optional aliases turn it into PyPSA's ±inf / NaN sentinel), 'true'/'false'
 * are the strings toFS emits for booleans, and a numeric-looking string
 * becomes a number so it does not upcast a numeric column to object dtype.
 */
/**
 * Widen a card's form seed with the current values of its chosen extras
 * (spec D20's seed layer). Keeps the change at each card to one line instead
 * of surgery on its curated key array.
 */
export function seedExtras<T extends object>(obj: T, base: FS, keys: string[]): FS {
  return { ...base, ...toFS(obj, keys as (keyof T)[]) }
}

export function extrasPatch(fs: FS, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of keys) {
    const raw = fs[k]
    if (raw === undefined) continue          // never send undefined
    const t = raw.trim()
    if (t === '') { out[k] = null; continue }
    if (t === 'true') { out[k] = true; continue }
    if (t === 'false') { out[k] = false; continue }
    const n = Number(t)
    out[k] = Number.isFinite(n) ? n : raw
  }
  return out
}

// ── Shared mini input helpers (defined inline in each card) ───────────────────
export type SetFS = React.Dispatch<React.SetStateAction<FS>>

export function NumInput({ label, k, fs, set, unit, tip }: { label: string; k: string; fs: FS; set: SetFS; unit?: string; tip?: string }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] text-muted truncate">
        <InfoTip text={tip}>{label}{unit ? ` (${unit})` : ''}</InfoTip>
      </span>
      <input type="number" step="any" value={fs[k] ?? ''}
        onChange={e => set(p => ({ ...p, [k]: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono focus:outline-none focus:border-accent w-full" />
    </label>
  )
}

// Discount rate is stored as decimal on the asset (0.07 = 7%) but typed as
// percent in the UI ("7"). The form holds the percentage string under
// `discount_rate_pct`; the save mutation converts to decimal before sending.
// Blank input ⇒ NaN on the asset ⇒ solver fills with global discount_rate at
// solve time. Placeholder shows the global so the user knows what default
// kicks in if they leave it empty.
export function useGlobalDiscountRate(): number | undefined {
  const currentProject = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(currentProject, 'solverConfig'),
    queryFn: simulationApi.getSolverConfig,
    staleTime: 60_000,
  })
  return data?.discount_rate
}

/**
 * The configured solve mode, for D22's four mode-gated rules. One data
 * dependency for all four, read from the same solverConfig query the Solver
 * Settings page already loads.
 */
export function useSolveMode(): SolveMode {
  const currentProject = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(currentProject, 'solverConfig'),
    queryFn: simulationApi.getSolverConfig,
    staleTime: 60_000,
  })
  return data?.mode ?? 'lopf'
}

export function useGlobalDefaultLifetime(): number | undefined {
  const currentProject = useUIStore(s => s.currentProject)
  const { data } = useQuery({
    queryKey: nk(currentProject, 'solverConfig'),
    queryFn: simulationApi.getSolverConfig,
    staleTime: 60_000,
  })
  return data?.default_lifetime
}

// Options for the build-year dropdown. Reads the NETWORK's investment periods
// (`/network/investment_periods`) — the store Model Horizon writes and the LP
// reads. This used to read `cfg.investment_periods` from SolverConfig, which no
// frontend code ever writes, so it always fell through to the generic grid and
// a user with periods 2026/2027/2028 was offered 2025/2030/2035/…
export function useBuildYearOptions(currentValue: number | null | undefined): number[] {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: ip } = useQuery({
    queryKey: nk(currentProject, 'investmentPeriods'),
    queryFn: networkApi.getInvestmentPeriods,
    staleTime: 60_000,
  })
  const periods = useMemo(
    () => (ip?.periods ?? []).map(Number).filter(Number.isFinite),
    [ip?.periods],
  )
  return useMemo(
    () => buildYearOptions(periods, currentValue, new Date().getFullYear()),
    [periods, currentValue],
  )
}

export function BuildYearSelect({ fs, set, tip }: { fs: FS; set: SetFS; tip?: string }) {
  const current = (() => { const v = parseInt(fs.build_year ?? '', 10); return isNaN(v) ? null : v })()
  const options = useBuildYearOptions(current)
  // If the asset's current value is 0/blank (PyPSA default), show the first
  // option in the dropdown as the selected value so the user sees a real
  // year. The effect below also writes that value into the form state so
  // a no-touch save persists what's visible (rather than 0).
  const selected = current != null && current > 0 ? String(current) : (options[0] != null ? String(options[0]) : '')
  useEffect(() => {
    if ((current == null || current <= 0) && options[0] != null) {
      set(p => p.build_year === String(options[0]) ? p : { ...p, build_year: String(options[0]) })
    }
    // We intentionally do NOT depend on `current` — re-firing when the user
    // picks a (small/positive) year would create a feedback loop. Once the
    // form holds a valid year we leave it alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options])
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] text-muted truncate">
        <InfoTip text={tip}>Build year</InfoTip>
      </span>
      <select value={selected}
        onChange={e => set(p => ({ ...p, build_year: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono focus:outline-none focus:border-accent w-full">
        {options.map(y => <option key={y} value={y}>{y}</option>)}
      </select>
    </label>
  )
}

// startEdit helper: returns the lifetime string to pre-fill the form with.
// If the asset's value is null / NaN / inf (PyPSA default) we fall back to
// the global default_lifetime from solver settings — matches user
// expectation that "all assets default to the global lifetime". Existing
// non-default values are preserved.
export function defaultLifetime(assetLt: number | null | undefined, globalLt: number | undefined): string {
  if (assetLt != null && Number.isFinite(assetLt) && assetLt > 0) return String(assetLt)
  if (globalLt != null && Number.isFinite(globalLt) && globalLt > 0) return String(globalLt)
  return ''
}

export function DiscountRateInput({ fs, set, tip }: { fs: FS; set: SetFS; tip?: string }) {
  const globalRate = useGlobalDiscountRate()
  const placeholder = globalRate !== undefined
    ? `global (${(globalRate * 100).toFixed(1)}%)`
    : 'global'
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] text-muted truncate">
        <InfoTip text={tip}>Discount rate (%)</InfoTip>
      </span>
      <input type="number" step="any" placeholder={placeholder}
        value={fs.discount_rate_pct ?? ''}
        onChange={e => set(p => ({ ...p, discount_rate_pct: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono focus:outline-none focus:border-accent w-full" />
    </label>
  )
}

// toFS-equivalent for discount_rate: returns the percent string if the asset
// has a finite per-asset rate, else "" so the field falls through to the
// global placeholder. Kept separate from toFS so callers don't need to know
// about the decimal↔percent conversion or about the "_pct" suffix.
export function discountRatePct(rate: number | null | undefined): string {
  if (rate == null || !Number.isFinite(rate)) return ''
  return (rate * 100).toString()
}

export function TxtInput({ label, k, fs, set, tip }: { label: string; k: string; fs: FS; set: SetFS; tip?: string }) {
  return (
    <label className="col-span-2 flex flex-col gap-0.5">
      <span className="text-[9px] text-muted">
        <InfoTip text={tip}>{label}</InfoTip>
      </span>
      <input type="text" value={fs[k] ?? ''}
        onChange={e => set(p => ({ ...p, [k]: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-xs focus:outline-none focus:border-accent" />
    </label>
  )
}

// Coordinate-pair input — the default way to set a bus's location. Lets the
// user paste a "latitude, longitude" pair (the order Google Maps & co. use)
// into one field instead of the two separate Longitude/Latitude boxes below.
// Writes through to form.x (= longitude) and form.y (= latitude), so those
// fields stay the source of truth and remain editable for fine-tuning. Keeps
// its own raw-text state so a partially-typed value ("50.9, ") isn't clobbered
// mid-keystroke, and re-seeds from x/y whenever they change externally (e.g.
// the user edits a separate box) while this field is blurred.
export function CoordPairInput({ fs, set, tip }: { fs: FS; set: SetFS; tip?: string }) {
  const [raw, setRaw] = useState('')
  const [focused, setFocused] = useState(false)
  const [bad, setBad] = useState(false)

  // "lat, lng" string built from the current form x/y, or '' if either blank.
  const formatted = (): string => {
    const x = (fs.x ?? '').trim()
    const y = (fs.y ?? '').trim()
    return x === '' || y === '' ? '' : `${y}, ${x}`
  }
  // Re-seed the display from x/y while not actively editing — keeps the pair
  // in sync when the user edits the separate Longitude/Latitude boxes.
  useEffect(() => {
    if (!focused) { setRaw(formatted()); setBad(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fs.x, fs.y, focused])

  const onChange = (text: string) => {
    setRaw(text)
    const t = text.trim()
    if (t === '') { setBad(false); return }   // empty ⇒ leave x/y untouched
    // Accept "lat, lng" / "lat,lng" / "lat lng" — one comma and/or whitespace.
    const m = t.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/)
    if (!m) { setBad(true); return }
    const lat = parseFloat(m[1]), lng = parseFloat(m[2])
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) { setBad(true); return }
    setBad(false)
    set(p => ({ ...p, y: m[1], x: m[2] }))
  }

  return (
    <label className="col-span-2 flex flex-col gap-0.5">
      <span className="text-[9px] text-muted">
        <InfoTip text={tip}>Coordinates (lat, lng)</InfoTip>
      </span>
      <input
        type="text"
        value={raw}
        placeholder="50.938, 6.960"
        onFocus={() => setFocused(true)}
        onBlur={() => { setFocused(false); setBad(false) }}
        onChange={e => onChange(e.target.value)}
        className={`bg-bg border rounded px-1.5 py-0.5 text-xs font-mono focus:outline-none w-full ${
          bad ? 'border-danger focus:border-danger' : 'border-border focus:border-accent'
        }`}
      />
    </label>
  )
}

export function ChkInput({ label, k, fs, set, tip, onCheck }: {
  label: string; k: string; fs: FS; set: SetFS; tip?: string
  // Optional side-effect fired AFTER the boolean flips. Used by the Extendable
  // toggle to auto-fill *_nom_min from the current *_nom value — see callers.
  onCheck?: (checked: boolean, prev: FS) => FS
}) {
  return (
    <label className="col-span-2 flex items-center gap-1.5 cursor-pointer py-0.5">
      <input type="checkbox" checked={fs[k] === 'true'}
        onChange={e => {
          const checked = e.target.checked
          set(p => {
            const next = { ...p, [k]: checked ? 'true' : 'false' }
            return onCheck ? onCheck(checked, next) : next
          })
        }}
        className="accent-accent" />
      <span className="text-xs text-text">
        <InfoTip text={tip}>{label}</InfoTip>
      </span>
    </label>
  )
}

// Helper: when the user just turned on Extendable, seed the *_nom_min field
// with the current *_nom value (only if min is currently empty / "0", so we
// don't clobber a value the user already chose). Default behavior matches
// the PyPSA modelling intent of "allow expansion but keep current size as a
// floor"; the user can still type 0 afterwards to allow shrinkage.
export function autofillNomMin(nomKey: string, minKey: string) {
  return (checked: boolean, next: FS): FS => {
    if (!checked) return next
    const existing = (next[minKey] ?? '').trim()
    if (existing && existing !== '0') return next
    const nom = (next[nomKey] ?? '').trim()
    if (!nom) return next
    return { ...next, [minKey]: nom }
  }
}

export function SelInput({ label, k, fs, set, options, tip }: { label: string; k: string; fs: FS; set: SetFS; options: string[]; tip?: string }) {
  const current = fs[k] ?? ''
  const allOpts = current && !options.includes(current) ? [current, ...options] : options
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[9px] text-muted truncate">
        <InfoTip text={tip}>{label}</InfoTip>
      </span>
      <select
        value={current}
        onChange={e => set(p => ({ ...p, [k]: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] focus:outline-none focus:border-accent w-full"
      >
        {allOpts.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  )
}

export function categorizeCarriers(carriers: Carrier[], currentValue: string): { label: string; names: string[] }[] {
  // Merge: existing n.carriers + curated PyPSA-Eur catalog + the currently
  // selected value (in case it's a one-off carrier not in either source). The
  // catalog ensures the user can always pick a valid standard carrier (e.g.
  // onwind, CCGT) without having to first create it under "Carrier" tab.
  const allNames = [...new Set([
    ...carriers.map(c => c.name),
    ...CARRIER_CATALOG_NAMES,
    ...(currentValue ? [currentValue] : []),
  ])]
  type Group = { label: string; names: string[]; test: (n: string) => boolean }
  const groups: Group[] = [
    { label: 'Electricity',        names: [], test: n => n === 'AC' || n === 'DC' },
    { label: 'Hydrogen & Fuels',   names: [], test: n => { const l = n.toLowerCase(); return l.includes('h2') || l.includes('hydrogen') || l.includes('electrolysis') || l.includes('fuel cell') || l.includes('nh3') || l.includes('methanol') || l.includes('methane') || l.includes('ammonia') || l.includes('biogas') } },
    { label: 'Renewables',         names: [], test: n => { const l = n.toLowerCase(); return l.includes('wind') || l.includes('solar') || l.includes('pv') || l === 'ror' || l === 'hydro' || l.includes('geothermal') || l === 'run-of-river' || l.includes('rooftop') || l.includes('wave') } },
    { label: 'Thermal & Nuclear',  names: [], test: n => { const l = n.toLowerCase(); return l.includes('coal') || l.includes('lignite') || l.includes('ocgt') || l.includes('ccgt') || l.includes('nuclear') || l.includes('oil') || l.includes('gas') || l.includes('biomass') || l.includes('cgt') } },
    { label: 'Heat',               names: [], test: n => { const l = n.toLowerCase(); return l.includes('heat') || l.includes('chp') || l.includes('boiler') || l.includes('pump') } },
    { label: 'Storage',            names: [], test: n => { const l = n.toLowerCase(); return l.includes('battery') || l.includes('phs') || l.includes('pumped') || l.includes('tank') || l === 'co2' || l.includes('co2 stored') } },
  ]
  const assigned = new Set<string>()
  for (const g of groups) {
    for (const name of allNames) {
      if (!assigned.has(name) && g.test(name)) { g.names.push(name); assigned.add(name) }
    }
  }
  const other = allNames.filter(n => !assigned.has(n))
  return [...groups.filter(g => g.names.length > 0).map(g => ({ label: g.label, names: g.names })), ...(other.length ? [{ label: 'Other', names: other }] : [])]
}

// NOTE: shares its grouping logic with components/CarrierSelect.tsx. If
// the catalog or group definitions diverge across files, fix them both.
// Long-term, this duplication should be deleted in favour of the shared
// component — held back here only because PropertiesPanel embeds the
// InfoTip + 9px label style that CarrierSelect's default label doesn't
// match. Migrating to one source requires extending CarrierSelect's
// label/tip props first.
export function CarrierGroupSelect({ k, fs, set, carriers, tip }: { k: string; fs: FS; set: SetFS; carriers: Carrier[]; tip?: string }) {
  const current = fs[k] ?? ''
  const groups = categorizeCarriers(carriers, current)
  return (
    <label className="col-span-2 flex flex-col gap-0.5">
      <span className="text-[9px] text-muted">
        <InfoTip text={tip}>Carrier</InfoTip>
      </span>
      <select
        value={current}
        onChange={e => set(p => ({ ...p, [k]: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] focus:outline-none focus:border-accent w-full"
      >
        {groups.length === 0 && <option value={current}>{current || '—'}</option>}
        {groups.map(g => (
          <optgroup key={g.label} label={g.label}>
            {g.names.map(name => <option key={name} value={name}>{name}</option>)}
          </optgroup>
        ))}
      </select>
    </label>
  )
}

export function SectionHdr({ title }: { title: string }) {
  return <p className="text-[9px] font-bold text-accent/70 uppercase tracking-wider pt-2 pb-0.5 col-span-2 border-t border-border/40 mt-1">{title}</p>
}

// Bus selector — same shape as CarrierGroupSelect. Used by Load / Generator /
// StorageUnit / Store edit forms so a user can reassign an asset to a
// different bus after creation. Renders the current value as a synthetic
// option when the asset's bus isn't in the current list (e.g. the bus was
// renamed or deleted), so the user sees what's set rather than a silent
// empty selector.
export function BusSelect({ k, fs, set, buses, tip, label = 'Bus' }: {
  k: string; fs: FS; set: SetFS; buses: Bus[]; tip?: string; label?: string
}) {
  const current = (fs[k] as string | undefined) ?? ''
  const known = (buses ?? []).some(b => b.name === current)
  return (
    <label className="col-span-2 flex flex-col gap-0.5">
      <span className="text-[9px] text-muted">
        <InfoTip text={tip}>{label}</InfoTip>
      </span>
      <select
        value={current}
        onChange={e => set(p => ({ ...p, [k]: e.target.value }))}
        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] focus:outline-none focus:border-accent w-full"
      >
        {!current && <option value="">—</option>}
        {current && !known && (
          <option value={current}>{current} (not in network)</option>
        )}
        {(buses ?? []).map(b => (
          <option key={b.name} value={b.name}>
            {b.name}{b.v_nom ? ` · ${b.v_nom} kV` : ''}{b.carrier && b.carrier !== 'AC' ? ` (${b.carrier})` : ''}
          </option>
        ))}
      </select>
    </label>
  )
}

// ── Mini inline profile chart ──────────────────────────────────────────────────
export function MiniProfileChart({ component, attribute, name, unit = 'MW' }: {
  component: string; attribute: string; name: string; unit?: 'MW' | 'pu'
}) {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: tsData, isLoading } = useQuery({
    queryKey: nk(currentProject, 'timeseries', component, attribute, name),
    queryFn: () => networkApi.getTimeseries(component, attribute, [name]),
    staleTime: 60_000,
  })

  if (isLoading) return (
    <div className="h-20 flex items-center justify-center text-[10px] text-muted animate-pulse">Loading…</div>
  )
  if (!tsData || !tsData.columns.includes(name)) return (
    <div className="h-20 flex items-center justify-center text-[10px] text-muted">No data</div>
  )

  const colIdx = tsData.columns.indexOf(name)
  const chartData = tsData.index.map((ts, i) => ({
    ts: ts.slice(0, 10),
    v: tsData.data[i]?.[colIdx] ?? null,
  }))
  const vals = chartData.map(d => d.v).filter((v): v is number => v != null)
  const peak = safeMax(vals)
  const fmt = (v: number) => unit === 'pu' ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)} MW`

  return (
    <div className="mt-2 border-t border-border/40 pt-1.5">
      <div className="h-[72px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 2, right: 2, bottom: 0, left: 2 }}>
            <defs>
              <linearGradient id="mpg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#2f81f7" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#2f81f7" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <Area type="monotone" dataKey="v" stroke="#2f81f7" strokeWidth={1.2} fill="url(#mpg)" dot={false} isAnimationActive={false} />
            <Tooltip
              contentStyle={{ fontSize: 10, padding: '2px 6px', background: 'var(--color-panel)', color: 'var(--color-text)', border: '1px solid var(--color-border)' }}
              formatter={(v: number) => [fmt(v), '']}
              labelFormatter={() => ''}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex justify-between text-[9px] text-muted mt-0.5 px-0.5">
        <span>{vals.length} pts</span>
        <span>Peak {fmt(peak)}</span>
      </div>
    </div>
  )
}

// ── Collapsed card shell ───────────────────────────────────────────────────────
export function CardShell({ title, badge, meta, onEdit, onDelete, delPending, children, extraBadge, onToggleChart, chartActive }: {
  title: string; badge?: string; meta: string;
  onEdit: () => void; onDelete: () => void; delPending: boolean;
  children?: React.ReactNode;
  extraBadge?: React.ReactNode;
  onToggleChart?: () => void;
  chartActive?: boolean;
}) {
  return (
    <div className="bg-panel rounded px-2 py-1.5 mb-1.5 group relative">
      <div className="flex items-start justify-between gap-1">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5 flex-wrap">
            <span className="text-xs font-semibold text-text truncate max-w-[140px]">{title}</span>
            {badge && <span className="text-[9px] bg-border/60 rounded px-1 text-muted shrink-0">{badge}</span>}
            {extraBadge}
          </div>
          <p className="text-[10px] text-muted leading-tight">{meta}</p>
          {children}
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {onToggleChart && (
            <button
              onClick={onToggleChart}
              className={`p-1 rounded transition-colors ${chartActive ? 'text-accent bg-accent/10' : 'text-muted hover:text-accent hover:bg-accent/10'}`}
              title="Toggle profile chart"
              aria-label={`Toggle profile chart — ${title}`}
            >
              <TrendingUp size={11} />
            </button>
          )}
          <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
            <button onClick={onEdit} aria-label={`Edit ${title}`} className="p-1 text-muted hover:text-accent rounded hover:bg-accent/10 transition-colors">
              <Pencil size={11} />
            </button>
            <button onClick={onDelete} disabled={delPending} aria-label={`Delete ${title}`}
              className="p-1 text-muted hover:text-danger rounded hover:bg-danger/10 transition-colors disabled:opacity-40">
              <Trash2 size={11} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Edit form shell ────────────────────────────────────────────────────────────
export function EditShell({ title, onSave, onCancel, saving, children }: {
  title: string; onSave: () => void; onCancel: () => void; saving: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-bg border border-accent/30 rounded-lg px-3 pt-2 pb-3 mb-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-text truncate">{title}</span>
        <button onClick={onCancel} aria-label={`Close ${title} editor`} className="text-muted hover:text-text transition-colors"><X size={12} /></button>
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-1.5">
        {children}
      </div>
      <div className="flex gap-2 mt-3">
        <button onClick={onSave} disabled={saving}
          className="flex-1 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50">
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button onClick={onCancel}
          className="px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors">
          Cancel
        </button>
      </div>
    </div>
  )
}


// ── ExtrasSection ────────────────────────────────────────────────────────────
// The appended block on an edit card holding attributes beyond the card's
// curated set (spec D20). Additive: it renders as the last child of EditShell's
// 2-column grid and touches nothing above it.
export function ExtrasSection({ componentClass, fs, set, curated }: {
  componentClass: string
  fs: FS
  set: SetFS
  curated: string[]
}) {
  const { data } = useCatalog(componentClass)
  const [keys, setKeys] = useState<string[]>(() => loadExtras(editScope(componentClass)))
  const [picking, setPicking] = useState(false)

  // Until the catalog arrives the card renders exactly as it does today.
  if (!data) return null

  const curatedSet = new Set(curated)
  const byName = new Map(data.attributes.map(a => [a.name, a]))
  // Only Input attributes are offerable: an Output is computed by the solver
  // and writing one is meaningless (the same authority D13 uses in the grid).
  const offerable = data.attributes.filter(
    a => a.status.startsWith('Input') && !curatedSet.has(a.name) && !keys.includes(a.name),
  )

  const add = (name: string) => {
    const next = [...keys, name]
    setKeys(next)
    saveExtras(editScope(componentClass), next)
    setPicking(false)
    // Adding a parameter never seeds a value — the field opens empty and
    // PyPSA's default continues to apply until the user types one (D23).
    set(prev => ({ ...prev, [name]: prev[name] ?? '' }))
  }

  const drop = (name: string) => {
    const next = keys.filter(k => k !== name)
    setKeys(next)
    saveExtras(editScope(componentClass), next)
  }

  return (
    <div className="col-span-2 mt-2 pt-2 border-t border-border">
      <div className="text-[9px] font-semibold text-muted uppercase tracking-wide mb-1">
        More parameters
      </div>
      {keys.map(k => {
        const attr = byName.get(k)
        return (
          <label key={k} className="flex items-center gap-1.5 mb-1">
            <span className="text-[9px] text-muted w-28 shrink-0 truncate"
                  title={attr?.description ?? k}>
              {k}{attr?.unit ? ` (${attr.unit})` : ''}
            </span>
            <input
              value={fs[k] ?? ''}
              onChange={e => set(prev => ({ ...prev, [k]: e.target.value }))}
              placeholder={attr?.default_text ?? ''}
              className="flex-1 bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono focus:outline-none focus:border-accent"
            />
            <button type="button" onClick={() => drop(k)} aria-label={`Remove ${k}`}
                    className="text-muted hover:text-danger">
              <X size={11} />
            </button>
          </label>
        )
      })}
      {picking ? (
        <select
          autoFocus
          value=""
          onChange={e => { if (e.target.value) add(e.target.value) }}
          onBlur={() => setPicking(false)}
          className="w-full bg-bg border border-accent rounded px-1.5 py-0.5 text-[10px]"
        >
          <option value="">Choose a parameter…</option>
          {offerable.map(a => (
            <option key={a.name} value={a.name}>
              {a.name}{a.unit ? ` (${a.unit})` : ''} — {a.type}, default {a.default_text || '—'}
            </option>
          ))}
        </select>
      ) : (
        <button type="button" onClick={() => setPicking(true)}
                className="text-[9px] text-accent hover:underline">+ Add parameter</button>
      )}
    </div>
  )
}
