import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Brush, Legend
} from 'recharts'
import { resultsApi } from '../api/simulation'
import { safeMax } from '../utils/numeric'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

const TABS = ['Summary', 'Dispatch', 'Prices', 'Storage', 'Line Flows'] as const
type Tab = typeof TABS[number]

const CARRIER_COLORS: Record<string, string> = {
  onwind: '#2f81f7', offwind: '#79c0ff', solar: '#d29922', CCGT: '#f0883e',
  OCGT: '#f85149', nuclear: '#3fb950', hydro: '#a5d6ff', biomass: '#56d364',
  battery: '#a371f7', H2: '#d2a8ff', coal: '#8b949e', lignite: '#6e4040',
  load: '#f85149',
}
const DEFAULT_COLOR = '#8b949e'

function getColor(key: string, index: number): string {
  return CARRIER_COLORS[key] ?? Object.values(CARRIER_COLORS)[index % Object.values(CARRIER_COLORS).length] ?? DEFAULT_COLOR
}

function NoData() {
  return (
    <div className="flex-1 flex items-center justify-center">
      <p className="text-muted text-sm">No results available — run an optimization first</p>
    </div>
  )
}

// ── Summary tab ───────────────────────────────────────────────────────────────
function SummaryTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: stats } = useQuery({ queryKey: nk(currentProject, 'results_statistics'), queryFn: resultsApi.getStatistics })

  if (!stats) return <NoData />

  const rows = Array.isArray(stats) ? stats : []
  const totalCost = rows.reduce((s: number, r: Record<string,number>) => s + (r.Capital_Cost ?? 0) + (r.Operational_Cost ?? 0), 0)

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {/* Metric cards */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-panel border border-border rounded p-3">
          <p className="text-[10px] text-muted mb-1">Total System Cost</p>
          <p className="text-lg font-mono text-accent">{(totalCost / 1e6).toFixed(2)} M€</p>
        </div>
        <div className="bg-panel border border-border rounded p-3">
          <p className="text-[10px] text-muted mb-1">Components</p>
          <p className="text-lg font-mono text-text">{rows.length}</p>
        </div>
      </div>

      {/* Statistics table */}
      <div>
        <p className="text-xs font-semibold text-muted mb-2">Component Statistics</p>
        <div className="overflow-auto">
          <table className="w-full text-xs border-collapse min-w-max">
            <thead>
              <tr className="border-b border-border">
                {rows.length > 0 && Object.keys(rows[0]).map(k => (
                  <th key={k} className="px-2 py-1.5 text-left text-muted font-semibold whitespace-nowrap">{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row: Record<string, unknown>, i: number) => (
                <tr key={i} className={`border-b border-border/40 ${i % 2 === 0 ? 'bg-bg' : 'bg-panel/60'}`}>
                  {Object.values(row).map((v, j) => (
                    <td key={j} className="px-2 py-1 whitespace-nowrap font-mono text-text">
                      {v == null ? '—' : typeof v === 'number' ? v.toFixed(2) : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Dispatch tab ──────────────────────────────────────────────────────────────
function DispatchTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: genRes } = useQuery({ queryKey: nk(currentProject, 'results_generators'), queryFn: () => resultsApi.getGeneratorResults() })
  const { data: loadRes } = useQuery({ queryKey: nk(currentProject, 'results_loads'), queryFn: () => resultsApi.getLoadResults() })
  const [selectedCarriers, setSelectedCarriers] = useState<Set<string>>(new Set())

  const { chartData, carriers } = useMemo(() => {
    if (!genRes) return { chartData: [], carriers: [] as string[] }
    const { index, columns, data } = genRes
    const carriers = columns as string[]
    const chartData = index.map((ts: string, i: number) => {
      const point: Record<string, unknown> = { ts: ts.slice(0, 16) }
      carriers.forEach((col: string, j: number) => { point[col] = (data as number[][])[i]?.[j] ?? 0 })
      if (loadRes) {
        const li = loadRes.index.indexOf(ts)
        if (li >= 0) point['_load'] = loadRes.data[li]?.reduce((s: number, v: number) => s + Math.abs(v), 0)
      }
      return point
    })
    return { chartData, carriers }
  }, [genRes, loadRes])

  if (!genRes) return <NoData />

  const activeCarriers = selectedCarriers.size > 0 ? carriers.filter(c => selectedCarriers.has(c)) : carriers

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Carrier filter */}
      <div className="flex flex-wrap gap-1 px-3 py-2 border-b border-border shrink-0">
        {carriers.map((c, i) => (
          <button
            key={c}
            onClick={() => setSelectedCarriers(prev => {
              const next = new Set(prev)
              next.has(c) ? next.delete(c) : next.add(c)
              return next
            })}
            className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
              selectedCarriers.size === 0 || selectedCarriers.has(c)
                ? 'border-accent/50 text-text' : 'border-border text-muted'
            }`}
            style={{ borderColor: getColor(c, i) + '60', color: getColor(c, i) }}
          >
            {c}
          </button>
        ))}
      </div>
      <div className="flex-1 p-3 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 40, left: 50 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
            <XAxis dataKey="ts" tick={{ fill: '#8b949e', fontSize: 9 }} interval="preserveStartEnd" />
            <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} unit=" MW" />
            <Tooltip contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 10 }} />
            <Brush dataKey="ts" height={20} stroke="#30363d" fill="#161b22" travellerWidth={6} />
            {activeCarriers.map((c, i) => (
              <Area key={c} type="monotone" dataKey={c} stackId="1"
                stroke={getColor(c, i)} fill={getColor(c, i)} fillOpacity={0.6}
                strokeWidth={1} dot={false} isAnimationActive={false} />
            ))}
            <Line type="monotone" dataKey="_load" stroke="#f85149" dot={false}
              strokeWidth={1.5} strokeDasharray="4 2" name="Load" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── Prices tab ────────────────────────────────────────────────────────────────
function PricesTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: priceRes } = useQuery({ queryKey: nk(currentProject, 'results_prices'), queryFn: () => resultsApi.getPrices() })
  const [selectedBuses, setSelectedBuses] = useState<Set<string>>(new Set())

  const { chartData, buses } = useMemo(() => {
    if (!priceRes) return { chartData: [], buses: [] as string[] }
    const { index, columns, data } = priceRes
    const buses = columns as string[]
    const chartData = index.map((ts: string, i: number) => {
      const point: Record<string, unknown> = { ts: ts.slice(0, 16) }
      buses.forEach((b: string, j: number) => { point[b] = (data as number[][])[i]?.[j] ?? 0 })
      return point
    })
    return { chartData, buses }
  }, [priceRes])

  if (!priceRes) return <NoData />

  const activeBuses = selectedBuses.size > 0 ? buses.filter(b => selectedBuses.has(b)) : buses.slice(0, 8)

  const stats = activeBuses.map((b, i) => {
    const ci = buses.indexOf(b)
    const vals = (priceRes as { data: number[][] }).data.map((row: number[]) => row[ci]).filter((v: number) => v != null && !isNaN(v))
    if (!vals.length) return null
    const sorted = [...vals].sort((a, b) => a - b)
    return {
      bus: b,
      mean: (vals.reduce((s: number, v: number) => s + v, 0) / vals.length).toFixed(2),
      p10: sorted[Math.floor(sorted.length * 0.1)].toFixed(2),
      p90: sorted[Math.floor(sorted.length * 0.9)].toFixed(2),
      color: getColor(b, i),
    }
  }).filter(Boolean)

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex flex-wrap gap-1 px-3 py-2 border-b border-border shrink-0">
        {buses.map((b, i) => (
          <button key={b}
            onClick={() => setSelectedBuses(prev => {
              const next = new Set(prev); next.has(b) ? next.delete(b) : next.add(b); return next
            })}
            className="px-2 py-0.5 rounded text-[10px] border transition-colors"
            style={{ borderColor: getColor(b, i) + '60', color: activeBuses.includes(b) ? getColor(b, i) : '#8b949e' }}
          >
            {b}
          </button>
        ))}
      </div>
      <div className="flex-1 p-3 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 40, left: 50 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
            <XAxis dataKey="ts" tick={{ fill: '#8b949e', fontSize: 9 }} interval="preserveStartEnd" />
            <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} unit=" €/MWh" />
            <Tooltip contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 10 }} />
            <Brush dataKey="ts" height={20} stroke="#30363d" fill="#161b22" travellerWidth={6} />
            {activeBuses.map((b, i) => (
              <Line key={b} type="monotone" dataKey={b} stroke={getColor(b, i)}
                dot={false} strokeWidth={1.5} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {stats.length > 0 && (
        <div className="shrink-0 border-t border-border px-3 py-1.5 flex flex-wrap gap-4">
          {stats.map(s => s && (
            <div key={s.bus} className="flex gap-2 text-[10px]">
              <span className="font-semibold" style={{ color: s.color }}>{s.bus}</span>
              <span className="text-muted">mean <span className="text-text font-mono">{s.mean}</span></span>
              <span className="text-muted">p10 <span className="text-text font-mono">{s.p10}</span></span>
              <span className="text-muted">p90 <span className="text-text font-mono">{s.p90}</span></span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Storage tab ───────────────────────────────────────────────────────────────
function StorageTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: stoRes } = useQuery({ queryKey: nk(currentProject, 'results_storage'), queryFn: () => resultsApi.getStorageResults() })

  const { chartData, units } = useMemo(() => {
    if (!stoRes) return { chartData: [], units: [] as string[] }
    const { index, columns, data } = stoRes
    const units = columns as string[]
    const chartData = index.map((ts: string, i: number) => {
      const point: Record<string, unknown> = { ts: ts.slice(0, 16) }
      units.forEach((u: string, j: number) => { point[u] = (data as number[][])[i]?.[j] ?? 0 })
      return point
    })
    return { chartData, units }
  }, [stoRes])

  if (!stoRes) return <NoData />

  return (
    <div className="flex-1 p-3 min-h-0">
      <p className="text-xs text-muted mb-2">State of Charge (MWh)</p>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 40, left: 50 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
          <XAxis dataKey="ts" tick={{ fill: '#8b949e', fontSize: 9 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} unit=" MWh" />
          <Tooltip contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 10 }} />
          <Legend wrapperStyle={{ fontSize: 10, color: '#8b949e' }} />
          <Brush dataKey="ts" height={20} stroke="#30363d" fill="#161b22" travellerWidth={6} />
          {units.map((u, i) => (
            <Line key={u} type="monotone" dataKey={u} stroke={getColor(u, i)}
              dot={false} strokeWidth={1.5} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Line Flows tab ────────────────────────────────────────────────────────────
function LineFlowsTab() {
  const currentProject = useUIStore(s => s.currentProject)
  const { data: lineRes } = useQuery({ queryKey: nk(currentProject, 'results_lines'), queryFn: () => resultsApi.getLineResults() })
  const [selectedLine, setSelectedLine] = useState<string | null>(null)

  const { chartData, lines, lineStats } = useMemo(() => {
    if (!lineRes) return { chartData: [], lines: [] as string[], lineStats: [] }
    const { index, columns, data } = lineRes
    const lines = columns as string[]
    const chartData = index.map((ts: string, i: number) => {
      const point: Record<string, unknown> = { ts: ts.slice(0, 16) }
      lines.forEach((l: string, j: number) => { point[l] = (data as number[][])[i]?.[j] ?? 0 })
      return point
    })
    const lineStats = lines.slice(0, 20).map((l, i) => {
      const ci = lines.indexOf(l)
      const vals = (data as number[][]).map((row: number[]) => row[ci]).filter((v: number) => v != null && !isNaN(v))
      if (!vals.length) return null
      return {
        name: l,
        max: safeMax(vals.map(Math.abs)).toFixed(1),
        mean: (vals.reduce((s: number, v: number) => s + Math.abs(v), 0) / vals.length).toFixed(1),
        color: getColor(l, i),
      }
    }).filter(Boolean)
    return { chartData, lines, lineStats }
  }, [lineRes])

  if (!lineRes) return <NoData />

  const activeLine = selectedLine ?? lines[0] ?? null

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Line list */}
      <div className="w-44 shrink-0 border-r border-border overflow-y-auto">
        {lineStats.map(s => s && (
          <button
            key={s.name}
            onClick={() => setSelectedLine(s.name)}
            className={`w-full flex items-start gap-2 px-3 py-2 text-xs transition-colors ${
              activeLine === s.name ? 'bg-accent/10 text-accent' : 'text-muted hover:text-text hover:bg-accent/5'
            }`}
          >
            <span className="truncate font-mono">{s.name}</span>
          </button>
        ))}
      </div>

      <div className="flex-1 flex flex-col overflow-hidden">
        {activeLine && (
          <>
            <div className="shrink-0 px-3 py-1.5 border-b border-border flex gap-4">
              {lineStats.filter(s => s?.name === activeLine).map(s => s && (
                <div key={s.name} className="flex gap-4 text-xs">
                  <span className="text-muted">max <span className="font-mono text-text">{s.max} MW</span></span>
                  <span className="text-muted">mean |flow| <span className="font-mono text-text">{s.mean} MW</span></span>
                </div>
              ))}
            </div>
            <div className="flex-1 p-3 min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 40, left: 50 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
                  <XAxis dataKey="ts" tick={{ fill: '#8b949e', fontSize: 9 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fill: '#8b949e', fontSize: 10 }} unit=" MW" />
                  <Tooltip contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 10 }} />
                  <Brush dataKey="ts" height={20} stroke="#30363d" fill="#161b22" travellerWidth={6} />
                  <Bar dataKey={activeLine} fill="#2f81f7" radius={[2, 2, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function ResultsViewer() {
  const [tab, setTab] = useState<Tab>('Summary')

  return (
    <div className="flex flex-col h-full">
      <div className="flex border-b border-border px-3 pt-2 shrink-0">
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm -mb-px border-b-2 transition-colors
              ${tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-text'}`}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex flex-col flex-1 overflow-hidden">
        {tab === 'Summary' && <SummaryTab />}
        {tab === 'Dispatch' && <DispatchTab />}
        {tab === 'Prices' && <PricesTab />}
        {tab === 'Storage' && <StorageTab />}
        {tab === 'Line Flows' && <LineFlowsTab />}
      </div>
    </div>
  )
}
