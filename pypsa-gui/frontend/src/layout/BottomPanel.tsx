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
import { useCatalog } from '../hooks/useCatalog'
import {
  CLOSED_SETS, buildSeriesIndex, columnHeaderLabel, columnHeaderTooltip,
  isBooleanDtype, resolveEditability, resolveEditor, toCatalogMap,
  type CatalogMap,
} from '../utils/attributeCatalog'
import { validateAndCoerce } from '../utils/gridEdit'
import {
  guardCell, parseTsv, resolvePasteShape, serialiseTsv, unguardCell,
} from '../utils/clipboardTsv'
import { isNumericDtype } from '../utils/attributeCatalog'
import BusAutocomplete from '../components/BusAutocomplete'
import CarrierSelect from '../components/CarrierSelect'

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

/**
 * FastAPI's `detail` is either a string or an array of validation objects.
 * Rendering the array directly gives "[object Object]", which
 * .cursor/rules/pypsa-gui-frontend.mdc:19 forbids and success criterion 10
 * tests for.
 */
function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail.map(d => {
      if (typeof d === 'string') return d
      const o = d as { loc?: unknown[]; msg?: string }
      const where = Array.isArray(o.loc) ? o.loc.filter(x => x !== 'body').join('.') : ''
      return where ? `${where}: ${o.msg ?? 'invalid'}` : (o.msg ?? 'invalid')
    })
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

