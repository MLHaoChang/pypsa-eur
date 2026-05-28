// Shared carrier-filter UX for the Results tabs.
//
// Every results tab that needs to scope its output to one carrier (or "All")
// uses the same hook + component pair. Centralising the UI here means:
//   • consistent look across tabs (tab-strip for ≤5 carriers, native select
//     otherwise — matches the design system's segmented control + select widgets)
//   • consistent storage (each tab passes its own `storageKey`, the hook
//     persists to localStorage and re-validates against the latest carrier
//     list on every mount so a stale persisted pick from a previous project
//     doesn't survive a load)
//   • a single source of truth for "this tab is currently filtered to carrier
//     X" so screenshot exports / CSV downloads / KPIs all read the same set
//
// The hook is intentionally generic — callers derive their own "carrier for
// row" function (some tabs filter by component.carrier, others by the
// component's attached bus.carrier, others by storage-carrier aliases). The
// hook only owns the UX + state.

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Seg } from './shared'

// Sentinel for "this row has no carrier set". `extractCarriers` rewrites
// blank carrier strings to this value AND `matchesSelected` normalises
// inputs to it before comparison, so the "Unspecified" segment filters to
// the rows the user expects (the QA gate caught the original mismatch where
// blank-carrier rows yielded `"" === "unspecified" → false`).
export const UNSPECIFIED_CARRIER = 'unspecified'

const normaliseCarrier = (c: string | undefined | null): string => {
  const trimmed = (c ?? '').trim()
  return trimmed === '' ? UNSPECIFIED_CARRIER : trimmed
}

// ── Hook ──────────────────────────────────────────────────────────────────
// `selectedCarrier === null` means "All". Caller decides what to do with the
// null value (typically: skip the filter and show every row).

export interface UseCarrierFilterArgs {
  /** Unique carrier names available on the current network for this tab's
   *  scope. Should be memoised by the caller — order is preserved in the UI. */
  availableCarriers: string[]
  /** Optional localStorage key. When provided, the selection persists across
   *  reloads. Prefix with the tab name to avoid collisions across tabs. */
  storageKey?: string
}

export interface CarrierFilterState {
  selectedCarrier: string | null
  setSelectedCarrier: (c: string | null) => void
  /** Convenience: true when the filter is set to a specific carrier (i.e. not "All"). */
  isFiltered: boolean
  /** Predicate suitable for `arr.filter(row => match(getCarrier(row)))`. When
   *  `selectedCarrier` is null this returns `true` for every input. */
  matchesSelected: (carrier: string | undefined | null) => boolean
}

const ALL_SENTINEL = '__ALL__'

export function useCarrierFilter(
  { availableCarriers, storageKey }: UseCarrierFilterArgs,
): CarrierFilterState {
  const initial = (): string | null => {
    if (!storageKey) return null
    try {
      const v = localStorage.getItem(storageKey)
      if (!v || v === ALL_SENTINEL) return null
      return v
    } catch { return null }
  }
  const [raw, setRaw] = useState<string | null>(initial)

  // Re-validate the persisted pick whenever the carrier list changes (e.g.
  // user loaded a different project mid-session). If the pick is no longer
  // present, silently clear it back to "All" rather than show a phantom
  // filter pointing nowhere. Done via effect (not in render) so the user
  // sees the change reflected in localStorage too.
  //
  // Skip when `availableCarriers` is empty — that's the "still loading"
  // state. Without this guard, a setSelectedCarrier("X") called immediately
  // after mount (before the parent's network query has settled) would race
  // with an effect that sees `raw=X, availableCarriers=[]` and clears the
  // just-set pick. QA-flagged race condition.
  useEffect(() => {
    if (availableCarriers.length === 0) return
    if (raw != null && !availableCarriers.includes(raw)) {
      setRaw(null)
      if (storageKey) {
        try { localStorage.removeItem(storageKey) } catch { /* ignore */ }
      }
    }
  }, [availableCarriers, raw, storageKey])

  // useCallback so consumers can safely list this in useEffect / useMemo
  // deps arrays without re-firing every render. The hook is called from
  // multiple tabs that pass this setter into children — stable identity
  // keeps memoised children from re-rendering needlessly.
  const setSelectedCarrier = useCallback((c: string | null) => {
    setRaw(c)
    if (!storageKey) return
    try {
      if (c == null) localStorage.removeItem(storageKey)
      else           localStorage.setItem(storageKey, c)
    } catch { /* ignore */ }
  }, [storageKey])

  const isFiltered = raw != null
  const matchesSelected = useMemo(() => {
    if (raw == null) return () => true
    const want = raw
    // Normalise the input the same way `extractCarriers` does — blank /
    // null rows map to UNSPECIFIED_CARRIER, so selecting "Unspecified"
    // filters to those rows correctly. QA-flagged bug from the original
    // bare `(carrier ?? '') === want` comparison.
    return (carrier: string | undefined | null) => normaliseCarrier(carrier) === want
  }, [raw])

  return { selectedCarrier: raw, setSelectedCarrier, isFiltered, matchesSelected }
}

