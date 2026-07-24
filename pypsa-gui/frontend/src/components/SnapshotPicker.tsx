import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Eye, EyeOff, ChevronLeft, ChevronRight, Play, Pause, Layers } from 'lucide-react'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { simulationApi, resultsApi } from '../api/simulation'

// ── Snapshot picker / playback bar ───────────────────────────────────────────
// A floating bar pinned to the bottom-center of the canvas (blank schematic OR
// satellite/hybrid map) with classic video-player controls so users can scrub
// or auto-step through n.snapshots while watching the bus / line annotations
// animate in real time.
//
// Controls (left → right):
//   • Eye toggle     — show / hide the per-snapshot annotations
//   • Period selector — multi-period only: pick which investment year to view
//   • Step backward  — previous snapshot
//   • Play / Pause   — auto-advance at the chosen speed
//   • Step forward   — next snapshot
//   • Speed selector — 0.25× / 0.5× / 1× / 2× / 4× of base interval (700 ms)
//   • Range slider   — scrub through the active snapshot window
//   • Timestamp      — current ISO + (idx+1 / count)
//
// Multi-period: when n.snapshots is a MultiIndex the backend ships a parallel
// `periods` array. The picker then constrains the scrubber to ONE period's
// contiguous index slice — the user selects the investment year, then steps
// through its hourly timesteps. Flat (single-period) networks scrub the whole
// range as before.
//
// Hidden when:
//   • the network has zero snapshots
//   • no simulation has been solved (status.condition is null and no solve_time)
//   • a slide panel or the command palette is open

const BASE_TICK_MS = 700  // 1× speed; full slider span ≈ snapshot_count × 700 ms
const SPEEDS = [0.25, 0.5, 1, 2, 4] as const

