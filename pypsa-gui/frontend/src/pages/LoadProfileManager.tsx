import { useState, useCallback, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { Download, Upload, FileSpreadsheet, CheckCircle2, Circle, AlertCircle } from 'lucide-react'
import { networkApi } from '../api/network'
import type { Load, LoadProfileMeta } from '../api/types'
import { appLog } from '../store/simulationStore'
import toast from 'react-hot-toast'
import { safeMax, safeMin } from '../utils/numeric'

// ── Helpers ────────────────────────────────────────────────────────────────────

const ACCENT = '#2f81f7'

function fmtMW(v: number) {
  return v >= 1000 ? `${(v / 1000).toFixed(2)} GW` : `${v.toFixed(1)} MW`
}

// ── Load list item ─────────────────────────────────────────────────────────────

function LoadListItem({
  load, meta, selected, onClick,
}: {
  load: Load
  meta: LoadProfileMeta | undefined
  selected: boolean
  onClick: () => void
}) {
  const hasProfile = meta?.has_profile === true

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors border-b border-border/40
        ${selected ? 'bg-accent/10 text-accent' : 'hover:bg-accent/5 text-text'}`}
    >
      {hasProfile
        ? <CheckCircle2 size={13} className="shrink-0 text-success" />
        : <Circle size={13} className="shrink-0 text-muted" />}
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium truncate">{load.name}</p>
        <p className="text-[10px] text-muted truncate">{load.bus}</p>
      </div>
      {hasProfile && meta.has_profile && (
        <span className="text-[9px] text-success font-mono shrink-0">
          {meta.rows}h
        </span>
      )}
    </button>
  )
}

// ── Upload zone ────────────────────────────────────────────────────────────────

function UploadZone({ onFile }: { onFile: (f: File) => void }) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) onFile(f)
  }, [onFile])

  return (
    <div
      onDrop={onDrop}
      onDragOver={e => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onClick={() => inputRef.current?.click()}
      className={`border-2 border-dashed rounded-lg p-6 flex flex-col items-center gap-2 cursor-pointer transition-colors
        ${dragging ? 'border-accent bg-accent/5' : 'border-border hover:border-accent/50 hover:bg-accent/3'}`}
    >
      <Upload size={24} className={dragging ? 'text-accent' : 'text-muted'} />
      <p className="text-xs font-semibold text-text">Drop Excel or CSV here</p>
      <p className="text-[10px] text-muted text-center">
        Columns = load names · Index = timestamps (ISO 8601)
      </p>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        className="hidden"
        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }}
      />
    </div>
  )
}

// ── Profile chart ──────────────────────────────────────────────────────────────

function ProfileChart({ loadName }: { loadName: string }) {
  const { data: tsData, isLoading } = useQuery({
    queryKey: ['timeseries', 'loads', 'p_set', loadName],
    queryFn: () => networkApi.getTimeseries('loads', 'p_set', [loadName]),
  })

  if (isLoading) {
    return <div className="flex items-center justify-center h-full text-muted text-xs">Loading…</div>
  }

  if (!tsData || !tsData.columns.includes(loadName)) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
        <AlertCircle size={20} />
        <p className="text-xs">No profile uploaded for this load</p>
      </div>
    )
  }

  const colIdx = tsData.columns.indexOf(loadName)
  const chartData = tsData.index.map((ts, i) => ({
    ts: ts.slice(0, 13).replace('T', ' '),  // "2024-01-01 06"
    value: tsData.data[i]?.[colIdx] ?? null,
  }))

  const vals = chartData.map(d => d.value).filter((v): v is number => v != null)
  // safeMax/safeMin avoid the `Math.max(...arr)` spread-overflow on 8760+
  // hourly series (browsers cap arg count near 65k).
  const peak = safeMax(vals)
  const avg = vals.reduce((a, b) => a + b, 0) / (vals.length || 1)
  const min = safeMin(vals)

  return (
    <div className="flex flex-col h-full gap-2">
      {/* Stat pills */}
      <div className="flex gap-3 px-1 shrink-0">
        {([['Peak', peak], ['Avg', avg], ['Min', min]] as [string, number][]).map(([label, v]) => (
          <div key={label} className="flex flex-col items-center">
            <span className="text-[9px] text-muted uppercase tracking-wide">{label}</span>
            <span className="text-xs font-mono text-text">{fmtMW(v)}</span>
          </div>
        ))}
        <div className="ml-auto flex flex-col items-end">
          <span className="text-[9px] text-muted uppercase tracking-wide">Points</span>
          <span className="text-xs font-mono text-text">{vals.length}</span>
        </div>
      </div>

      {/* Chart */}
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 20, left: 44 }}>
            <defs>
              <linearGradient id="loadGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={ACCENT} stopOpacity={0.25} />
                <stop offset="95%" stopColor={ACCENT} stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis
              dataKey="ts"
              tick={{ fill: '#9ca3af', fontSize: 9 }}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fill: '#9ca3af', fontSize: 10 }}
              tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(1)}G` : `${v}`}
              width={40}
            />
            <Tooltip
              contentStyle={{ background: 'var(--color-panel)', border: '1px solid var(--color-border)', color: 'var(--color-text)', fontSize: 11 }}
              formatter={(v: number) => [fmtMW(v), 'p_set']}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={ACCENT}
              strokeWidth={1.5}
              fill="url(#loadGrad)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── Main ───────────────────────────────────────────────────────────────────────

export default function LoadProfileManager() {
  const qc = useQueryClient()
  const [selectedLoad, setSelectedLoad] = useState<string | null>(null)

  const { data: loads = [] } = useQuery({
    queryKey: ['loads'],
    queryFn: networkApi.getLoads,
  })

  const { data: profiles = {} } = useQuery({
    queryKey: ['load_profiles'],
    queryFn: networkApi.getLoadProfiles,
  })

  const uploadMut = useMutation({
    mutationFn: (file: File) => networkApi.uploadLoadProfile(file),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['load_profiles'] })
      qc.invalidateQueries({ queryKey: ['timeseries', 'loads', 'p_set'] })
      const n = result.matched.length
      const u = result.unmatched.length
      const msg = `Load profile uploaded: ${result.rows} rows for ${n} load${n !== 1 ? 's' : ''}` +
        (u ? ` (${u} unmatched column${u !== 1 ? 's' : ''} ignored)` : '')
      toast.success(msg)
      appLog('INFO', msg)
      if (u > 0) appLog('WARN', `Unmatched columns ignored: ${result.unmatched.join(', ')}`)
      if (result.matched.length > 0 && !selectedLoad) {
        setSelectedLoad(result.matched[0])
      }
    },
    onError: (e: Error) => {
      toast.error(e.message)
      appLog('ERROR', `Load profile upload failed: ${e.message}`)
    },
  })

  const handleDownloadTemplate = async () => {
    try {
      appLog('INFO', 'Downloading load profile template…')
      const blob = await networkApi.downloadLoadTemplate()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'load_profiles_template.xlsx'
      a.click()
      URL.revokeObjectURL(url)
      appLog('INFO', 'Template downloaded: load_profiles_template.xlsx')
    } catch {
      toast.error('Template download failed')
      appLog('ERROR', 'Template download failed')
    }
  }

  const loadsWithProfile = (loads as Load[]).filter(l => profiles[l.name]?.has_profile)
  const loadsWithout = (loads as Load[]).filter(l => !profiles[l.name]?.has_profile)

  const selectedMeta = selectedLoad ? profiles[selectedLoad] : undefined

  return (
    <div className="flex h-full overflow-hidden">

      {/* ── Left: load list ────────────────────────────────────────── */}
      <div className="w-52 shrink-0 border-r border-border flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-border shrink-0">
          <p className="text-xs font-semibold text-text">Loads</p>
          <p className="text-[10px] text-muted mt-0.5">
            {loadsWithProfile.length}/{(loads as Load[]).length} with profile
          </p>
        </div>
        <div className="flex-1 overflow-y-auto">
          {(loads as Load[]).length === 0 && (
            <p className="p-3 text-xs text-muted">No loads in network.</p>
          )}
          {loadsWithProfile.map(l => (
            <LoadListItem
              key={l.name}
              load={l}
              meta={profiles[l.name]}
              selected={selectedLoad === l.name}
              onClick={() => setSelectedLoad(l.name)}
            />
          ))}
          {loadsWithout.length > 0 && loadsWithProfile.length > 0 && (
            <div className="px-3 py-1 border-t border-border/40">
              <p className="text-[10px] text-muted font-semibold uppercase tracking-wide">No profile</p>
            </div>
          )}
          {loadsWithout.map(l => (
            <LoadListItem
              key={l.name}
              load={l}
              meta={profiles[l.name]}
              selected={selectedLoad === l.name}
              onClick={() => setSelectedLoad(l.name)}
            />
          ))}
        </div>
      </div>

      {/* ── Centre: upload + info ───────────────────────────────────── */}
      <div className="w-80 shrink-0 flex flex-col overflow-y-auto p-4 gap-4 border-r border-border">

        {/* Actions row */}
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-text">Load Profiles</h2>
          <div className="flex-1" />
          <button
            onClick={handleDownloadTemplate}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border rounded text-xs text-muted hover:text-accent hover:border-accent transition-colors"
          >
            <FileSpreadsheet size={13} />
            Download Template
          </button>
        </div>

        {/* Upload zone */}
        <UploadZone onFile={f => uploadMut.mutate(f)} />
        {uploadMut.isPending && (
          <p className="text-xs text-accent animate-pulse text-center">Uploading…</p>
        )}

        {/* Format hint */}
        <div className="border border-border/60 rounded-lg p-3 bg-panel text-xs text-muted space-y-1">
          <p className="font-semibold text-text text-[11px]">Expected file format</p>
          <p>• First column: timestamps (e.g. <span className="font-mono">2024-01-01 00:00</span>)</p>
          <p>• Remaining columns: one per load, named exactly as the load name</p>
          <p>• Values: active power demand in <span className="font-mono">MW</span></p>
          <p>• Formats accepted: <span className="font-mono">.xlsx</span>, <span className="font-mono">.xls</span>, <span className="font-mono">.csv</span></p>
          <p className="pt-1 text-[10px]">
            The template provides a 1-week hourly sinusoidal profile (Mon 00:00 → Sun 23:00)
            scaled between 0 and each load's current static <span className="font-mono">p_set</span>.
          </p>
        </div>

        {/* Summary table when data exists */}
        {loadsWithProfile.length > 0 && (
          <div>
            <p className="text-[11px] font-semibold text-text mb-2">Uploaded profiles</p>
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="border-b border-border bg-panel">
                  {['Load', 'Bus', 'Points', 'Start', 'End', 'Peak', 'Avg'].map(h => (
                    <th key={h} className="text-left px-2 py-1.5 text-[10px] font-semibold text-muted uppercase tracking-wide whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loadsWithProfile.map((l, i) => {
                  const m = profiles[l.name]
                  if (!m?.has_profile) return null
                  return (
                    <tr
                      key={l.name}
                      onClick={() => setSelectedLoad(l.name)}
                      className={`border-b border-border/40 cursor-pointer transition-colors
                        ${selectedLoad === l.name ? 'bg-accent/10' : i % 2 === 0 ? 'bg-bg hover:bg-accent/5' : 'bg-panel hover:bg-accent/5'}`}
                    >
                      <td className="px-2 py-1 font-semibold text-text">{l.name}</td>
                      <td className="px-2 py-1 text-muted">{l.bus}</td>
                      <td className="px-2 py-1 font-mono">{m.rows}</td>
                      <td className="px-2 py-1 font-mono text-[10px]">{m.start?.slice(0, 10) ?? '–'}</td>
                      <td className="px-2 py-1 font-mono text-[10px]">{m.end?.slice(0, 10) ?? '–'}</td>
                      <td className="px-2 py-1 font-mono">{fmtMW(m.peak)}</td>
                      <td className="px-2 py-1 font-mono">{fmtMW(m.mean)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Right: chart panel ─────────────────────────────────────── */}
      <div className="flex-1 min-w-0 border-l border-border flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-border shrink-0 flex items-center gap-2">
          <Download size={12} className="text-muted" />
          <span className="text-xs font-semibold text-text truncate">
            {selectedLoad ?? 'Select a load'}
          </span>
          {selectedMeta?.has_profile && (
            <span className="ml-auto text-[9px] text-success font-mono shrink-0">
              {selectedMeta.rows} pts
            </span>
          )}
        </div>

        <div className="flex-1 min-h-0 p-3">
          {selectedLoad
            ? <ProfileChart loadName={selectedLoad} />
            : (
              <div className="flex flex-col items-center justify-center h-full gap-2 text-muted">
                <p className="text-xs">Click a load to preview its profile</p>
              </div>
            )
          }
        </div>
      </div>
    </div>
  )
}
