// The project's saved report charts, shared by every "Add to report" button
// and the Generate dialog: one list per project, fetched on first use and kept
// in step by this module's own add / remove (ui-modern 02, slice 7b).
import { useEffect, useSyncExternalStore } from 'react'
import {
  addReportChart, listReportCharts, removeReportChart,
  type ChartCell, type ChartSnapshot, type ChartSpec,
} from '../../api/reports'

export const MAX_CELL_CHARS = 200
export const MAX_CELLS = 10_000

interface State { charts: ChartSnapshot[]; limit: number; loaded: boolean }
const EMPTY: State = { charts: [], limit: 12, loaded: false }
const states = new Map<string, State>()
const loading = new Set<string>()
const listeners = new Set<() => void>()

function set(project: string, s: State) {
  states.set(project, s)
  listeners.forEach(l => l())
}
const get = (project: string | null): State => (project && states.get(project)) || EMPTY

/** Forget everything (tests; a project switch needs no call). */
export function resetReportCharts() { states.clear(); loading.clear(); listeners.forEach(l => l()) }

export async function refreshReportCharts(project: string): Promise<void> {
  if (loading.has(project)) return
  loading.add(project)
  try {
    const r = await listReportCharts(project)
    set(project, { charts: r.charts, limit: r.limit, loaded: true })
  } catch {
    // Leave the list as it was: the buttons then read "Add to report".
  } finally {
    loading.delete(project)
  }
}

export function useReportCharts(project: string | null, enabled = true) {
  const state = useSyncExternalStore(
    cb => { listeners.add(cb); return () => { listeners.delete(cb) } },
    () => get(project),
  )
  useEffect(() => {
    if (enabled && project && !states.get(project)?.loaded) void refreshReportCharts(project)
  }, [enabled, project])
  return state
}

export async function addChart(project: string, spec: ChartSpec, png: Blob): Promise<ChartSnapshot> {
  const row = await addReportChart(project, spec, png)
  const s = get(project)
  set(project, { ...s, charts: [...s.charts, row] })
  return row
}

export async function removeChart(project: string, id: string): Promise<void> {
  await removeReportChart(project, id)
  const s = get(project)
  set(project, { ...s, charts: s.charts.filter(c => c.id !== id) })
}

/** The saved chart that is this chart: same view, figure id and title. */
export function findSnapshot(
  charts: readonly ChartSnapshot[], view: string, figure: string, title: string,
): ChartSnapshot | undefined {
  return charts.find(c => c.view === view && c.figure === figure && c.title === title)
}

const cell = (v: unknown): ChartCell => {
  if (v == null) return null
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string') return v.length > MAX_CELL_CHARS ? v.slice(0, MAX_CELL_CHARS) : v
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  return String(v).slice(0, MAX_CELL_CHARS)
}

/** Rows the backend accepts: NaN / Infinity become empty, long text is cut, and
 *  the table is capped at 10 000 cells (`rowsTotal` keeps the true count). */
export function sanitizeRows(columns: readonly string[], rows: readonly (readonly unknown[])[]) {
  const width = Math.max(columns.length, 1)
  const keep = Math.floor(MAX_CELLS / width)
  return rows.slice(0, keep).map(r => columns.map((_, i) => cell(r[i])))
}