export default function SnapshotPicker() {
  const {
    resultsOverlayEnabled, setResultsOverlay,
    resultsSnapshotIdx, setResultsSnapshotIdx, activeSlidePanel,
    resultSource, paletteMode, currentProject,
  } = useUIStore()
  const { data: snap } = useQuery({
    queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots,
    staleTime: 5_000,
  })
  const { data: status } = useQuery({
    queryKey: nk(currentProject, 'simulationStatus'), queryFn: simulationApi.getStatus,
    staleTime: 5_000,
  })
  // AC PF status — when available, decorate the timestamp with a ✓ / ✗
  // glyph reflecting the current snapshot's convergence so users see
  // mismatches in the playback bar without leaving the canvas.
  const { data: acPfStatus } = useQuery({
    queryKey: nk(currentProject, 'results', 'ac_pf', 'status'), queryFn: resultsApi.getAcPfStatus,
  })

  const count = snap?.count ?? 0

  // ── Multi-period awareness ─────────────────────────────────────────────
  // Derive each investment period's contiguous [start, end] index range from
  // the backend's parallel `periods` array (present only for MultiIndex
  // snapshots). null ⇒ flat single-period network.
  const periodInfo = useMemo(() => {
    const ps = snap?.periods
    if (!ps || ps.length === 0) return null
    const order: Array<number | string> = []
    const ranges = new Map<string, { period: number | string; start: number; end: number }>()
    ps.forEach((p, i) => {
      const key = String(p)
      const r = ranges.get(key)
      if (r) { r.end = i }
      else { ranges.set(key, { period: p, start: i, end: i }); order.push(p) }
    })
    return { order, ranges }
  }, [snap?.periods])

  // Which period the scrubber is locked to (multi-period only). Defaults to the
  // first period when multi-period data appears; cleared when the network goes
  // back to flat snapshots or rebuilds with a different period set.
  const [selectedPeriodKey, setSelectedPeriodKey] = useState<string | null>(null)
  useEffect(() => {
    if (periodInfo) {
      if (selectedPeriodKey === null || !periodInfo.ranges.has(selectedPeriodKey)) {
        setSelectedPeriodKey(String(periodInfo.order[0]))
      }
    } else if (selectedPeriodKey !== null) {
      setSelectedPeriodKey(null)
    }
  }, [periodInfo, selectedPeriodKey])

  // The index window the scrubber operates within: the selected period's slice
  // when multi-period, otherwise the whole snapshot range.
  const activeRange = useMemo(() => {
    if (periodInfo && selectedPeriodKey) {
      const r = periodInfo.ranges.get(selectedPeriodKey)
      if (r) return r
    }
    return { period: null as number | string | null, start: 0, end: Math.max(0, count - 1) }
  }, [periodInfo, selectedPeriodKey, count])

  // Clamp the persisted index into the active window whenever it (or the
  // snapshot set) changes underneath us.
  useEffect(() => {
    if (count === 0) return
    if (resultsSnapshotIdx < activeRange.start) setResultsSnapshotIdx(activeRange.start)
    else if (resultsSnapshotIdx > activeRange.end) setResultsSnapshotIdx(activeRange.end)
  }, [count, resultsSnapshotIdx, setResultsSnapshotIdx, activeRange.start, activeRange.end])

  // ── Playback state ─────────────────────────────────────────────────────
  // `playing` toggles a setInterval that advances idx every BASE_TICK_MS / speed.
  // We pause automatically when reaching the end of the active window to avoid
  // wrap-around confusion; user can press Play again to restart.
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState<typeof SPEEDS[number]>(1)
  // Track idx via a ref so the interval reads the freshest value instead of
  // closing over a stale state at the moment setInterval was registered.
  const idxRef = useRef(resultsSnapshotIdx)
  useEffect(() => { idxRef.current = resultsSnapshotIdx }, [resultsSnapshotIdx])

  useEffect(() => {
    if (!playing) return
    const tick = BASE_TICK_MS / speed
    const id = window.setInterval(() => {
      const next = idxRef.current + 1
      if (next > activeRange.end) { setPlaying(false); return }
      setResultsSnapshotIdx(next)
    }, tick)
    return () => window.clearInterval(id)
  }, [playing, speed, activeRange.end, setResultsSnapshotIdx])

  // ── Visibility gating ──────────────────────────────────────────────────
  if (count === 0) return null
  // Hide behind any open slide panel (Time Series / Results / Solver / Horizon)
  // so the panel's content area isn't blocked by the floating playback bar.
  if (activeSlidePanel) return null
  // Same for the command palette (Ctrl/Cmd+K / Ctrl/Cmd+P). The picker sits
  // at z-[900] (above Leaflet's z-800 controls), which would otherwise float
  // on top of the palette's z-[500] overlay.
  if (paletteMode !== null) return null

  const idx = Math.max(activeRange.start, Math.min(resultsSnapshotIdx, activeRange.end))
  const stamp = snap?.snapshots?.[idx] ?? ''
  const hasResults = !!status?.condition && status.solve_time != null
  const windowLen = activeRange.end - activeRange.start + 1
  const localIdx = idx - activeRange.start

  // Period-aware AC PF convergence lookup. The legacy `converged_per_snapshot`
  // map keys by ISO timestamp alone — ambiguous on multi-period networks
  // where the same operational hour appears under every investment period
  // (CLAUDE.md flags this as a known footgun). The newer `converged_list`
  // carries `{snapshot, period, ok}` and disambiguates by matching against
  // the currently-selected period. Fall back to the legacy map only on
  // flat single-period networks where keys are unique.
  const isSnapshotConverged = (() => {
    if (!acPfStatus?.available || !stamp) return null
    const selectedPeriod = activeRange.period
    if (selectedPeriod != null && acPfStatus.converged_list?.length) {
      const periodStr = String(selectedPeriod)
      const entry = acPfStatus.converged_list.find(e =>
        e.snapshot === stamp && String(e.period ?? '') === periodStr
      )
      if (entry) return entry.ok
      // Period+snapshot pair missing from list → treat as "no record",
      // hide the badge rather than fall through to ambiguous legacy map.
      return null
    }
    // Flat network or list unavailable — legacy map is safe to consult.
    if (acPfStatus.converged_per_snapshot && stamp in acPfStatus.converged_per_snapshot) {
      return acPfStatus.converged_per_snapshot[stamp]
    }
    return null
  })()

  const togglePlay = () => {
    if (!resultsOverlayEnabled) setResultsOverlay(true)
    if (idx >= activeRange.end) setResultsSnapshotIdx(activeRange.start)  // restart
    setPlaying(p => !p)
  }

  return (
    <div
      className="absolute z-[900] flex items-center gap-1 bg-bg border border-border rounded-md shadow-lg px-1.5 py-1 text-[10px]"
      style={{ bottom: 10, left: '50%', transform: 'translateX(-50%)', maxWidth: 'calc(100vw - 360px)' }}
    >
      <button
        type="button"
        onClick={() => setResultsOverlay(!resultsOverlayEnabled)}
        title={resultsOverlayEnabled ? 'Hide results overlay' : 'Show results overlay'}
        disabled={!hasResults}
        className={`flex items-center gap-1 px-1.5 py-0.5 rounded transition-colors disabled:opacity-40 ${
          resultsOverlayEnabled ? 'bg-accent text-white' : 'text-muted hover:text-text hover:bg-panel'
        }`}
      >
        {resultsOverlayEnabled ? <Eye size={10} /> : <EyeOff size={10} />}
        Results
      </button>

      {!hasResults ? (
        <span className="text-[10px] text-muted italic px-1">Run a simulation to enable</span>
      ) : (
        <>
          <span className="w-px h-4 bg-border mx-0.5" />

          {/* Period selector — multi-period networks only. Selecting a year
              jumps the scrubber to that period's first snapshot and locks the
              slider to its slice. */}
          {periodInfo && selectedPeriodKey && (
            <>
              <Layers size={10} className="text-muted" />
              <select
                value={selectedPeriodKey}
                onChange={(e) => {
                  setPlaying(false)
                  const key = e.target.value
                  setSelectedPeriodKey(key)
                  const r = periodInfo.ranges.get(key)
                  if (r) setResultsSnapshotIdx(r.start)
                }}
                title="Investment period to display"
                className="text-[10px] border border-border rounded bg-bg px-0.5 py-0"
              >
                {periodInfo.order.map(p => (
                  <option key={String(p)} value={String(p)}>{p}</option>
                ))}
              </select>
              <span className="w-px h-4 bg-border mx-0.5" />
            </>
          )}

          <button
            type="button"
            onClick={() => { setPlaying(false); setResultsSnapshotIdx(Math.max(activeRange.start, idx - 1)) }}
            disabled={idx <= activeRange.start}
            className="text-muted hover:text-accent disabled:opacity-30 p-0.5"
            title="Previous snapshot"
          ><ChevronLeft size={12} /></button>

          <button
            type="button"
            onClick={togglePlay}
            title={playing ? 'Pause' : 'Play through snapshots'}
            className={`flex items-center justify-center w-5 h-5 rounded-full transition-colors ${
              playing ? 'bg-warn text-white' : 'bg-accent text-white hover:bg-accent/90'
            }`}
          >
            {playing ? <Pause size={10} /> : <Play size={10} />}
          </button>

          <button
            type="button"
            onClick={() => { setPlaying(false); setResultsSnapshotIdx(Math.min(activeRange.end, idx + 1)) }}
            disabled={idx >= activeRange.end}
            className="text-muted hover:text-accent disabled:opacity-30 p-0.5"
            title="Next snapshot"
          ><ChevronRight size={12} /></button>

          <select
            value={speed}
            onChange={e => setSpeed(Number(e.target.value) as typeof SPEEDS[number])}
            title="Playback speed"
            className="text-[10px] border border-border rounded bg-bg px-0.5 py-0"
          >
            {SPEEDS.map(s => <option key={s} value={s}>{s}×</option>)}
          </select>

          <span className="w-px h-4 bg-border mx-0.5" />

          <input
            type="range"
            min={activeRange.start}
            max={activeRange.end}
            step={1}
            value={idx}
            onChange={(e) => { setPlaying(false); setResultsSnapshotIdx(Number(e.target.value)) }}
            disabled={!resultsOverlayEnabled}
            style={{ width: 140, height: 12 }}
          />

          <span className="font-mono text-[10px] text-muted whitespace-nowrap">
            {stamp ? stamp.slice(5, 16).replace('T', ' ') : '—'}
            <span className="ml-1 opacity-70">({localIdx + 1}/{windowLen})</span>
            {/* AC PF convergence glyph for the current snapshot+period.
                Only meaningful when the user is viewing the AC PF result
                source — under LOPF the LP always "converges" (a different
                concept). `isSnapshotConverged` consults the period-aware
                `converged_list` first; null means "no record" (hide badge). */}
            {resultSource === 'ac_pf' && isSnapshotConverged !== null && (
              isSnapshotConverged === false ? (
                <span className="ml-1.5 text-danger font-bold" title="AC PF did not converge for this snapshot">✗</span>
              ) : (
                <span className="ml-1.5 text-success font-bold" title="AC PF converged for this snapshot">✓</span>
              )
            )}
          </span>
        </>
      )}
    </div>
  )
}