// ── Component ─────────────────────────────────────────────────────────────
// Tab-strip for compact carrier lists, native select for longer ones. The
// "All" pill is always first and represents "no filter applied".

export interface CarrierFilterProps {
  availableCarriers: string[]
  selectedCarrier: string | null
  onChange: (c: string | null) => void
  /** Optional short label rendered before the control ("Carrier", "Show",
   *  "Storage", etc.). Omit for a bare control inside a chart header. */
  label?: ReactNode
  /** Cap above which the tab strip switches to a dropdown. Default 5. */
  segMax?: number
  className?: string
}

export function CarrierFilter({
  availableCarriers, selectedCarrier, onChange, label, segMax = 5, className,
}: CarrierFilterProps) {
  // One or zero carriers → nothing meaningful to filter. Render nothing so
  // the tab's header doesn't grow a useless control on simple networks.
  if (availableCarriers.length <= 1) return null

  const valueForSeg = selectedCarrier ?? ALL_SENTINEL
  const segOptions = [
    { value: ALL_SENTINEL, label: 'All' },
    ...availableCarriers.map(c => ({ value: c, label: carrierDisplayLabel(c) })),
  ]
  const segHandler = (v: string) => onChange(v === ALL_SENTINEL ? null : v)

  return (
    <div className={`inline-flex items-center gap-2 ${className ?? ''}`}>
      {label && (
        <span className="text-[10.5px] font-medium uppercase tracking-[0.08em] text-muted">
          {label}
        </span>
      )}
      {availableCarriers.length <= segMax ? (
        <Seg
          value={valueForSeg}
          onChange={segHandler}
          options={segOptions}
          title="Filter results by carrier"
        />
      ) : (
        <select
          value={valueForSeg}
          onChange={(e) => segHandler(e.target.value)}
          title="Filter results by carrier"
          className="h-[24px] rounded-[6px] border border-border bg-bg text-text px-2 text-[11px] font-medium
                     focus:outline-none focus:ring-1 focus:ring-accent"
        >
          {segOptions.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      )}
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────

/** Extract the unique, sorted list of carrier values from an array of
 *  network components. Non-string / blank carriers fold into
 *  `UNSPECIFIED_CARRIER` so they still appear as a filter option (instead
 *  of disappearing). The resulting list is suitable to pass straight to
 *  `useCarrierFilter`. */
export function extractCarriers<T>(
  rows: ReadonlyArray<T>,
  accessor: (row: T) => string | undefined | null,
): string[] {
  const seen = new Set<string>()
  for (const r of rows) seen.add(normaliseCarrier(accessor(r)))
  return [...seen].sort((a, b) => {
    // Push the sentinel to the end so the common carriers appear first in
    // the tab strip.
    if (a === UNSPECIFIED_CARRIER && b !== UNSPECIFIED_CARRIER) return 1
    if (b === UNSPECIFIED_CARRIER && a !== UNSPECIFIED_CARRIER) return -1
    return a.localeCompare(b)
  })
}

// ── Bindings ──────────────────────────────────────────────────────────────
// Convenience: pre-bind a hook state to the matching component props so
// callers can spread the result in JSX instead of repeating prop names.
//
//   const filter = useCarrierFilter({ availableCarriers, storageKey: 'cap:carrier' })
//   <CarrierFilter {...bindCarrierFilter(filter, availableCarriers)} label="Carrier" />
//
// Saves three lines at every consumer site (six tabs).
export function bindCarrierFilter(
  state: CarrierFilterState,
  availableCarriers: string[],
): Pick<CarrierFilterProps, 'availableCarriers' | 'selectedCarrier' | 'onChange'> {
  return {
    availableCarriers,
    selectedCarrier: state.selectedCarrier,
    onChange: state.setSelectedCarrier,
  }
}

/** Friendly display label for a carrier key — capitalises the first letter
 *  for the most common values (`ac` → `AC`, `h2` → `H2`, `heat` → `Heat`)
 *  and falls back to title-casing for everything else. */
export function carrierDisplayLabel(carrier: string): string {
  const c = carrier.trim()
  if (c === '') return 'Unspecified'
  // Acronyms we want left in upper-case regardless of how the user typed them.
  const ACRONYMS = new Set(['ac', 'dc', 'h2', 'co2', 'lng', 'ng', 'pv', 'ror'])
  const low = c.toLowerCase()
  if (ACRONYMS.has(low)) return low.toUpperCase()
  // Hyphenated / multi-word carriers: title-case each segment.
  return c
    .split(/[-_\s]+/)
    .map(seg => seg.charAt(0).toUpperCase() + seg.slice(1).toLowerCase())
    .join(' ')
}
