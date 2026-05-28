import { useMemo, useState, useCallback } from 'react'
import { Search, ChevronUp, ChevronDown } from 'lucide-react'

// ── Reusable filter+sort plumbing for result tables ─────────────────────────
// Three pieces:
//
//   1. `useFilterableTable(rows, options)` — hook that owns the sort + search
//      state, returns the visible rows and the callbacks/getters the table
//      header needs to render arrows + handle clicks.
//
//   2. `<TableSearchBox value={...} onChange={...} />` — a thin search input
//      with magnifier icon and clear button, drop-in above the table.
//
//   3. `<SortHeader>` — clickable <th> wrapper that flips the sort key /
//      direction and shows the active-sort arrow.
//
// The hook is intentionally type-flexible: rows can be any shape, and the
// caller passes a `getSearchText(row)` to control which fields participate
// in the substring match (e.g. just `name + carrier`, not the numeric
// columns). Sort uses a `getValue(row, key)` extractor so the caller can
// expose nested values (`row.cycles.total`) without flattening.

export type SortDirection = 'asc' | 'desc' | null

export interface UseFilterableTableOptions<R, K extends string> {
  rows: R[]
  // Which key to sort by initially (or null for "original order"). The hook
  // keeps the initial sort stable; clicking the same column toggles
  // asc → desc → null (back to original order) for a 3-state cycle.
  initialSortKey?: K | null
  initialSortDir?: SortDirection
  // Extract the comparable value for a (row, key) pair. Strings are
  // compared lexicographically; numbers numerically; nulls always sort
  // last regardless of direction (the "least informative" cells stay at
  // the bottom of either order).
  getValue: (row: R, key: K) => string | number | null | undefined
  // Substring-search source. Defaults to the empty string (search disabled).
  // Return a lowercased concatenation of every searchable field on the row.
  getSearchText?: (row: R) => string
}

export interface UseFilterableTableResult<R, K extends string> {
  rows: R[]                              // post-filter / post-sort
  search: string
  setSearch: (s: string) => void
  sortKey: K | null
  sortDir: SortDirection
  // Click handler factory for a column. Cycles asc → desc → null.
  // When the column wasn't active, jumps straight to asc.
  onSortClick: (key: K) => () => void
  // Convenience helper for `<SortHeader>` so it doesn't need to know the
  // active key explicitly — pass the rendered column key and it returns
  // the right arrow (or null).
  ariaSort: (key: K) => 'ascending' | 'descending' | undefined
}

export function useFilterableTable<R, K extends string>(
  options: UseFilterableTableOptions<R, K>,
): UseFilterableTableResult<R, K> {
  const { rows, getValue, getSearchText } = options
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState<K | null>(options.initialSortKey ?? null)
  const [sortDir, setSortDir] = useState<SortDirection>(options.initialSortDir ?? null)

  const onSortClick = useCallback((key: K) => () => {
    setSortKey(prev => {
      if (prev !== key) {
        setSortDir('asc')
        return key
      }
      // Same column already active — cycle through the 3 states.
      setSortDir(d => {
        if (d === 'asc') return 'desc'
        if (d === 'desc') return null
        return 'asc'
      })
      return prev
    })
  }, [])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q || !getSearchText) return rows
    return rows.filter(r => getSearchText(r).includes(q))
  }, [rows, search, getSearchText])

  const sorted = useMemo(() => {
    if (!sortKey || sortDir == null) return filtered
    // Shallow copy so the original order survives across re-renders if the
    // user later cycles back to "no sort".
    const dir = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      const va = getValue(a, sortKey)
      const vb = getValue(b, sortKey)
      // Null / undefined always last regardless of direction. Common pattern
      // when a column is "n/a" on some rows (e.g. LCOE on assets with
      // zero dispatch) — keep them out of the way at the bottom.
      const aNil = va == null || (typeof va === 'number' && !Number.isFinite(va))
      const bNil = vb == null || (typeof vb === 'number' && !Number.isFinite(vb))
      if (aNil && bNil) return 0
      if (aNil) return 1
      if (bNil) return -1
      if (typeof va === 'number' && typeof vb === 'number') {
        return (va - vb) * dir
      }
      const sa = String(va).toLowerCase()
      const sb = String(vb).toLowerCase()
      if (sa < sb) return -1 * dir
      if (sa > sb) return  1 * dir
      return 0
    })
  }, [filtered, sortKey, sortDir, getValue])

  const ariaSort = useCallback((key: K) =>
    sortKey === key && sortDir === 'asc' ? 'ascending' as const
    : sortKey === key && sortDir === 'desc' ? 'descending' as const
    : undefined, [sortKey, sortDir])

  return { rows: sorted, search, setSearch, sortKey, sortDir, onSortClick, ariaSort }
}

// ── Search box (drop in above the table) ────────────────────────────────────

export function TableSearchBox(props: {
  value: string
  onChange: (s: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <div className={`relative inline-flex items-center ${props.className ?? ''}`}>
      <Search size={12} className="absolute left-2 text-muted pointer-events-none" />
      <input
        type="search"
        value={props.value}
        onChange={e => props.onChange(e.target.value)}
        placeholder={props.placeholder ?? 'Filter…'}
        className="pl-7 pr-2 py-1 border border-border rounded text-[11px] font-mono bg-bg focus:outline-none focus:border-accent w-44"
      />
    </div>
  )
}

// ── Clickable sort header ──────────────────────────────────────────────────

export function SortHeader<K extends string>(props: {
  columnKey: K
  label: React.ReactNode
  sortKey: K | null
  sortDir: SortDirection
  onClick: () => void
  align?: 'left' | 'right' | 'center'
  className?: string
  title?: string
}) {
  const active = props.sortKey === props.columnKey && props.sortDir != null
  const arrow = active
    ? (props.sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)
    : null
  const alignClass = props.align === 'right'  ? 'text-right'
                   : props.align === 'center' ? 'text-center'
                   : 'text-left'
  return (
    <th
      className={`${alignClass} py-1 font-medium select-none cursor-pointer hover:text-text transition-colors ${props.className ?? ''}`}
      onClick={props.onClick}
      title={props.title ?? 'Click to sort'}
      aria-sort={
        active && props.sortDir === 'asc' ? 'ascending'
        : active && props.sortDir === 'desc' ? 'descending'
        : 'none'
      }
    >
      <span className="inline-flex items-center gap-1">
        {props.label}
        {arrow}
      </span>
    </th>
  )
}
