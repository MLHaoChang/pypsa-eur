import { useState, useCallback, useRef, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, Terminal, Trash2, History, Columns3, X, Search, ExternalLink } from 'lucide-react'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { networkApi } from '../api/network'
import { changelogApi, type ChangeLogEntry } from '../api/changelog'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { coerceForColumn } from '../utils/coerce'
import { useSimulationStore } from '../store/simulationStore'

// ── Constants ─────────────────────────────────────────────────────────────────

const HEIGHT_KEY = 'network-diagram:bottom-panel-height'
const DEFAULT_H  = 220
const MIN_H      = 36
const MAX_H_FRAC = 0.55

function storedH() {
  try { return parseInt(localStorage.getItem(HEIGHT_KEY) ?? '') || DEFAULT_H } catch { return DEFAULT_H }
}

const TABS = ['Log', 'History', 'Buses', 'Lines', 'Transformers', 'Generators', 'Storage', 'Stores', 'Loads', 'Links', 'Carriers'] as const
type Tab = typeof TABS[number]

const TAB_COLUMNS: Record<Tab, string[]> = {
  Log:          [],
  History:      [],
  Buses:        ['name', 'v_nom', 'carrier', 'control', 'x', 'y', 'sub_network'],
  Lines:        ['name', 'bus0', 'bus1', 'r', 'x', 's_nom', 'length', 'carrier'],
  Transformers: ['name', 'bus0', 'bus1', 'v_nom_0', 'v_nom_1', 's_nom', 'x', 'tap_ratio', 'type'],
  Generators:   ['name', 'bus', 'carrier', 'p_nom', 'p_nom_extendable', 'marginal_cost', 'capital_cost'],
  Storage:      ['name', 'bus', 'carrier', 'p_nom', 'max_hours', 'efficiency_store', 'efficiency_dispatch'],
  // Stores are pure energy reservoirs (no power-rating concept). Default
  // visible columns mirror the LOPF-relevant fields a user typically tunes.
  Stores:       ['name', 'bus', 'carrier', 'e_nom', 'e_nom_extendable', 'e_initial', 'e_cyclic', 'standing_loss', 'capital_cost'],
  Loads:        ['name', 'bus', 'carrier', 'p_set', 'q_set'],
  Links:        ['name', 'bus0', 'bus1', 'carrier', 'p_nom', 'efficiency', 'marginal_cost'],
  // Carrier rows live in n.carriers — the network-wide intensity table
  // that feeds the primary_energy global constraint (CO2 caps). The bulk
  // editor here is the canonical place to set per-fuel co2_emissions.
  Carriers:     ['name', 'co2_emissions', 'nice_name', 'color', 'unit'],
}

const COL_LABELS: Record<string, string> = {
  v_nom: 'V nom (kV)', s_nom: 'S nom (MVA)', p_nom: 'P nom (MW)',
  v_nom_0: 'V₀ (kV)', v_nom_1: 'V₁ (kV)', tap_ratio: 'Tap',
  p_set: 'P set (MW)', q_set: 'Q set (MVAr)', max_hours: 'Max hrs',
  efficiency_store: 'η store', efficiency_dispatch: 'η disp',
  p_nom_extendable: 'Extendable', marginal_cost: 'MC ($/MWh)', capital_cost: 'CC ($/MW)',
  sub_network: 'Sub-net',
  // Store-specific. e_nom is energy capacity (MWh); e_initial seeds SoC at t=0;
  // e_cyclic (bool) ties initial SoC = final SoC for cyclic operation.
  e_nom: 'E nom (MWh)', e_nom_extendable: 'Extendable', e_initial: 'E init (MWh)',
  e_cyclic: 'Cyclic', standing_loss: 'Stand. loss',
  // Carrier columns
  co2_emissions: 'CO₂ (t/MWh primary)', nice_name: 'Display name',
}

const TAB_TYPES: Record<Tab, string | null> = {
  Log: null, History: null, Buses: 'Bus', Lines: 'Line', Transformers: 'Transformer',
  Generators: 'Generator', Storage: 'StorageUnit', Stores: 'Store',
  Loads: 'Load', Links: 'Link', Carriers: 'Carrier',
}

// Component class → TanStack-Query queryKey used by the asset tables in
// PropertiesPanel + the canvas. Bulk-update mutations need this so they can
// invalidate ONLY the table they just edited instead of nuking the whole
// query cache (P1 perf fix).
const TAB_TO_API_KEY: Record<string, string> = {
  Bus: 'buses', Line: 'lines', Transformer: 'transformers',
  Generator: 'generators', StorageUnit: 'storage_units', Store: 'stores',
  Load: 'loads', Link: 'links', Carrier: 'carriers',
}

// ── Per-tab column visibility (persisted) ────────────────────────────────────

const COL_VIS_KEY = (tab: string) => `bottompanel:cols:${tab}`

function loadVisible(tab: string, fallback: string[]): Set<string> {
  try {
    const raw = localStorage.getItem(COL_VIS_KEY(tab))
    if (!raw) return new Set(fallback)
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return new Set(fallback)
    return new Set(arr.filter(x => typeof x === 'string'))
  } catch { return new Set(fallback) }
}

function saveVisible(tab: string, cols: Set<string>) {
  try { localStorage.setItem(COL_VIS_KEY(tab), JSON.stringify([...cols])) }
  catch { /* quota: ignore */ }
}

// ── AssetTable ───────────────────────────────────────────────────────────────
// Rich table for the asset tabs (Buses / Lines / Generators / …). Adds three
// things over the original SimpleTable:
//   1. Dynamic columns — every key in the data is selectable via the
//      "Columns" dropdown. Default visible set is the curated list in
//      TAB_COLUMNS; user picks are persisted per tab in localStorage.
//   2. Multi-row selection — click checkbox or shift-click range, click a
//      row body to open the right-side properties panel as before.
//   3. Bulk-edit toolbar — appears when ≥1 rows selected. Pick a column,
//      type a value, Apply → one PATCH /network/_bulk request.