// ── CellEditor ───────────────────────────────────────────────────────────────
// One editor is mounted at a time, chosen by D4's resolution table. Every
// editor commits a STRING through the same gridEdit validator, so the typed
// path and the paste path cannot disagree.
//
// Because a fresh editor mounts per cell, the uncontrolled-input staleness rule
// (CLAUDE.md:586-587) is satisfied structurally — no key-remount trick needed.
function CellEditor({
  componentClass, column, initial, seed, busNames, catalog, onCommit, onCancel,
}: {
  componentClass: string
  column: string
  initial: string
  seed?: string
  busNames: string[]
  catalog: CatalogMap
  /**
   * `advance` asks the grid to move the active cell down one row after a
   * successful commit. Only a plain Enter sets it: D5 gives Ctrl/Cmd+Enter
   * (fill) and a blur no move, and advancing off a blur would yank focus while
   * the user is reaching for another cell.
   */
  onCommit: (raw: string, fill: boolean, advance?: boolean) => void
  onCancel: () => void
}) {
  const [raw, setRaw] = useState(seed ?? initial)
  const kind = resolveEditor(componentClass, column, catalog)
  const inputCls = 'w-full bg-bg border border-accent px-1 py-0 text-[11px] font-mono outline-none'

  // Shared key handling for the text-like editors (D5, open-editor column).
  const onKey = (e: React.KeyboardEvent) => {
    const modifier = e.ctrlKey || e.metaKey
    if (e.key === 'Enter') {
      e.preventDefault(); e.stopPropagation()
      // Ctrl/Cmd+Enter = fill gesture, and D5 gives it no downward move.
      onCommit(raw, modifier, !modifier)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation()
      onCancel()
      return
    }
    if (modifier && (e.key === 's' || e.key === 'S')) {
      // A save must never run with a pending edit outstanding
      // (CLAUDE.md:650-666,812) and the grid cannot make its PATCH land
      // synchronously. Swallowing the first keypress is the honest behaviour.
      e.preventDefault(); e.stopPropagation()
      onCommit(raw, false)
      toast.success('Cell saved — press again to save the project')
      return
    }
    // Arrows must not reach the grid while an editor is open (D5). Bus cells
    // are the exception and handle their own arrows (BusAutocomplete).
    if (e.key.startsWith('Arrow')) e.stopPropagation()
  }

  if (kind === 'bus') {
    // `onChange` fires on every keystroke, so it may only move the draft.
    // Wiring it to onCommit made the first character commit a partial name —
    // and since commitCell's first act is setEditing(null), that closed the
    // editor and left the column unchangeable. Same hazard the colour editor
    // below already avoids by committing on blur rather than on change.
    //
    // The wrapper carries onKeyDown so Escape still cancels and Enter with
    // nothing highlighted commits a fully typed name; BusAutocomplete stops
    // both keys itself whenever it consumes them.
    return (
      <span onKeyDown={onKey}>
        <BusAutocomplete
          autoFocus
          value={raw}
          onChange={setRaw}
          onSelect={v => onCommit(v, false, true)}
          buses={busNames}
          allowUnknown={false}
          placeholder="Bus…"
        />
      </span>
    )
  }

  if (kind === 'carrier') {
    // Consumed as-is, styling props only (D4). Its native <select> popup cannot
    // be clipped by the grid's scroll container.
    return (
      <CarrierSelect
        value={raw}
        onChange={v => { setRaw(v); onCommit(v, false) }}
        label={null}
        className="w-full text-[11px] py-0"
        wrapperClassName="block"
      />
    )
  }

  if (kind === 'closedSet') {
    const options = CLOSED_SETS[`${componentClass}.${column}`] ?? []
    return (
      <select
        autoFocus
        value={raw}
        onChange={e => { setRaw(e.target.value); onCommit(e.target.value, false) }}
        onKeyDown={onKey}
        className={inputCls}
      >
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }

  if (kind === 'color') {
    // Carried over from CarriersTable: the picker fires on every keystroke, so
    // commit on blur for one request per change.
    return (
      <span className="flex items-center gap-1">
        <input
          type="color"
          value={/^#[0-9a-f]{6}$/i.test(raw) ? raw : '#888888'}
          onChange={e => setRaw(e.target.value)}
          onBlur={() => onCommit(raw, false)}
          className="w-6 h-5 rounded border border-border cursor-pointer"
        />
        <input
          autoFocus
          type="text"
          value={raw}
          onChange={e => setRaw(e.target.value)}
          onBlur={() => onCommit(raw, false)}
          onKeyDown={onKey}
          className={inputCls}
        />
      </span>
    )
  }

  // numeric and text both use a type="text" input: type="number" cannot hold
  // `inf` and reads back '' (D12).
  return (
    <input
      autoFocus
      type="text"
      value={raw}
      onChange={e => setRaw(e.target.value)}
      onBlur={() => onCommit(raw, false)}
      onKeyDown={onKey}
      className={inputCls}
    />
  )
}

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

  // AssetTable does not receive currentProject as a prop and does not
  // destructure it today — only its parent BottomPanel does. The grid needs it
  // for every nk() key below, so take it reactively here. In the non-React
  // mutation callbacks read it via useUIStore.getState().currentProject
  // instead: the parity rule at queryKeys.ts:16-22 is what makes a mismatched
  // id return undefined.
  const currentProject = useUIStore(s => s.currentProject)

  // ── Active cell (spec D19) ─────────────────────────────────────────────────
  // Exactly one cell is tabbable at a time; everything else carries -1. Focus
  // stays on a real element, so blur-commit and Escape work unchanged and no
  // ARIA grid roles are needed — the <table> already carries the right ones.
  // `seed` carries the character that opened the editor, so type-over replaces
  // the value instead of appending to it (D5). Only `editing` ever sets it.
  type CellRef = { name: string; col: string; seed?: string }
  const [active, setActive] = useState<CellRef | null>(null)
  // Which cell has an OPEN editor. One at a time: the draft is a single
  // { name, col, raw }, not a flat draft map, because at the render cap a map
  // would mount 1000 x 15 inputs — the DOM budget the cap exists to protect.
  const [editing, setEditing] = useState<CellRef | null>(null)
  useEffect(() => { setActive(null); setEditing(null) }, [tab])

  // ── Catalog + series shadow (D13, D14) ────────────────────────────────────
  const { data: catalogPayload } = useCatalog(componentClass)
  const catalog = useMemo(
    () => toCatalogMap(catalogPayload?.attributes ?? []),
    [catalogPayload],
  )
  const { data: tsList = [] } = useQuery({
    queryKey: nk(currentProject, 'timeseries'),
    queryFn: networkApi.listTimeseries,
  })
  const series = useMemo(
    () => buildSeriesIndex(tsList, TAB_TO_API_KEY[componentClass] ?? ''),
    [tsList, componentClass],
  )

  const editabilityOf = useCallback(
    (rowName: string, col: string) =>
      resolveEditability({ componentClass, column: col, rowName, catalog, series }),
    [componentClass, catalog, series],
  )

  // The bus list the bus-terminal editor offers, and the set gridEdit checks
  // membership against (exact and case-sensitive).
  const { data: allBusRows = [] } = useQuery({
    queryKey: nk(currentProject, 'buses'), queryFn: networkApi.getBuses,
  })
  const busNames = useMemo(
    () => (allBusRows as Array<{ name: string }>).map(b => b.name).sort(),
    [allBusRows],
  )

  const isBooleanCell = useCallback((col: string): boolean => {
    const attr = catalog.get(col)
    return !!attr && isBooleanDtype(attr.dtype)
  }, [catalog])

  const seriesShadowed = useCallback((rowName: string, col: string): boolean => {
    const ed = editabilityOf(rowName, col)
    return !ed.editable && ed.reason === 'series'
  }, [editabilityOf])

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

  /**
   * Composite key for the cell-ref map. A tab cannot occur in a PyPSA name or
   * attribute — the same fact utils/clipboardTsv.ts relies on to do without a
   * quote grammar — so it is a safe separator for `col` + `name`.
   */
  const cellKey = (name: string, col: string) => `${col}\t${name}`
  const cellRefs = useRef<Record<string, HTMLTableCellElement | null>>({})
  useEffect(() => {
    if (!active) return
    cellRefs.current[cellKey(active.name, active.col)]?.focus()
  }, [active])

  /** Move the active cell by (dRow, dCol) within `displayed` x `visibleCols`. */
  const moveActive = useCallback((dRow: number, dCol: number) => {
    setActive(prev => {
      if (!prev) return prev
      const rowIdx = displayed.findIndex(r => r.name === prev.name)
      const colIdx = visibleCols.indexOf(prev.col)
      if (rowIdx < 0 || colIdx < 0) return prev
      const nextRow = Math.min(Math.max(rowIdx + dRow, 0), displayed.length - 1)
      const nextCol = Math.min(Math.max(colIdx + dCol, 0), visibleCols.length - 1)
      return { name: displayed[nextRow].name as string, col: visibleCols[nextCol] }
    })
  }, [displayed, visibleCols])

  /**
   * Keyboard map for a cell with NO open editor (spec D5). The open-editor
   * column of that table lives in the editor component.
   *
   * stopPropagation() on Escape is what keeps an open slide panel open: every
   * other Escape listener in the app is bubble-phase (App.tsx:512 and the
   * unguarded ones in TopologyCanvas / MapCanvas / ChatPanel), so stopping
   * here pre-empts all of them without touching those files. The one
   * capture-phase listener, AppHeader.tsx:277, is guarded at its source.
   */
  const onCellKeyDown = (e: React.KeyboardEvent, rowName: string, col: string) => {
    if (editing) return                       // the editor owns the keyboard
    const modifier = e.ctrlKey || e.metaKey
    if (modifier) return                      // copy/paste

    switch (e.key) {
      case 'ArrowDown':  e.preventDefault(); moveActive(1, 0); return
      case 'ArrowUp':    e.preventDefault(); moveActive(-1, 0); return
      case 'ArrowRight': e.preventDefault(); moveActive(0, 1); return
      case 'ArrowLeft':  e.preventDefault(); moveActive(0, -1); return
      case 'Tab':
        e.preventDefault(); moveActive(0, e.shiftKey ? -1 : 1); return
      case 'Escape':
        e.stopPropagation()
        setActive(null)
        return
      case 'Enter':
        e.preventDefault()
        if (editabilityOf(rowName, col).editable) setEditing({ name: rowName, col })
        return
      default:
        // A printable character opens the editor seeded with it (D5).
        // preventDefault matters: without it the same keypress ALSO reaches the
        // freshly-focused input, so typing 7 over 400 produced "4007".
        if (e.key.length === 1 && editabilityOf(rowName, col).editable) {
          e.preventDefault()
          setEditing({ name: rowName, col, seed: e.key })
        }
    }
  }

  /** Visual state for a cell: active ring, and the read-only/series greys. */
  const cellClass = (rowName: string, col: string): string => {
    const base = 'px-2 font-mono whitespace-nowrap text-[11px] outline-none'
    const ring = active?.name === rowName && active?.col === col
      ? ' ring-1 ring-inset ring-accent' : ''
    const ed = editabilityOf(rowName, col)
    if (ed.editable) return base + ring
    // Output / override / unknown read as "not yours to edit"; a series-shadowed
    // cell reads as "the static value is dead here" (D14) and gets its badge in
    // the cell body.
    return `${base}${ring} text-muted bg-panel/40`
  }

  const toggleRow = (row: Record<string, unknown>, idx: number, ev: React.MouseEvent) => {
    const name = row.name as string
    if (!name) return
    // Read the modifier BEFORE the updater. React runs a functional setState
    // updater later, by which point the synthetic event has been recycled and
    // `ev.shiftKey` reads false — the range would silently degrade to a single
    // toggle. Same reason the anchor is snapshotted here.
    const rangeSelect = ev.shiftKey
    const anchor = lastClickedIdxRef.current
    setSelectedRows(prev => {
      const next = new Set(prev)
      if (rangeSelect && anchor !== null) {
        // Range select: include every row between the last anchor and this one.
        const lo = Math.min(anchor, idx)
        const hi = Math.max(anchor, idx)
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

  const [showColMenu, setShowColMenu] = useState(false)

  // ── Optimistic bulk write (spec D10, D11) ─────────────────────────────────
  // No in-repo precedent (recon §4), so the contract is implemented exactly as
  // the spec states it. The key MUST use the same projectId the useQuery that
  // populated the cache used, or getQueryData returns undefined and the
  // rollback wipes the payload (queryKeys.ts:16-22).
  const lastMutationAtRef = useRef(0)

  type BulkRow = { name: string; updates: Record<string, unknown> }
  type BulkBody = {
    component_class: string
    names?: string[]
    updates?: Record<string, unknown>
    rows?: BulkRow[]
  }
  type BulkCtx = { previous: unknown; selection: Set<string>; key: unknown[] }

  const bulkMut = useMutation({
    mutationFn: (body: BulkBody) => networkApi.bulkUpdate(body),
    onMutate: async (body: BulkBody): Promise<BulkCtx> => {
      const projectId = useUIStore.getState().currentProject
      const key = nk(projectId, TAB_TO_API_KEY[componentClass] ?? componentClass.toLowerCase() + 's')
      await qc.cancelQueries({ queryKey: key })
      const previous = qc.getQueryData(key)
      const selection = new Set(selectedRows)

      const patch = new Map<string, Record<string, unknown>>()
      if (body.rows) for (const r of body.rows) patch.set(r.name, r.updates)
      else for (const n of body.names ?? []) patch.set(n, body.updates ?? {})

      qc.setQueryData(key, (old: Record<string, unknown>[] | undefined) =>
        (old ?? []).map(r => {
          const up = patch.get(r.name as string)
          return up ? { ...r, ...up } : r
        }))
      return { previous, selection, key }
    },
    onError: (e: { response?: { data?: { detail?: unknown } } }, _body, ctx) => {
      if (ctx) {
        qc.setQueryData(ctx.key, ctx.previous)
        setSelectedRows(ctx.selection)
        // Re-read the truth rather than trusting the rollback.
        qc.invalidateQueries({ queryKey: ctx.key })
      }
      toast.error(formatDetail(e.response?.data?.detail, 'Bulk update failed'))
    },
    onSuccess: (r) => {
      lastMutationAtRef.current = Date.now()
      // SCOPED invalidation. A bare `qc.invalidateQueries()` would wipe EVERY
      // cached query, triggering ~15 refetches (carriers, profiles,
      // ac_pf_status, snapshots, …) per apply. We only need:
      //  • the table for the component class we edited
      //  • the audit-trail surfaces (undoInfo, changelog)
      //  • result queries (they reference component data via bus/name joins)
      const projectId = useUIStore.getState().currentProject
      const tableKey = TAB_TO_API_KEY[componentClass] ?? componentClass.toLowerCase() + 's'
      qc.invalidateQueries({ queryKey: [tableKey] })
      qc.invalidateQueries({ queryKey: nk(projectId, 'undoInfo') })
      qc.invalidateQueries({ queryKey: ['changelog'] })
      qc.invalidateQueries({ queryKey: nk(projectId, 'results') })
      toast.success(`Updated ${r.updated} ${componentClass.toLowerCase()}(s)`)
    },
  })

  /**
   * Issue one request for one gesture.
   *
   * `spaceUndo` is true for a paste or a fill: D11 requires each of those to
   * get its OWN undo step, and the middleware coalesces pushes inside a 500 ms
   * window (undo_service.py:55,90-102). Waiting out the remainder of that
   * window is a client-side timing concern, deliberately not a server flag.
   * Single-cell commits pass false and are allowed to coalesce (ruling 16).
   */
  const applyBulk = async (rows: BulkRow[], spaceUndo = false) => {
    if (rows.length === 0) return
    const first = JSON.stringify(rows[0].updates)
    const sameEverywhere = rows.every(r => JSON.stringify(r.updates) === first)

    if (spaceUndo) {
      const elapsed = Date.now() - lastMutationAtRef.current
      if (lastMutationAtRef.current > 0 && elapsed < 500) {
        await new Promise(res => setTimeout(res, 500 - elapsed))
      }
    }

    // The scalar form whenever every row gets the same value in every column
    // (every fill gesture); the row form otherwise. One gesture is always
    // exactly one request (D9).
    bulkMut.mutate(sameEverywhere
      ? { component_class: componentClass, names: rows.map(r => r.name), updates: rows[0].updates }
      : { component_class: componentClass, rows })
  }

  /**
   * The single commit path for a typed editor (D4). `fill` means the value
   * applies to the whole paste target (Ctrl/Cmd+Enter, or Ctrl/Cmd+click on a
   * boolean), not just this row.
   */
  const commitCell = (
    rowName: string, col: string, raw: string, fill: boolean, advance = false,
  ) => {
    setEditing(null)
    const row = sorted.find(r => r.name === rowName)
    const currentText = row?.[col] == null ? '' : String(row[col])
    // No round-trip when the committed text equals the cell's current display
    // text — the same skip the old CarriersTable did (criterion 2). The move
    // still happens: Enter advanced the cursor whether or not anything changed.
    if (!fill && raw === currentText) {
      if (advance) moveActive(1, 0)
      return
    }

    const result = validateAndCoerce(col, raw, {
      componentClass, catalog, busNames: new Set(busNames),
    })
    // Deliberately no advance on a rejection: the cell keeps focus so the user
    // can correct the value they just typed.
    if (!result.ok) { toast.error(result.error); return }

    const targets = fill && selectedRows.size > 0 ? [...selectedRows] : [rowName]
    applyBulk(targets.map(n => ({ name: n, updates: { [col]: result.value } })))
    if (advance) moveActive(1, 0)
  }

  /**
   * The paste target (D7): the checkbox selection if non-empty, otherwise the
   * active cell's row — in `sorted` order, NOT `displayed`, so rows past the
   * 1000-row render cap are included (criterion 4).
   */
  const pasteTargetRows = useCallback((): string[] => {
    if (selectedRows.size > 0) {
      return sorted.map(r => r.name as string).filter(n => selectedRows.has(n))
    }
    return active ? [active.name] : []
  }, [selectedRows, sorted, active])

  /** True when a column's values are text, i.e. injection-guard territory. */
  const isStringColumn = useCallback((col: string): boolean => {
    const attr = catalog.get(col)
    if (!attr) return true
    return !isNumericDtype(attr.dtype) && !isBooleanDtype(attr.dtype)
  }, [catalog])

  const onCopy = (e: React.ClipboardEvent) => {
    if (editing) return                        // native input copy
    const rows = pasteTargetRows()
    if (!active || rows.length === 0) return
    e.preventDefault()
    const cols = selectedRows.size > 0 ? visibleCols : [active.col]
    const matrix = rows.map(rowName => {
      const row = sorted.find(r => r.name === rowName)
      return cols.map(c => guardCell(
        row?.[c] == null ? '' : String(row[c]), isStringColumn(c),
      ))
    })
    e.clipboardData.setData('text/plain', serialiseTsv(matrix))
  }

  const onPaste = (e: React.ClipboardEvent) => {
    if (editing) return                        // native input paste
    if (!active) return
    e.preventDefault()

    const matrix = parseTsv(e.clipboardData.getData('text/plain'))
    const targets = pasteTargetRows()
    const startCol = visibleCols.indexOf(active.col)
    const columnsAvailable = Math.max(visibleCols.length - startCol, 0)
    const shape = resolvePasteShape(matrix, targets.length, columnsAvailable)
    if (shape.kind === 'reject') { toast.error(shape.message); return }

    // Expand every shape into the same (row, col, raw) list so validation and
    // the no-op skip have exactly one implementation (D7, D8).
    const cells: { name: string; col: string; raw: string }[] = []
    if (shape.kind === 'fill') {
      for (const nm of targets) cells.push({ name: nm, col: active.col, raw: shape.value })
    } else if (shape.kind === 'rowwise') {
      targets.forEach((nm, i) => cells.push({ name: nm, col: active.col, raw: shape.values[i] }))
    } else {
      targets.forEach((nm, i) => shape.matrix[i].forEach((raw, j) => {
        cells.push({ name: nm, col: visibleCols[startCol + j], raw })
      }))
    }

    // Whole-batch validation (D8): editability first, then the value.
    const offenders: string[] = []
    const accepted: { name: string; col: string; value: unknown }[] = []
    for (const cell of cells) {
      const ed = editabilityOf(cell.name, cell.col)
      if (!ed.editable) { offenders.push(`${cell.name} / ${cell.col}`); continue }
      const raw = unguardCell(cell.raw, isStringColumn(cell.col))
      const result = validateAndCoerce(cell.col, raw, {
        componentClass, catalog, busNames: new Set(busNames),
      })
      if (!result.ok) { offenders.push(`${cell.name} / ${cell.col}`); continue }
      const row = sorted.find(r => r.name === cell.name)
      const currentText = row?.[cell.col] == null ? '' : String(row[cell.col])
      if (raw === currentText) continue                    // no-op, criterion 3
      accepted.push({ name: cell.name, col: cell.col, value: result.value })
    }

    if (offenders.length > 0) {
      const shown = offenders.slice(0, 5).join(', ')
      const rest = offenders.length > 5 ? ` and ${offenders.length - 5} more` : ''
      toast.error(`Paste rejected — cannot write ${shown}${rest}.`)
      return
    }
    if (accepted.length === 0) { toast('No changes'); return }

    // Group by row so one gesture is one request (D9).
    const byRow = new Map<string, Record<string, unknown>>()
    for (const a of accepted) {
      const existing = byRow.get(a.name) ?? {}
      existing[a.col] = a.value
      byRow.set(a.name, existing)
    }
    const rows = [...byRow].map(([name, updates]) => ({ name, updates }))

    // D18: a confirmToast, never a Dialog — Dialog.tsx:148 is capture-phase AND
    // calls stopPropagation, so while one is open it swallows the grid's Escape.
    if (targets.length > 200) {
      confirmToast(
        `Apply this paste to ${targets.length} rows?`,
        () => { applyBulk(rows, true) },
        { confirmLabel: 'Paste' },
      )
      return
    }
    applyBulk(rows, true)                                  // true → D11 spacing
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

        {/* The select-a-column-and-type-a-value toolbar is gone (D29): paste
            respects the row selection and Ctrl/Cmd+Enter fills it, so the
            capability survives with a better interface. */}
        {selectedRows.size > 0 ? (
          <>
            <span className="text-muted">·</span>
            <span className="font-medium text-text">{selectedRows.size} selected</span>
            <span className="text-muted">
              ·  Paste to write every selected row, or Ctrl/Cmd+Enter in a cell to fill them.
            </span>
            <button
              onClick={() => setSelectedRows(new Set())}
              className="text-muted hover:text-danger flex items-center gap-0.5"
              title="Clear selection"
            ><X size={11} /></button>
          </>
        ) : (
          <span className="text-muted">
            ·  Click a cell to select it, double-click or press Enter to edit.
            Select rows to paste or fill across many.
          </span>
        )}
      </div>

      {/* Table. Copy/paste are bound here rather than on each cell so a paste
          anywhere in the grid is caught, and via ClipboardEvent rather than
          navigator.clipboard — no secure context, no gesture permission, no
          Firefox prompt (spec D6). */}
      <div className="flex-1 overflow-auto" onCopy={onCopy} onPaste={onPaste}>
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
                  // D15: a curated COL_LABELS entry wins; otherwise the catalog
                  // unit is appended, so Lines read `r (Ohm)`. The tooltip
                  // additionally states the per-km split for r/x/b.
                  title={columnHeaderTooltip(componentClass, col, catalog) ?? undefined}
                  className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide cursor-pointer hover:text-text whitespace-nowrap select-none"
                >
                  {columnHeaderLabel(col, catalog, COL_LABELS)}
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
                      ref={el => { cellRefs.current[cellKey(name, col)] = el }}
                      data-row={name}
                      data-col={col}
                      // Roving tabindex (D19): exactly one cell is tabbable, so
                      // focus stays on a real element and blur-commit works.
                      tabIndex={active?.name === name && active?.col === col ? 0 : -1}
                      // No stopPropagation: the row's own onClick still opens
                      // the properties panel, which is existing behaviour.
                      onClick={() => setActive({ name, col })}
                      // Double-click edits; a single click only selects. Both
                      // halves are load-bearing: onCopy/onPaste bail while an
                      // editor is open, so if a single click opened one the
                      // multi-row paste flow would be unreachable by mouse.
                      onDoubleClick={() => {
                        if (editabilityOf(name, col).editable) {
                          setEditing({ name, col })
                        }
                      }}
                      onKeyDown={e => onCellKeyDown(e, name, col)}
                      className={cellClass(name, col)}
                      style={{ paddingBlock: 'var(--row-padding-y)' }}
                    >
                      {/* Editors are disabled while a write is in flight — the
                          ModelHorizon.tsx:907-913 pattern, which prevents a
                          double-blur race. */}
                      {editing?.name === name && editing?.col === col && !bulkMut.isPending ? (
                        <CellEditor
                          componentClass={componentClass}
                          column={col}
                          initial={row[col] == null ? '' : String(row[col])}
                          seed={editing.seed}
                          busNames={busNames}
                          catalog={catalog}
                          onCommit={(raw, fill, advance) =>
                            commitCell(name, col, raw, fill, advance)}
                          onCancel={() => setEditing(null)}
                        />
                      ) : isBooleanCell(col) ? (
                        // The one exception to click-to-edit (D4): a checkbox
                        // holds no draft, so there is nothing to open. Bounded
                        // by boolean columns x 1000, at most two per tab.
                        <input
                          type="checkbox"
                          checked={row[col] === true}
                          disabled={!editabilityOf(name, col).editable}
                          onClick={e => {
                            e.stopPropagation()
                            // Ctrl/Cmd+click is a FILL gesture over the paste
                            // target, mirroring Ctrl/Cmd+Enter and preserving
                            // the deleted toolbar's set-many-booleans ability.
                            const modifier = e.ctrlKey || e.metaKey
                            commitCell(name, col, row[col] === true ? 'false' : 'true', modifier)
                          }}
                          onChange={() => { /* commit happens in onClick */ }}
                          className="cursor-pointer"
                        />
                      ) : seriesShadowed(name, col) ? (
                        <span className="flex items-center gap-1">
                          <span className="opacity-50">{fmt(row[col])}</span>
                          <span
                            title="A time series exists for this asset — the static value is not what the solver reads."
                            className="px-1 rounded bg-border/40 text-[8px] uppercase tracking-wide"
                          >series</span>
                        </span>
                      ) : (
                        fmt(row[col])
                      )}
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
          ) : (
            <>
              <AssetTable
                tab={activeTab}
                componentClass={TAB_TYPES[activeTab] ?? ''}
                data={tableData[activeTab]}
                defaultColumns={TAB_COLUMNS[activeTab]}
                selectedName={selectedName}
                onRowClick={handleRowClick}
              />
              {activeTab === 'Carriers' && (
                // Carried over from the deleted CarriersTable — the one piece
                // of that component the shared grid has no place for (D16).
                <p className="text-[10px] text-muted px-2 py-1.5 border-t border-border bg-bg-2/50">
                  CO₂ values are per MWh of <em>primary</em> energy — output-MWh
                  intensity is computed in the Emissions tab as
                  <code> co2_emissions / efficiency</code>.
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