interface AssetTableProps {
  tab: string                         // for localStorage scoping
  componentClass: string              // for bulkUpdate (e.g. "Generator")
  data: Record<string, unknown>[]
  defaultColumns: string[]            // curated initial visible set
  selectedName?: string | null
  onRowClick: (row: Record<string, unknown>) => void
}

// Coerce a free-text value to the type of the column's first non-null sample.
// Handles the common cases: numeric fields, booleans, strings. NaN out → null
// so the user typing nothing applies an intentional "unset" rather than a
// silent zero.
//
// coerceForColumn moved to utils/coerce.ts so it can be unit-tested without
// mounting this panel. Imported at the top of the file.

function AssetTable({
  tab, componentClass, data, defaultColumns, selectedName, onRowClick,
}: AssetTableProps) {
  const qc = useQueryClient()
  const [sortCol, setSortCol] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})

  // Available columns: union of every key seen in data, ordered with `name`
  // first, the curated defaults next (in their declared order), then everything
  // else alphabetically. This stays stable across data refetches.
  const availableCols = useMemo(() => {
    const seen = new Set<string>()
    for (const row of data) for (const k of Object.keys(row)) seen.add(k)
    const front = ['name']
    const curated = defaultColumns.filter(c => c !== 'name' && seen.has(c))
    const rest = [...seen].filter(c => c !== 'name' && !defaultColumns.includes(c)).sort()
    return [...front, ...curated, ...rest]
  }, [data, defaultColumns])

  const [visible, setVisible] = useState<Set<string>>(() => loadVisible(tab, defaultColumns))
  // Hydrate when switching tabs (the component is reused across tabs).
  useEffect(() => { setVisible(loadVisible(tab, defaultColumns)) }, [tab, defaultColumns])

  const visibleCols = useMemo(
    () => availableCols.filter(c => visible.has(c)),
    [availableCols, visible],
  )
  const toggleCol = (col: string) => {
    setVisible(prev => {
      const next = new Set(prev)
      if (next.has(col)) next.delete(col); else next.add(col)
      saveVisible(tab, next)
      return next
    })
  }
  const showAll = () => { const all = new Set(availableCols); setVisible(all); saveVisible(tab, all) }
  const showDefaults = () => {
    const def = new Set(defaultColumns); setVisible(def); saveVisible(tab, def)
  }

  // Multi-row selection. Reset whenever the tab changes — a Bus selection has
  // no meaning on the Lines tab, and persisting across tab switches is more
  // confusing than helpful.
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set())
  useEffect(() => { setSelectedRows(new Set()) }, [tab])
  const lastClickedIdxRef = useRef<number | null>(null)

  // Search: substring match on row.name. Filter happens BEFORE sort so the
  // user sees the same ordering as the unfiltered table — sort is purely
  // visual and shouldn't surprise the search workflow.
  const [search, setSearch] = useState('')
  // Reset the query whenever the tab changes — a search for "BE0 0 CCGT" on
  // the Generators tab has no meaning on the Lines tab.
  useEffect(() => { setSearch('') }, [tab])
  const searchInputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return data
    return data.filter(r => {
      const n = r.name
      return typeof n === 'string' && n.toLowerCase().includes(q)
    })
  }, [data, search])

  const sorted = useMemo(() => {
    if (!sortCol) return filtered
    return [...filtered].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true })
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [filtered, sortCol, sortDir])

  // Render-cap. The full HTML <table> would otherwise render 100k+ nodes on
  // PyPSA-Eur-scale networks (~5000 components × ~15 columns × 2 inner spans).
  // Selected row stays in view if the user already had a row selected.
  // Cap is high enough (1000) that all-but-the-biggest networks render in
  // full; above it, search/sort are the canonical drill-down path.
  const RENDER_CAP = 1000
  const displayed = useMemo(() => {
    if (sorted.length <= RENDER_CAP) return sorted
    // Always include the currently-selected row if there is one, so the
    // properties panel doesn't desync from the table.
    if (selectedName) {
      const selIdx = sorted.findIndex(r => r.name === selectedName)
      if (selIdx >= RENDER_CAP) {
        return [...sorted.slice(0, RENDER_CAP - 1), sorted[selIdx]]
      }
    }
    return sorted.slice(0, RENDER_CAP)
  }, [sorted, selectedName])
  const truncated = sorted.length > RENDER_CAP

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  const fmt = (v: unknown): string => {
    if (v == null) return '–'
    if (typeof v === 'boolean') return v ? '✓' : '–'
    if (typeof v === 'number') {
      if (!isFinite(v)) return '–'
      return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/\.?0+$/, '')
    }
    return String(v)
  }

  const toggleRow = (row: Record<string, unknown>, idx: number, ev: React.MouseEvent) => {
    const name = row.name as string
    if (!name) return
    setSelectedRows(prev => {
      const next = new Set(prev)
      if (ev.shiftKey && lastClickedIdxRef.current !== null) {
        // Range select: include every row between the last anchor and this one.
        const lo = Math.min(lastClickedIdxRef.current, idx)
        const hi = Math.max(lastClickedIdxRef.current, idx)
        for (let i = lo; i <= hi; i++) {
          const n = sorted[i]?.name as string | undefined
          if (n) next.add(n)
        }
      } else {
        if (next.has(name)) next.delete(name); else next.add(name)
      }
      return next
    })
    lastClickedIdxRef.current = idx
  }

  const allSelected = sorted.length > 0 && selectedRows.size === sorted.length
  const toggleAll = () => {
    if (allSelected) { setSelectedRows(new Set()); return }
    setSelectedRows(new Set(sorted.map(r => r.name as string).filter(Boolean)))
  }

  // Bulk edit
  const [showColMenu, setShowColMenu] = useState(false)
  const [editCol, setEditCol] = useState<string>('')
  const [editValue, setEditValue] = useState<string>('')
  // Reset bulk-edit fields whenever selection changes — re-asking for the
  // column + value avoids the "wait, what was I editing?" footgun.
  useEffect(() => { setEditCol(''); setEditValue('') }, [selectedRows])

  const bulkMut = useMutation({
    mutationFn: (body: { component_class: string; names: string[]; updates: Record<string, unknown> }) =>
      networkApi.bulkUpdate(body),
    onSuccess: (r) => {
      // SCOPED invalidation. The previous `qc.invalidateQueries()` (no key)
      // wiped EVERY cached query, triggering ~15 refetches (carriers, profiles,
      // ac_pf_status, snapshots, etc.) per bulk apply. On large networks this
      // was the dominant source of perceived lag. We only need:
      //  • the table for the component class we edited
      //  • the audit-trail surfaces (undoInfo, changelog)
      //  • result queries (they reference component data via bus/name joins)
      // Other caches (carriers, snapshots, ac_pf_status) are untouched by a
      // bulk attribute write, so leave them alone.
      const tableKey = TAB_TO_API_KEY[componentClass] ?? componentClass.toLowerCase() + 's'
      qc.invalidateQueries({ queryKey: [tableKey] })
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'undoInfo') })
      qc.invalidateQueries({ queryKey: ['changelog'] })
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'results') })
      toast.success(`Updated ${r.updated} ${componentClass.toLowerCase()}(s)`)
      setSelectedRows(new Set())
      setEditCol(''); setEditValue('')
    },
    onError: (e: { response?: { data?: { detail?: string } } }) => {
      const detail = e.response?.data?.detail ?? 'Bulk update failed'
      toast.error(detail)
    },
  })

  const onApply = () => {
    if (!editCol) { toast.error('Pick a column first'); return }
    if (selectedRows.size === 0) { toast.error('No rows selected'); return }
    // Use the first non-null sample to type-coerce the value
    const sample = data.find(r => r[editCol] != null)?.[editCol]
    const value = coerceForColumn(editValue, sample)
    confirmToast(
      `Set ${editCol} = ${value === null ? '(unset)' : JSON.stringify(value)} on ${selectedRows.size} ${componentClass.toLowerCase()}(s)?`,
      () => bulkMut.mutate({
        component_class: componentClass,
        names: [...selectedRows],
        updates: { [editCol]: value },
      }),
      { confirmLabel: 'Apply' },
    )
  }

  useEffect(() => {
    if (!selectedName) return
    rowRefs.current[selectedName]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedName])

  // When the search query has a top match, scroll it into view. This is
  // separate from the selectedName effect so typing keeps re-anchoring on
  // the first match without needing the user to click first.
  useEffect(() => {
    if (!search.trim()) return
    const top = sorted[0]
    if (top?.name) {
      rowRefs.current[top.name as string]?.scrollIntoView({
        block: 'nearest', behavior: 'smooth',
      })
    }
  }, [search, sorted])

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { setSearch(''); searchInputRef.current?.blur(); return }
    if (e.key === 'Enter') {
      // Enter = select the top match. With one match this auto-targets the
      // user's intent; with multiple, it picks the first sorted result so
      // pressing Enter on a partial query still does something useful.
      const top = sorted[0]
      if (top) onRowClick(top)
    }
  }

  if (data.length === 0) {
    return <div className="flex items-center justify-center h-16 text-muted text-xs">No data</div>
  }

  // Column-visibility dropdown closes when the user clicks anywhere outside
  // it. We rely on a one-time pointerdown listener instead of mousedown
  // bubbling so the menu's own clicks don't dismiss it.
  const closeColMenu = () => setShowColMenu(false)

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar: column toggle (always) + bulk edit (when rows selected) */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-border/40 bg-panel shrink-0 text-[11px]">
        <div className="relative">
          <button
            onClick={() => setShowColMenu(s => !s)}
            className="flex items-center gap-1 px-2 py-1 border border-border rounded text-muted hover:text-text hover:border-text/40"
            title="Show / hide columns"
          >
            <Columns3 size={11} />
            Columns
            <span className="text-[10px] opacity-70">({visibleCols.length}/{availableCols.length})</span>
          </button>
          {showColMenu && (
            <>
              {/* dismiss layer */}
              <div className="fixed inset-0 z-[100]" onClick={closeColMenu} />
              <div className="absolute z-[101] left-0 top-full mt-1 w-60 max-h-72 overflow-y-auto bg-bg border border-border rounded shadow-lg p-1.5">
                <div className="flex items-center justify-between px-1 py-1 border-b border-border/40 mb-1">
                  <span className="text-[10px] text-muted">VISIBLE COLUMNS</span>
                  <div className="flex gap-1">
                    <button onClick={showDefaults} className="text-[10px] text-muted hover:text-accent">defaults</button>
                    <span className="text-muted/40">·</span>
                    <button onClick={showAll}      className="text-[10px] text-muted hover:text-accent">all</button>
                  </div>
                </div>
                {availableCols.map(col => (
                  <label key={col} className="flex items-center gap-2 px-1 py-0.5 hover:bg-panel rounded cursor-pointer">
                    <input
                      type="checkbox" checked={visible.has(col)}
                      onChange={() => toggleCol(col)}
                      disabled={col === 'name'}  // anchor column — never hidden
                    />
                    <span className="font-mono text-[11px]">{col}</span>
                    {COL_LABELS[col] && <span className="text-[10px] text-muted">— {COL_LABELS[col]}</span>}
                  </label>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Search input — sits next to Columns so it's always reachable.
            Filters by row.name (substring, case-insensitive). Enter selects
            the top match; Escape clears the field. */}
        <div className="flex items-center gap-1 relative">
          <Search size={11} className="absolute left-1.5 text-muted pointer-events-none" />
          <input
            ref={searchInputRef}
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={onSearchKey}
            placeholder="Search by name…"
            className="pl-5 pr-5 py-1 border border-border rounded text-[11px] bg-bg w-44 font-mono focus:outline-none focus:border-accent"
          />
          {search && (
            <button
              onClick={() => { setSearch(''); searchInputRef.current?.focus() }}
              className="absolute right-1 text-muted hover:text-danger"
              title="Clear search"
            ><X size={10} /></button>
          )}
          {search && (
            <span className="text-[10px] text-muted ml-1">
              {sorted.length} {sorted.length === 1 ? 'match' : 'matches'}
            </span>
          )}
          {/* Render-cap notice. The table caps display at RENDER_CAP rows to
              keep the DOM tractable on PyPSA-Eur-scale networks; this lets
              the user know to use search/sort to drill down. */}
          {truncated && (
            <span className="text-[10px] text-warn ml-2 font-medium" title="Search to narrow down — the table caps at 1000 rendered rows for performance.">
              Showing {displayed.length} of {sorted.length} — use search/sort to drill down
            </span>
          )}
        </div>

        {selectedRows.size > 0 ? (
          <>
            <span className="text-muted">·</span>
            <span className="font-medium text-text">{selectedRows.size} selected</span>
            <span className="text-muted">·</span>
            <span className="text-muted">Set</span>
            <select
              value={editCol}
              onChange={e => setEditCol(e.target.value)}
              className="px-1.5 py-0.5 border border-border rounded bg-bg font-mono text-[11px]"
            >
              <option value="">(column)</option>
              {availableCols.filter(c => c !== 'name').map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <span className="text-muted">to</span>
            <input
              value={editValue}
              onChange={e => setEditValue(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') onApply() }}
              placeholder="value"
              className="px-1.5 py-0.5 border border-border rounded bg-bg font-mono text-[11px] w-32"
            />
            <button
              onClick={onApply}
              disabled={bulkMut.isPending || !editCol}
              className="px-2 py-0.5 bg-accent text-white rounded text-[11px] font-medium hover:bg-accent/90 disabled:opacity-40"
            >{bulkMut.isPending ? 'Applying…' : 'Apply'}</button>
            <button
              onClick={() => setSelectedRows(new Set())}
              className="text-muted hover:text-danger flex items-center gap-0.5"
              title="Clear selection"
            ><X size={11} /></button>
          </>
        ) : (
          <span className="text-muted">·  Click checkboxes to select rows for bulk edit (shift-click for range).</span>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-xs border-collapse" style={{ minWidth: 'max-content' }}>
          <thead>
            <tr className="sticky top-0 bg-panel z-10 border-b border-border">
              <th className="w-7 px-1 py-1.5 sticky left-0 bg-panel z-20">
                <input type="checkbox" checked={allSelected}
                       onChange={toggleAll}
                       title={allSelected ? 'Deselect all' : 'Select all (filtered)'} />
              </th>
              {visibleCols.map(col => (
                <th
                  key={col}
                  onClick={() => handleSort(col)}
                  className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide cursor-pointer hover:text-text whitespace-nowrap select-none"
                >
                  {COL_LABELS[col] ?? col}
                  {sortCol === col && (
                    <span className="ml-0.5 text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
              <th className="w-7 px-1 py-1.5" />
            </tr>
          </thead>
          <tbody>
            {displayed.map((row, i) => {
              const name = row.name as string
              const isSelected = selectedName === name
              const isChecked = selectedRows.has(name)
              return (
                <tr
                  key={name ?? i}
                  ref={el => { rowRefs.current[name] = el }}
                  onClick={() => onRowClick(row)}
                  className={`border-b border-border/40 cursor-pointer transition-colors
                    ${isSelected
                      ? 'bg-accent/10 hover:bg-accent/15'
                      : isChecked
                      ? 'bg-warn/5 hover:bg-warn/10'
                      : i % 2 === 0
                      ? 'bg-bg hover:bg-accent/5'
                      : 'bg-panel hover:bg-accent/5'}`}
                >
                  <td className={`w-7 px-1 sticky left-0
                    ${isSelected ? 'bg-accent/10' : isChecked ? 'bg-warn/5' : i % 2 === 0 ? 'bg-bg' : 'bg-panel'}`}
                      style={{ paddingBlock: 'var(--row-padding-y)' }}
                      onClick={e => { e.stopPropagation(); toggleRow(row, i, e) }}
                  >
                    <input type="checkbox" checked={isChecked} onChange={() => { /* handled by td onClick */ }} />
                  </td>
                  {visibleCols.map(col => (
                    <td
                      key={col}
                      className={`px-2 font-mono whitespace-nowrap text-[11px] ${isSelected ? 'text-text' : 'text-text'}`}
                      style={{ paddingBlock: 'var(--row-padding-y)' }}
                    >
                      {fmt(row[col])}
                    </td>
                  ))}
                  <td className="w-7 px-1 whitespace-nowrap" style={{ paddingBlock: 'var(--row-padding-y)' }}>
                    <button
                      onClick={e => {
                        e.stopPropagation()
                        useUIStore.getState().requestAssetDetail({ componentClass, name })
                      }}
                      title="View this asset's results"
                      className="text-muted hover:text-accent"
                    ><ExternalLink size={11} /></button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SimpleTable (legacy, retained for any future read-only callers) ──────────

function SimpleTable({
  columns, data, selectedName, onRowClick,
}: {
  columns: string[]
  data: Record<string, unknown>[]
  selectedName?: string | null
  onRowClick: (row: Record<string, unknown>) => void
}) {
  const [sortCol, setSortCol] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({})

  useEffect(() => {
    if (!selectedName) return
    rowRefs.current[selectedName]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedName])

  const sorted = useMemo(() => {
    if (!sortCol) return data
    return [...data].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true })
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [data, sortCol, sortDir])

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('asc') }
  }

  const fmt = (v: unknown): string => {
    if (v == null) return '–'
    if (typeof v === 'boolean') return v ? '✓' : '–'
    if (typeof v === 'number') {
      if (!isFinite(v)) return '–'
      return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/\.?0+$/, '')
    }
    return String(v)
  }

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-16 text-muted text-xs">
        No data
      </div>
    )
  }

  return (
    <table className="w-full text-xs border-collapse" style={{ minWidth: 'max-content' }}>
      <thead>
        <tr className="sticky top-0 bg-panel z-10 border-b border-border">
          {columns.map(col => (
            <th
              key={col}
              onClick={() => handleSort(col)}
              className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide cursor-pointer hover:text-text whitespace-nowrap select-none"
            >
              {COL_LABELS[col] ?? col}
              {sortCol === col && (
                <span className="ml-0.5 text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>
              )}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row, i) => {
          const name = row.name as string
          const isSelected = selectedName === name
          return (
            <tr
              key={name ?? i}
              ref={el => { rowRefs.current[name] = el }}
              onClick={() => onRowClick(row)}
              className={`border-b border-border/40 cursor-pointer transition-colors
                ${isSelected
                  ? 'bg-accent/10 hover:bg-accent/15'
                  : i % 2 === 0
                  ? 'bg-bg hover:bg-accent/5'
                  : 'bg-panel hover:bg-accent/5'}`}
            >
              {columns.map(col => (
                <td
                  key={col}
                  className={`px-2 font-mono whitespace-nowrap text-[11px] ${isSelected ? 'text-text' : 'text-text'}`}
                  style={{ paddingBlock: 'var(--row-padding-y)' }}
                >
                  {fmt(row[col])}
                </td>
              ))}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── SolverLog ─────────────────────────────────────────────────────────────────

function lineClass(line: string): string {
  // Phase markers (lifecycle: Loading → Validation → Optimising → Storing →
  // Summary). Three-state colouring so the user can scan run health at a
  // glance without reading every line:
  //   • red    — terminal failure (Failed / Aborted / Validation failed)
  //   • green  — successful completion (Solve complete, Validation passed, Summary)
  //   • blue   — neutral / in-progress (Loading, Applying, Storing, …)
  // Match red before green so "Validation failed" doesn't accidentally hit
  // the "passed" pattern.
  if (line.startsWith('[PHASE]')) {
    const body = line.slice(7).toLowerCase()  // drop the "[PHASE]" prefix
    if (/(^|\s)(failed|aborted|error)/.test(body)) return 'text-danger font-semibold'
    if (/(solve complete|passed|summary|optimal|completed)/.test(body)) return 'text-success font-semibold'
    return 'text-[#58a6ff] font-semibold'
  }
  if (line.startsWith('[VALIDATION]')) {
    if (line.includes('ERROR')) return 'text-danger'
    if (line.includes('WARN'))  return 'text-warn'
    return 'text-[#58a6ff]'
  }
  // Numerical-conditioning diagnostics ([NUMERICS] …): amber when the objective
  // is flagged ill-conditioned, neutral blue for the informational report.
  if (line.startsWith('[NUMERICS]')) {
    return line.includes('WARN') ? 'text-warn' : 'text-[#58a6ff]'
  }
  // Infeasibility "why" diagnostics — red, they explain a failed solve.
  if (line.startsWith('[INFEASIBLE]')) return 'text-danger'
  // Binding global-constraint shadow price (e.g. CO2 cap carbon price) — amber.
  if (line.startsWith('[GLOBAL-CONSTRAINT]')) return 'text-warn'
  // App-level entries: "[HH:MM:SS] ERROR …" / "[HH:MM:SS] WARN …" / "[HH:MM:SS] INFO …"
  if (/^\[\d{2}:\d{2}:\d{2}\] ERROR/.test(line)) return 'text-danger'
  if (/^\[\d{2}:\d{2}:\d{2}\] WARN/.test(line))  return 'text-warn'
  if (/^\[\d{2}:\d{2}:\d{2}\] INFO/.test(line))  return 'text-[#58a6ff]'
  // Solver / library log lines (pass-through from SSE)
  if (line.startsWith('ERROR') || line.includes(':ERROR:') || line.includes(' ERROR ')) return 'text-danger'
  if (line.startsWith('WARNING') || line.includes(':WARNING:') || line.includes(' WARN ')) return 'text-warn'
  return ''
}

function SolverLog() {
  const { logLines, clearLog } = useSimulationStore()
  const bottomRef  = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const userScrolledRef = useRef(false)

  // Auto-scroll to bottom unless user has scrolled up
  useEffect(() => {
    const el = containerRef.current
    if (!el || userScrolledRef.current) return
    el.scrollTop = el.scrollHeight
  }, [logLines])

  const onScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
    userScrolledRef.current = !atBottom
  }, [])

  const scrollToBottom = useCallback(() => {
    userScrolledRef.current = false
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* toolbar */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-border/40 shrink-0 bg-panel">
        <span className="text-[10px] text-muted font-mono">{logLines.length} lines</span>
        <div className="flex-1" />
        {userScrolledRef.current && (
          <button
            onClick={scrollToBottom}
            className="text-[10px] text-accent hover:underline"
          >↓ scroll to bottom</button>
        )}
        <button
          onClick={clearLog}
          className="flex items-center gap-1 text-[10px] text-muted hover:text-danger transition-colors"
          title="Clear log"
        >
          <Trash2 size={10} /> Clear
        </button>
      </div>

      {/* log body */}
      <div
        ref={containerRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto p-2 font-mono text-[11px] text-muted"
        id="log-terminal"
      >
        {logLines.length === 0
          ? <p className="italic text-xs text-muted/60 p-1">
              No output yet. Errors, API calls, and solver output appear here.
            </p>
          : logLines.map((line, i) => (
              <div key={i} className={`leading-[1.6] whitespace-pre-wrap break-all ${lineClass(line)}`}>
                {line}
              </div>
            ))
        }
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ── ChangeHistory ─────────────────────────────────────────────────────────────

const ACTION_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  add:                  { bg: 'bg-green-100',   text: 'text-green-700',   label: 'ADD' },
  update:               { bg: 'bg-blue-100',    text: 'text-blue-700',    label: 'EDIT' },
  delete:               { bg: 'bg-red-100',     text: 'text-red-700',     label: 'DEL' },
  undo:                 { bg: 'bg-amber-100',   text: 'text-amber-700',   label: 'UNDO' },
  import:               { bg: 'bg-purple-100',  text: 'text-purple-700',  label: 'IMP' },
  export:               { bg: 'bg-violet-100',  text: 'text-violet-700',  label: 'EXP' },
  save:                 { bg: 'bg-teal-100',    text: 'text-teal-700',    label: 'SAVE' },
  load:                 { bg: 'bg-cyan-100',    text: 'text-cyan-700',    label: 'LOAD' },
  timeseries:           { bg: 'bg-indigo-100',  text: 'text-indigo-700',  label: 'TS' },
  // Phase 8 scenario-tree verbs.
  scenario_create:      { bg: 'bg-fuchsia-100', text: 'text-fuchsia-700', label: 'SCEN' },
  delete_project:       { bg: 'bg-red-100',     text: 'text-red-800',     label: 'DEL-PROJ' },
  // Phase 6 dispatch invalidation + Phase 2 snapshot ops were already emitted
  // by the backend; styling them here so they don't fall through to the grey
  // catch-all pill.
  dispatch_invalidated: { bg: 'bg-yellow-100',  text: 'text-yellow-700',  label: 'INVAL' },
  snapshot:             { bg: 'bg-sky-100',     text: 'text-sky-700',     label: 'SNAP' },
  restore:              { bg: 'bg-emerald-100', text: 'text-emerald-700', label: 'REST' },
  delete_snapshot:      { bg: 'bg-rose-100',    text: 'text-rose-700',    label: 'SNAP-DEL' },
  cluster:              { bg: 'bg-orange-100',  text: 'text-orange-700',  label: 'CLST' },
  warn:                 { bg: 'bg-yellow-100',  text: 'text-yellow-800',  label: 'WARN' },
}

function ChangeHistory() {
  const { data: entries = [], refetch } = useQuery({
    queryKey: ['changelog'],
    queryFn: changelogApi.getAll,
    refetchInterval: 5000,
  })

  const clearMut = useMutation({
    mutationFn: changelogApi.clear,
    onSuccess: () => refetch(),
  })

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* toolbar */}
      <div className="flex items-center gap-2 px-2 py-1 border-b border-border/40 shrink-0 bg-panel">
        <span className="text-[10px] text-muted font-mono">{entries.length} entries</span>
        <div className="flex-1" />
        <button
          onClick={() => clearMut.mutate()}
          disabled={clearMut.isPending || entries.length === 0}
          className="flex items-center gap-1 text-[10px] text-muted hover:text-danger transition-colors disabled:opacity-40"
          title="Clear history"
        >
          <Trash2 size={10} /> Clear
        </button>
      </div>

      {/* log body */}
      <div className="flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <p className="italic text-xs text-muted/60 p-3">No changes recorded yet.</p>
        ) : (
          <table className="w-full text-xs border-collapse" style={{ minWidth: 'max-content' }}>
            <thead>
              <tr className="sticky top-0 bg-panel z-10 border-b border-border">
                <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap">Time</th>
                <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap">Action</th>
                <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap">Type</th>
                <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap">Name</th>
                <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide">Description</th>
              </tr>
            </thead>
            <tbody>
              {(entries as ChangeLogEntry[]).map((entry, i) => {
                const s = ACTION_STYLES[entry.action] ?? { bg: 'bg-gray-100', text: 'text-gray-600', label: entry.action.toUpperCase() }
                const ts = new Date(entry.timestamp)
                const isToday = ts.toDateString() === new Date().toDateString()
                const localTime = ts.toLocaleTimeString([], {
                  hour: '2-digit', minute: '2-digit', second: '2-digit',
                })
                // Older-than-today entries lead with a short date (Jun 12) so a
                // multi-day session is readable at a glance.
                const dateLabel = isToday ? '' : ts.toLocaleDateString([], { month: 'short', day: 'numeric' })
                const fullStamp = ts.toLocaleString()  // tooltip shows full date + time
                return (
                  <tr
                    key={entry.id}
                    className={`border-b border-border/40 ${i % 2 === 0 ? 'bg-bg' : 'bg-panel'}`}
                  >
                    <td
                      className="px-2 py-1 font-mono text-[10px] text-muted whitespace-nowrap"
                      title={fullStamp}
                    >
                      {dateLabel ? <span className="mr-1 text-muted/70">{dateLabel}</span> : null}
                      {localTime}
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap">
                      <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${s.bg} ${s.text}`}>
                        {s.label}
                      </span>
                    </td>
                    <td className="px-2 py-1 text-[10px] text-muted whitespace-nowrap">{entry.component_type || '—'}</td>
                    <td className="px-2 py-1 font-mono text-[10px] whitespace-nowrap max-w-[140px] truncate" title={entry.name}>{entry.name || '—'}</td>
                    <td className="px-2 py-1 text-[11px] text-text">{entry.description}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── BottomPanel ───────────────────────────────────────────────────────────────

export default function BottomPanel() {
  const [height, setHeight]   = useState(storedH)
  const [collapsed, setCollapsed] = useState(false)
  const [activeTab, setActiveTab] = useState<Tab>('Buses')
  const dragRef = useRef<{ startY: number; startH: number } | null>(null)

  const { selectedComponent, setSelectedComponent, openRightPanel, setHighlightedComponent, bottomTabRequest, clearBottomTabRequest, currentProject } = useUIStore()

  useEffect(() => {
    if (!bottomTabRequest) return
    const tab = bottomTabRequest as Tab
    if (TABS.includes(tab)) {
      setActiveTab(tab)
      setCollapsed(false)
      const h = parseInt(localStorage.getItem(HEIGHT_KEY) ?? '') || DEFAULT_H
      setHeight(h > MIN_H ? h : DEFAULT_H)
    }
    clearBottomTabRequest()
  }, [bottomTabRequest, clearBottomTabRequest])

  const { data: buses        = [] } = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses })
  const { data: lines        = [] } = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: links        = [] } = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'),  queryFn: networkApi.getTransformers })
  const { data: generators   = [] } = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: loads        = [] } = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: sus          = [] } = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores       = [] } = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })
  const { data: carriers     = [] } = useQuery({ queryKey: nk(currentProject, 'carriers'),      queryFn: networkApi.getCarriers })

  // Component rows carry no string index signature, so TS rejects a direct
  // cast to Record<string, unknown>[]; route through `unknown` (the table
  // renderer only ever reads columns by string key, so this is sound).
  const tableData: Record<Tab, Record<string, unknown>[]> = {
    Log:          [],
    History:      [],
    Buses:        buses        as unknown as Record<string, unknown>[],
    Lines:        lines        as unknown as Record<string, unknown>[],
    Transformers: transformers as unknown as Record<string, unknown>[],
    Generators:   generators   as unknown as Record<string, unknown>[],
    Storage:      sus          as unknown as Record<string, unknown>[],
    Stores:       stores       as unknown as Record<string, unknown>[],
    Loads:        loads        as unknown as Record<string, unknown>[],
    Links:        links        as unknown as Record<string, unknown>[],
    Carriers:     carriers     as unknown as Record<string, unknown>[],
  }

  const onHandleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { startY: e.clientY, startH: height }
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return
      const delta = dragRef.current.startY - e.clientY  // drag up = grow panel
      const newH = Math.max(MIN_H, Math.min(window.innerHeight * MAX_H_FRAC, dragRef.current.startH + delta))
      setHeight(newH)
      if (newH > MIN_H + 20) setCollapsed(false)
      localStorage.setItem(HEIGHT_KEY, String(Math.round(newH)))
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [height])

  const toggleCollapse = useCallback(() => {
    if (collapsed) {
      setCollapsed(false)
      const h = parseInt(localStorage.getItem(HEIGHT_KEY) ?? '') || DEFAULT_H
      setHeight(h > MIN_H ? h : DEFAULT_H)
    } else {
      setCollapsed(true)
    }
  }, [collapsed])

  const panelH = collapsed ? MIN_H : height
  const colType = TAB_TYPES[activeTab]
  const selectedName = selectedComponent?.type === colType ? selectedComponent.name : null

  const handleRowClick = useCallback((row: Record<string, unknown>) => {
    const name = row.name as string
    const type = TAB_TYPES[activeTab]
    // Carrier rows have no PropertiesPanel editor yet — the bulk-edit toolbar
    // and inline cell edit cover the main co2_emissions / color use cases.
    // Skip the panel open so the row-click doesn't surface an empty form.
    if (type === 'Carrier') return
    if (type && name) {
      const busName = (row.bus ?? row.bus0) as string | undefined
      setSelectedComponent({ type, name })
      setHighlightedComponent({ type, name, busName })
      openRightPanel()
    }
  }, [activeTab, setSelectedComponent, setHighlightedComponent, openRightPanel])

  return (
    <div
      data-no-panel-close
      className="flex flex-col shrink-0 border-t border-border bg-bg overflow-hidden"
      style={{ height: panelH }}
    >
      {/* Drag handle */}
      <div
        className="h-1 shrink-0 cursor-row-resize bg-border/60 hover:bg-accent/50 transition-colors"
        onMouseDown={onHandleMouseDown}
        onDoubleClick={toggleCollapse}
      />

      {/* Tab bar */}
      <div className="flex items-center shrink-0 h-7 border-b border-border bg-panel px-1 gap-0">
        {TABS.map(tab => {
          const count = (tab !== 'Log' && tab !== 'History') ? tableData[tab].length : null
          return (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); if (collapsed) setCollapsed(false) }}
              className={`h-full px-2.5 text-[11px] font-medium border-b-2 -mb-px whitespace-nowrap transition-colors flex items-center gap-1
                ${activeTab === tab && !collapsed
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-text'}`}
            >
              {tab === 'Log'     && <Terminal size={10} />}
              {tab === 'History' && <History  size={10} />}
              {tab}
              {count != null && count > 0 && (
                <span className="text-[9px] text-muted/70 font-mono">({count})</span>
              )}
            </button>
          )
        })}

        <div className="flex-1" />
        <button
          onClick={toggleCollapse}
          className="p-1 mr-1 text-muted hover:text-text transition-colors"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
      </div>

      {/* Content */}
      {!collapsed && (
        <div className="flex-1 overflow-auto">
          {activeTab === 'Log' ? (
            <SolverLog />
          ) : activeTab === 'History' ? (
            <ChangeHistory />
          ) : activeTab === 'Carriers' ? (
            <CarriersTable rows={tableData.Carriers} />
          ) : (
            <AssetTable
              tab={activeTab}
              componentClass={TAB_TYPES[activeTab] ?? ''}
              data={tableData[activeTab]}
              defaultColumns={TAB_COLUMNS[activeTab]}
              selectedName={selectedName}
              onRowClick={handleRowClick}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── CarriersTable ──────────────────────────────────────────────────────────
// Inline-editable table for `n.carriers`. Each cell is its own controlled
// input that calls PUT /api/network/carriers/{name} on blur. The generic
// AssetTable doesn't do inline edit (it relies on bulk-edit toolbar), but
// for a small N (~10-30 carriers, typically) per-cell typing is the
// natural interaction.
//
// Editable columns: co2_emissions (number), nice_name / color / unit
// (string). Name is read-only — renaming a carrier means touching every
// asset that references it, which would silently break dispatch.
interface CarriersTableProps {
  rows: Array<Record<string, unknown>>
}

function CarriersTable({ rows }: CarriersTableProps) {
  const qc = useQueryClient()
  const [draftValues, setDraftValues] = useState<Record<string, string>>({})

  const updateMut = useMutation({
    mutationFn: ({ name, body }: { name: string; body: Record<string, unknown> }) =>
      networkApi.updateCarrier(name, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'carriers') })
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'undoInfo') })
      // The carriers DataFrame is referenced by every result endpoint that
      // groups by carrier — invalidate those too so the next render sees
      // the new CO2 intensity / nice_name without a manual refresh.
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'results') })
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }) => {
      // FastAPI returns `detail: string` for HTTPException, but `detail: [
      // {type, loc, msg, input}, ...]` for Pydantic validation failures.
      // Passing the raw array to react-hot-toast crashes React because
      // it tries to render objects as JSX children. Coerce to a string
      // before display.
      const raw = e?.response?.data?.detail
      const msg = typeof raw === 'string'
        ? raw
        : Array.isArray(raw)
          // Validation array: surface every msg with its field path.
          ? raw.map((d) => {
              const r = d as { loc?: unknown[]; msg?: string }
              const field = Array.isArray(r.loc) ? r.loc.slice(1).join('.') : ''
              return field ? `${field}: ${r.msg ?? 'invalid'}` : (r.msg ?? 'invalid')
            }).join(' · ')
          : 'Failed to update carrier'
      toast.error(msg)
    },
  })

  const draftKey = (name: string, col: string) => `${name}|${col}`

  const commit = (name: string, col: string, raw: string, current: unknown) => {
    const key = draftKey(name, col)
    // Strip the draft regardless of outcome — the input falls back to the
    // backend value via the React Query cache invalidation above.
    setDraftValues(d => { const c = { ...d }; delete c[key]; return c })
    let next: unknown
    if (col === 'co2_emissions') {
      if (raw.trim() === '') { next = 0 }
      else {
        const v = Number(raw)
        if (!Number.isFinite(v) || v < 0) {
          toast.error('CO₂ emissions must be a non-negative number')
          return
        }
        next = v
      }
    } else {
      next = raw
    }
    // Skip the round-trip when the value didn't actually change. Compare as
    // strings to avoid the 0 vs 0.0 / int vs float false-positives.
    if (String(next) === String(current ?? '')) return
    // Build the full carrier object from the React Query cache so the
    // backend's remove+add cycle (in `_update_component`) doesn't reset
    // the other columns (`color`, `nice_name`, `unit`, `co2_emissions`)
    // to schema defaults. Same pattern as the PropertiesPanel cards.
    const cached = qc.getQueryData<Array<Record<string, unknown>>>(nk(useUIStore.getState().currentProject, 'carriers')) ?? []
    const row = cached.find(c => c.name === name) ?? rows.find(r => r.name === name) ?? { name }
    updateMut.mutate({ name, body: { ...row, [col]: next } })
  }

  const cellInput = (name: string, col: string, value: unknown, kind: 'number' | 'string') => {
    const key = draftKey(name, col)
    const displayValue = draftValues[key] !== undefined
      ? draftValues[key]
      : (value == null ? '' : String(value))
    return (
      <input
        // Force remount when the cached value changes so the field re-syncs
        // after a refetch (otherwise React reuses the old DOM element and
        // the typed-then-reset cycle leaks stale text — a documented
        // PyPSA-GUI footgun for uncontrolled inputs).
        key={`${key}-${value ?? ''}`}
        type={kind === 'number' ? 'number' : 'text'}
        step={kind === 'number' ? 'any' : undefined}
        min={kind === 'number' && col === 'co2_emissions' ? 0 : undefined}
        value={displayValue}
        onChange={e => setDraftValues(d => ({ ...d, [key]: e.target.value }))}
        onBlur={() => commit(name, col, displayValue, value)}
        onKeyDown={e => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') {
            setDraftValues(d => { const c = { ...d }; delete c[key]; return c })
            ;(e.target as HTMLInputElement).blur()
          }
        }}
        className="w-full px-1.5 py-0.5 border border-transparent rounded text-[11px] font-mono bg-transparent
                   focus:bg-bg focus:border-accent hover:border-border"
        placeholder={kind === 'number' ? '0' : ''}
      />
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-32 text-muted text-xs gap-1">
        <span>No carriers yet.</span>
        <span className="text-[10px]">Set a generator's <code>carrier</code> field to auto-create one.</span>
      </div>
    )
  }

  return (
    <div className="overflow-auto h-full">
      <table className="w-full text-xs border-collapse" style={{ minWidth: 'max-content' }}>
        <thead>
          <tr className="sticky top-0 bg-panel z-10 border-b border-border">
            <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase whitespace-nowrap">Name</th>
            <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-muted uppercase whitespace-nowrap"
                title="Carrier CO₂ intensity, tCO₂ per MWh of primary energy. The Emissions tab divides by generator efficiency to get tCO₂ per MWh of output.">
              CO₂ (t/MWh primary)
            </th>
            <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase whitespace-nowrap">Display name</th>
            <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase whitespace-nowrap">Color</th>
            <th className="text-left  px-2 py-1.5 text-[10px] font-semibold text-muted uppercase whitespace-nowrap">Unit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const name = row.name as string
            return (
              <tr key={name ?? i}
                  className={`border-b border-border/40 ${i % 2 === 0 ? 'bg-bg' : 'bg-panel'}`}>
                <td className="px-2 py-0.5 font-mono text-[11px] text-text whitespace-nowrap" title="Rename via a per-asset workflow only — changing the key here would silently break every reference.">
                  {name}
                </td>
                <td className="px-1 py-0.5 text-right">
                  {cellInput(name, 'co2_emissions', row.co2_emissions, 'number')}
                </td>
                <td className="px-1 py-0.5">
                  {cellInput(name, 'nice_name', row.nice_name, 'string')}
                </td>
                <td className="px-1 py-0.5 flex items-center gap-1">
                  <input
                    type="color"
                    value={(row.color as string) || '#888888'}
                    onChange={e => {
                      // Color picker fires every keystroke — buffer in the
                      // draft, commit on blur for a single PUT per change.
                      setDraftValues(d => ({ ...d, [draftKey(name, 'color')]: e.target.value }))
                    }}
                    onBlur={() => {
                      const k = draftKey(name, 'color')
                      const v = draftValues[k]
                      if (v !== undefined) commit(name, 'color', v, row.color)
                    }}
                    className="w-6 h-5 rounded border border-border cursor-pointer"
                  />
                  {cellInput(name, 'color', row.color, 'string')}
                </td>
                <td className="px-1 py-0.5">
                  {cellInput(name, 'unit', row.unit, 'string')}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="text-[10px] text-muted px-2 py-1.5 border-t border-border bg-bg-2/50">
        Click any cell to edit. <kbd className="px-1 border border-border rounded text-[9px] font-mono">Enter</kbd> saves,
        {' '}<kbd className="px-1 border border-border rounded text-[9px] font-mono">Esc</kbd> reverts.
        CO₂ values are per MWh of <em>primary</em> energy — output-MWh intensity is computed in the Emissions tab as
        <code> co2_emissions / efficiency</code>.
      </p>
    </div>
  )
}
