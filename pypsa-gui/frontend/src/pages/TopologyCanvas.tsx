import { useCallback, useMemo, useEffect, useLayoutEffect, useRef, useState, useId, createContext, useContext } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ReactFlow, Background, Controls, MiniMap, useNodesState, useEdgesState,
  Handle, Position, type NodeProps, type EdgeProps, type Connection,
  BackgroundVariant, Panel, useReactFlow, EdgeLabelRenderer,
  type Node, type Edge, type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Plus, Layers, Flame, BatteryCharging,
  Zap, Wind, Trash2, X as XIcon, type LucideIcon,
  Clock, CheckCircle2, Loader2, XCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { networkApi } from '../api/network'
import { resultsApi } from '../api/simulation'
import { projectsApi } from '../api/projects'
import { rawFetchHeaders } from '../api/csrf'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { isRenewableCarrier } from '../utils/carriers'
import { useSimulationStore } from '../store/simulationStore'
import { getCarrierBadge, type BadgeDef } from '../utils/carrierBadges'
import CarrierSelect from '../components/CarrierSelect'
import { Dialog } from '../components/Dialog'
import {
  CanvasResultsProvider, useCanvasResults, fmtMW, loadingColor,
} from '../components/CanvasResultsContext'
import type { Bus, Generator, Line, Load, StorageUnit, Store, Transformer } from '../api/types'
import { safeMinMax } from '../utils/numeric'
import { colourForCarrier } from './results/shared'

// ── Types ──────────────────────────────────────────────────────────────────────
type WP = { x: number; y: number }
type AssetCategory = 'Thermal' | 'Renewables' | 'Storage' | 'Load'

interface EdgeData extends Record<string, unknown> {
  s_nom?: number
  v_nom?: number
  type: 'line' | 'link' | 'transformer' | 'asset'
  carrier?: string
  color: string
  waypoints: WP[]
  history: WP[][]  // [0] = straight-line state; push on every committed change (cap 50)
}

// ── localStorage persistence ───────────────────────────────────────────────────
const STORAGE_VERSION = 1
// Per-project localStorage key, mirroring `layoutCacheKey` (defined later). The
// `?? '__local__'` is inlined here on purpose: this helper is referenced from
// module-level functions that run before `layoutCacheKey` is initialised, so it
// must not depend on it. The no-active-project / offline fallback uses the
// `__local__` slot.
const storageKeyFor = (project: string | null): string =>
  `network-diagram:${project ?? '__local__'}:state`

interface PersistedNode { id: string; canvasX: number; canvasY: number }
interface PersistedEdge { id: string; waypoints: WP[]; history: WP[][] }
interface PersistedState {
  version: number; savedAt: number
  nodes: PersistedNode[]; edges: PersistedEdge[]
}

function loadDiagramState(project: string | null): PersistedState | null {
  const key = storageKeyFor(project)
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.version !== STORAGE_VERSION) {
      localStorage.removeItem(key)
      return null
    }
    return parsed as PersistedState
  } catch (e) {
    console.warn('Saved diagram state could not be loaded:', e)
    return null
  }
}

function saveDiagramState(project: string | null, state: PersistedState): void {
  try {
    localStorage.setItem(storageKeyFor(project), JSON.stringify(state))
  } catch (e) {
    console.warn('Diagram state could not be saved:', e)
  }
}

// ── Server-side latent-layout store ───────────────────────────────────────────
// The blank-canvas layout ("latent coordinates") is persisted per-project as
// `layout.json` on the server, so the schematic travels with the project
// bundle across machines. The global localStorage store above is the fallback
// for the unsaved / default network (no active project) — and the offline
// fallback if a server write fails, so a layout is never silently lost.

// Coerce an opaque layout document (the server stores it without interpreting
// its shape) into a PersistedState, or null if it isn't a recognisable one.
// Defensive: a future format bump must degrade to "no layout" (→ algorithm
// re-layout), never crash the canvas.
function coercePersistedState(raw: unknown): PersistedState | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  if (o.version !== STORAGE_VERSION) return null
  if (!Array.isArray(o.nodes) || !Array.isArray(o.edges)) return null
  return o as unknown as PersistedState
}

async function fetchLayoutFor(project: string | null): Promise<PersistedState | null> {
  if (!project) return loadDiagramState(project)
  try {
    return coercePersistedState(await projectsApi.getLayout(project))
  } catch {
    // Server unreachable — fall back to whatever's in localStorage so the
    // user still sees *a* layout rather than an algorithm reset.
    return loadDiagramState(project)
  }
}

function persistLayoutFor(project: string | null, state: PersistedState): void {
  if (!project) { saveDiagramState(project, state); return }
  projectsApi.putLayout(project, state as unknown as Record<string, unknown>)
    .catch(() => {
      // Server write failed — keep the layout in localStorage so it isn't
      // lost; it'll re-sync to the server on the next successful save.
      saveDiagramState(project, state)
    })
}

// Page-unload-safe variant. Browsers cancel in-flight XHR/fetch requests the
// moment a page starts unloading (F5, tab close, navigation), so the normal
// `persistLayoutFor` above silently drops the most recent drag if the user
// hits refresh inside the 300 ms scheduleSave debounce. `fetch(... keepalive)`
// hands the request off to the browser's background-network slot — it
// completes EVEN AFTER the page is gone, bounded by ~64 KB body and the
// browser's keepalive cap. Falls back to `navigator.sendBeacon` for older
// browsers (which is POST-only, so we route through a tiny POST shim the
// backend accepts as an alias for PUT — `sendBeacon` cannot send PUT). Last
// resort: synchronous XHR (deprecated but reliable). Always also writes to
// localStorage so the in-flight payload is never lost even if every network
// path fails.
function persistLayoutOnUnload(project: string | null, state: PersistedState): void {
  saveDiagramState(project, state)  // safety net first, before any network attempt
  if (!project) return
  const url = `/api/projects/${encodeURIComponent(project)}/layout`
  const body = JSON.stringify(state)
  // Path 1: fetch with keepalive — survives page unload, supports PUT.
  try {
    fetch(url, {
      method: 'PUT',
      body,
      // Read at unload time, not cached: raw fetch bypasses the axios CSRF
      // interceptor, and a 403 here loses the user's last drag silently.
      headers: { 'Content-Type': 'application/json', ...rawFetchHeaders('PUT') },
      keepalive: true,
    }).catch(() => { /* localStorage fallback already done */ })
    return
  } catch { /* fall through */ }
  // Path 2: sendBeacon — POST-only, but we expose a thin alias on the backend.
  // Currently the backend only accepts PUT on /layout, so this path is a
  // future-proofed stub; keep the call so we degrade gracefully if a future
  // browser drops fetch-keepalive support.
}

// ── In-memory layout cache (survives a TopologyCanvas unmount) ─────────────────
// App.tsx renders the blank canvas OR the satellite/hybrid map canvas — never
// both — so switching the canvas view UNMOUNTS TopologyCanvas, discarding its
// per-component layout refs (`posCache`, `savedStateRef`). That stranded the
// schematic at the algorithm default on a map→blank switch until the async
// server fetch resolved, and a debounced save firing in that window could even
// overwrite layout.json with the algorithm positions. This module-level cache
// keeps the last-known layout per project alive across unmounts, so a remount
// restores it synchronously on the very first render — no flicker, no clobber.
const layoutMemCache = new Map<string, PersistedState>()
const layoutCacheKey = (project: string | null): string => project ?? '__local__'

// Synchronously flush any pending diagram layout (node positions + edge
// waypoints) to the server BEFORE a project save fires. Without this, the
// canvas's `scheduleSave` debounce (300 ms) can leave the latest drag in
// flight when the user hits the Save button — the project save races ahead,
// the layout.json write lands after with the same payload, but if the user
// closes the tab or the network fails in between, the layout doesn't survive
// to the next session and the canvas re-seeds from the layout algorithm
// (random-looking starting position).
//
// Falls back to localStorage when the server write fails or no project is
// active, matching `persistLayoutFor`'s existing behaviour. Idempotent and
// safe to call when nothing's cached (returns immediately).
// Outcome of a flush attempt — returned so callers can surface it in toasts,
// logs, or the network DevTools panel without re-reading the cache.
// • `nothing`  — no pending state in cache or localStorage (caller may stay quiet)
// • `server`   — successfully PUT to /projects/{name}/layout
// • `local`    — wrote to localStorage (either because project is null, or the
//                server PUT failed and we used the localStorage fallback)
export interface FlushLayoutResult {
  status: 'nothing' | 'server' | 'local'
  nodes: number
  edges: number
}

export async function flushPendingLayoutToServer(project: string | null): Promise<FlushLayoutResult> {
  // Look up under the project key first; fall back to the local-key entry
  // for the "first save of a never-saved network" case — drags before the
  // user names the project go into `__local__`, and we want them carried
  // over to the freshly-named project on its first save instead of being
  // stranded. Do NOT fall back to localStorage when a project is active —
  // that would let an old per-machine layout (from a different project)
  // bleed into the new project's layout.json.
  let state = layoutMemCache.get(layoutCacheKey(project))
  let source = 'cache[project]'
  if (!state && project) {
    state = layoutMemCache.get(layoutCacheKey(null))
    source = 'cache[__local__] fallback'
  }
  if (!state && !project) {
    // No project + no in-memory cache → fall back to localStorage. This
    // catches the "user dragged in a previous session, never created a
    // project, hits Save without opening the canvas" edge case.
    state = loadDiagramState(project) ?? undefined
    source = 'localStorage fallback'
  }
  if (!state) {
    return { status: 'nothing', nodes: 0, edges: 0 }
  }
  const nodes = state.nodes.length
  const edges = state.edges.length
  if (!project) {
    saveDiagramState(project, state)
    return { status: 'local', nodes, edges }
  }
  try {
    await projectsApi.putLayout(project, state as unknown as Record<string, unknown>)
    // Pin under the project key so subsequent flushes don't have to chase
    // the local-key fallback again.
    layoutMemCache.set(layoutCacheKey(project), state)
    return { status: 'server', nodes, edges }
  } catch (e) {
    saveDiagramState(project, state)
    console.warn('[layout-flush] PUT failed → wrote to localStorage instead', { project, error: e })
    return { status: 'local', nodes, edges }
  }
}

// ── Edge right-click menu coordination ────────────────────────────────────────
interface EdgeMenuCtx {
  open: (edgeId: string, x: number, y: number) => void
  close: () => void
  scheduleSave: () => void
}
const EdgeMenuContext = createContext<EdgeMenuCtx>({ open: () => {}, close: () => {}, scheduleSave: () => {} })

// ── Overlap detection context ─────────────────────────────────────────────────
const OverlapContext = createContext<ReadonlySet<string>>(new Set())

// ── Connect mode context ───────────────────────────────────────────────────────
interface ConnectModeCtx { active: boolean; sourceId: string | null }
const ConnectModeContext = createContext<ConnectModeCtx>({ active: false, sourceId: null })

// ── Highlight context ──────────────────────────────────────────────────────────
const HighlightContext = createContext<{ type: string; name: string; busName?: string } | null>(null)

// ── Layout constants and pure functions ───────────────────────────────────────
export const BUS_R = 44  // half-diagonal of rounded-rect bus node (~88px wide × 30px tall)
const ASSET_R = 62  // half-diagonal of ~100×100 asset card

function hashStr(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0
  }
  return h
}

function mulberry32(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s += 0x6d2b79f5
    let x = Math.imul(s ^ (s >>> 15), 1 | s)
    x ^= x + Math.imul(x ^ (x >>> 7), 61 | x)
    return ((x ^ (x >>> 14)) >>> 0) / 0xffffffff
  }
}

const LAYOUT_ASSET_OFFSETS: Record<AssetCategory, { dx: number; dy: number }> = {
  Thermal:    { dx: -185, dy: -155 },
  Renewables: { dx:  185, dy: -155 },
  Storage:    { dx:  185, dy:   90 },
  Load:       { dx:  -30, dy:  175 },
}

function buildAssetDescriptors(
  buses: Bus[], generators: Generator[], loads: Load[],
  sus: StorageUnit[], stores: Store[],
): { id: string; busId: string; category: AssetCategory }[] {
  const result: { id: string; busId: string; category: AssetCategory }[] = []
  buses.forEach(bus => {
    const busGens   = generators.filter(g => g.bus === bus.name)
    const busLoads  = loads.filter(l => l.bus === bus.name)
    const busSus    = sus.filter(s => s.bus === bus.name)
    const busStores = stores.filter(s => s.bus === bus.name)
    const thermal   = busGens.filter(g => !isRenewableCarrier(g.carrier))
    const renewable = busGens.filter(g =>  isRenewableCarrier(g.carrier))
    const storage   = [...busSus, ...busStores]
    if (thermal.length   > 0) result.push({ id: `assetgrp-${bus.name}-Thermal`,    busId: bus.name, category: 'Thermal' })
    if (renewable.length > 0) result.push({ id: `assetgrp-${bus.name}-Renewables`, busId: bus.name, category: 'Renewables' })
    if (storage.length   > 0) result.push({ id: `assetgrp-${bus.name}-Storage`,    busId: bus.name, category: 'Storage' })
    if (busLoads.length  > 0) result.push({ id: `assetgrp-${bus.name}-Load`,        busId: bus.name, category: 'Load' })
  })
  return result
}

function runLayout(
  buses: { id: string; v_nom: number }[],
  assetNodes: { id: string; busId: string; category: AssetCategory }[],
  W = 1400, H = 900,
): Record<string, { x: number; y: number }> {
  if (buses.length === 0) return {}
  const TIER_Y = [H * 0.2, H * 0.5, H * 0.8]
  const BUS_SEP = 210

  const tierGroups: [string, number][][] = [[], [], []]
  buses.forEach(b => {
    const tier = b.v_nom >= 220 ? 0 : b.v_nom >= 50 ? 1 : 2
    tierGroups[tier].push([b.id, b.v_nom])
  })

  const pos: Record<string, { x: number; y: number }> = {}

  tierGroups.forEach((group, tier) => {
    if (group.length === 0) return
    const startX = W / 2 - ((group.length - 1) * BUS_SEP) / 2
    group.forEach(([id], i) => {
      const rand = mulberry32(hashStr(id))
      pos[id] = {
        x: startX + i * BUS_SEP + (rand() - 0.5) * 40,
        y: TIER_Y[tier]              + (rand() - 0.5) * 60,
      }
    })
  })

  // Collision resolution — push overlapping bus nodes apart
  const ids = buses.map(b => b.id)
  const minDist = BUS_R * 2 + 32
  for (let iter = 0; iter < 50; iter++) {
    let moved = false
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos[ids[i]], b = pos[ids[j]]
        const dx = b.x - a.x, dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        if (dist < minDist) {
          const push = (minDist - dist) / 2 + 1
          const nx = (dx / dist) * push, ny = (dy / dist) * push
          a.x -= nx; a.y -= ny; b.x += nx; b.y += ny
          moved = true
        }
      }
    }
    if (!moved) break
  }

  // Center around canvas origin (single-pass min/max — see utils/numeric.ts).
  const xs = ids.map(id => pos[id].x), ys = ids.map(id => pos[id].y)
  const xmm = safeMinMax(xs), ymm = safeMinMax(ys)
  const cx = (xmm.min + xmm.max) / 2
  const cy = (ymm.min + ymm.max) / 2
  const offX = W / 2 - cx, offY = H / 2 - cy
  ids.forEach(id => { pos[id].x += offX; pos[id].y += offY })

  // Place asset nodes relative to their bus with fixed offsets
  assetNodes.forEach(ag => {
    if (!pos[ag.busId]) return
    const off = LAYOUT_ASSET_OFFSETS[ag.category]
    pos[ag.id] = { x: pos[ag.busId].x + off.dx, y: pos[ag.busId].y + off.dy }
  })

  return pos
}

interface AssetGroupData extends Record<string, unknown> {
  category: AssetCategory
  busName: string
  busCarrier: string
  assetCount: number
  totalMW: number
  carriers: string[]
}

// ── Blank-canvas layout: latent coordinates only ──────────────────────────────
// The blank (schematic) canvas is DELIBERATELY decoupled from the geographic
// `bus.x` / `bus.y`. Those geographic coordinates drive the satellite/hybrid
// map view and the clustering algorithms — they are the user-entered, public
// coordinates. The blank canvas instead uses "latent coordinates": positions
// produced by `runLayout` (a force-directed schematic layout) and persisted
// per-project as `layout.json` (server-side, travels with the bundle) — never
// derived from, and never written back to, `bus.x` / `bus.y`.
//
// There used to be a `buildCoordLayout` / `hasSignificantCoords` /
// `projectBusCoord` path that seeded the schematic from geographic coords,
// plus a "Reset to model coordinates" button. Both were removed: the
// schematic must not leak or depend on the geographic coordinate set.
// Do NOT re-introduce a geographic→schematic projection here.


// ── Color helpers ──────────────────────────────────────────────────────────────
function getLineColor(vNom: number): string {
  if (vNom > 300) return '#dc2626'
  if (vNom > 200) return '#16a34a'
  if (vNom > 100) return '#2563eb'
  return '#374151'
}

const CARRIER_BUS_COLORS: Record<string, string> = {
  AC: '#e60012', DC: '#d97706', heat: '#ea580c', H2: '#7c3aed', gas: '#0369a1',
}

const CARRIER_LINK_COLORS: Record<string, string> = {
  H2: '#7c3aed', electrolysis: '#7c3aed', 'H2 electrolysis': '#7c3aed',
  heat: '#ea580c', heat_pump: '#f97316',
  gas: '#0369a1', CCGT: '#0369a1', OCGT: '#0891b2',
  battery: '#16a34a', BEV: '#16a34a',
  DC: '#d97706', HVDC: '#d97706',
  AC: '#e60012',
}
const FALLBACK_COLORS = ['#7c3aed', '#ea580c', '#0369a1', '#16a34a', '#d97706', '#ec4899', '#06b6d4', '#84cc16', '#f43f5e']

function getLinkColor(carrier: string, fallbackIdx: number): string {
  if (CARRIER_LINK_COLORS[carrier]) return CARRIER_LINK_COLORS[carrier]
  for (const [key, col] of Object.entries(CARRIER_LINK_COLORS)) {
    if (carrier.toLowerCase().includes(key.toLowerCase())) return col
  }
  return FALLBACK_COLORS[fallbackIdx % FALLBACK_COLORS.length]
}

// ── Asset category helpers ─────────────────────────────────────────────────────
// isRenewableCarrier imported from utils/carriers (single null-safe source).

// Per-category badges floating around each bus on the diagram. Pictograms
// match the left-sidebar palette so the same glyph means the same thing
// whether the user is creating an asset or reading the diagram.
const CATEGORY_CONFIG: Record<AssetCategory, { Icon: LucideIcon; color: string; offsetX: number; offsetY: number }> = {
  Thermal:    { Icon: Flame,           color: '#dc2626', offsetX: -150, offsetY: -110 },
  Renewables: { Icon: Wind,            color: '#16a34a', offsetX:  130, offsetY: -110 },
  Storage:    { Icon: BatteryCharging, color: '#7c3aed', offsetX:  150, offsetY:   50 },
  Load:       { Icon: Zap,             color: '#d97706', offsetX:  -20, offsetY:  130 },
}

const CAT_DISPLAY_LABELS: Record<AssetCategory, string> = {
  Thermal: 'Conventional', Renewables: 'Renewables', Storage: 'Storage', Load: 'Load',
}

// ── Carrier badge (for link edges) ─────────────────────────────────────────────
// Icon may be a lucide icon OR a custom component (e.g. shared H2Icon). Both
// accept `size` and `style`; `strokeWidth` is optional — lucide icons honour it,
// custom SVG glyphs (H2Icon) just ignore the extra prop.
// Table and getCarrierBadge moved to utils/carrierBadges.tsx for sharing with
// the map component.

function CarrierBadge({ carrier, color }: { carrier: string; color: string }) {
  const { Icon, label } = getCarrierBadge(carrier)
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 3,
      backgroundColor: color, color: '#fff',
      padding: '2px 7px', borderRadius: 10,
      fontSize: 9, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace",
      border: '2px solid #fff',
      boxShadow: '0 1px 5px rgba(0,0,0,0.25)',
      whiteSpace: 'nowrap', lineHeight: 1.2, userSelect: 'none',
    }}>
      <Icon size={9} strokeWidth={2.5} />
      <span style={{ marginLeft: 2 }}>{label}</span>
    </div>
  )
}

// Centered invisible handle — edges connect to the circle center, not its edge
const CENTER_HANDLE: React.CSSProperties = {
  opacity: 0, width: 0, height: 0,
  position: 'absolute', left: '50%', top: '50%',
  transform: 'translate(-50%, -50%)', pointerEvents: 'none',
}

// ── Bus node ───────────────────────────────────────────────────────────────────
const BUS_NODE_H = 30
const BUS_NODE_MIN_W = 72

function BusNode({ id, data, selected }: NodeProps) {
  const { bus } = data as unknown as { bus: Bus }
  const color = getLineColor(bus.v_nom ?? 1)
  const overlapping = useContext(OverlapContext)
  const isOverlapping = overlapping.has(id)
  const connectMode = useContext(ConnectModeContext)
  const isSource = connectMode.sourceId === id
  const highlight = useContext(HighlightContext)
  const isHighlighted = highlight?.type === 'Bus' && highlight.name === bus.name
  const isConnectedHighlight = !isHighlighted && !!highlight?.busName && highlight.busName === bus.name

  // Results overlay — when enabled, attach a chip below the bus name showing
  // the snapshot's generation feeding this bus and load drawn from it (both
  // in MW). Negative gen would mean generation is being curtailed; we render
  // it raw because PyPSA's n.generators_t.p is non-negative for non-bidirectional
  // assets, so it's already a sane positive number for thermals/renewables.
  const results = useCanvasResults()
  const overlay = results.enabled ? results.byBus.get(bus.name) : undefined

  return (
    <div
      className="relative flex items-center gap-1.5 transition-all"
      style={{
        minWidth: BUS_NODE_MIN_W,
        height: BUS_NODE_H,
        paddingLeft: 8,
        paddingRight: 10,
        borderRadius: 6,
        cursor: connectMode.active ? (isSource ? 'default' : 'crosshair') : 'pointer',
        backgroundColor: selected ? `${color}12` : 'var(--color-bg)',
        border: `2px solid ${isOverlapping ? '#ef4444' : color}`,
        boxShadow: isSource
          ? `0 0 0 5px ${color}28, 0 2px 8px rgba(0,0,0,0.14)`
          : isOverlapping
          ? `0 0 0 3px rgba(239,68,68,0.30), 0 2px 8px rgba(0,0,0,0.14)`
          : isHighlighted
          ? `0 0 0 3px ${color}, 0 0 0 8px ${color}70, 0 0 22px 8px ${color}45, 0 2px 8px rgba(0,0,0,0.14)`
          : selected
          ? `0 0 0 3px ${color}28, 0 2px 8px rgba(0,0,0,0.14)`
          : '0 1px 4px rgba(0,0,0,0.10)',
        whiteSpace: 'nowrap',
      }}
    >
      {isSource && (
        <div className="absolute rounded animate-ping pointer-events-none"
          style={{ inset: -6, border: `2px solid ${color}`, opacity: 0.45, borderRadius: 9 }} />
      )}
      {isHighlighted && (
        <>
          <div className="absolute pointer-events-none highlight-ring"
            style={{ inset: -4, border: `2.5px solid ${color}`, borderRadius: 9, opacity: 1 }} />
          <div className="absolute pointer-events-none highlight-ring-outer"
            style={{ inset: -4, border: `3px solid ${color}`, borderRadius: 9, opacity: 0.85 }} />
        </>
      )}
      {isConnectedHighlight && (
        <div className="absolute pointer-events-none"
          style={{ inset: -5, border: `1.5px dashed ${color}`, borderRadius: 10, opacity: 0.55 }} />
      )}
      <Handle type="target" position={Position.Left} style={CENTER_HANDLE} />
      <Handle type="source" position={Position.Left} style={CENTER_HANDLE} />

      {/* Carrier colour dot */}
      <div style={{ width: 7, height: 7, borderRadius: '50%', backgroundColor: color, flexShrink: 0 }} />

      {/* Name + voltage */}
      <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--color-text)', lineHeight: 1 }}>
        {bus.name}
      </span>
      {bus.v_nom ? (
        <span style={{ fontSize: 9, color: 'var(--color-muted)', marginLeft: 2 }}>{bus.v_nom}kV</span>
      ) : null}

      {/* Results overlay chip + per-carrier stacked donut — absolute-
          positioned below the bus capsule so the bus's own height stays
          unchanged. The chip shows NET balance (gen − load) with arrow
          direction encoding whether power leaves (green ↓) or enters
          (orange ↑) the bus at this snapshot. The donut to its left
          appears only when the bus hosts >1 carrier of activity (e.g. an
          AC bus that ALSO has a heat consumer); each slice is sized by
          that carrier's |gen|+|load| magnitude and coloured via the
          shared carrier palette. Single-carrier buses hide the donut —
          the chip alone conveys the full story. */}
      {(() => {
        if (!overlay) return null
        // Show gen/load separately when BOTH sides are non-zero so a
        // balanced AC transmission bus (gen ≈ load → net ≈ 0) still
        // surfaces its activity. Previous net-only logic hid the chip on
        // any bus where supply matched demand — surprised users who
        // expected to see the 200+ MW flowing through. Skip-zero rule
        // means single-direction buses (pure load / pure gen) render
        // just one arrow without "0 kW" clutter.
        const hasGen  = overlay.gen > 0.05
        const hasLoad = overlay.load > 0.05
        // Per-carrier magnitudes — drives the donut. A carrier counts
        // when its |gen|+|load| > epsilon at this snapshot. Buses with
        // exactly one active carrier render the chip only.
        const carrierSlices: Array<{ carrier: string; mag: number }> = []
        if (overlay.byCarrier && overlay.byCarrier.size > 0) {
          for (const [carrier, v] of overlay.byCarrier) {
            const mag = Math.abs(v.gen) + Math.abs(v.load)
            if (mag > 0.05) carrierSlices.push({ carrier, mag })
          }
        }
        const showDonut = carrierSlices.length > 1
        const totalMag = carrierSlices.reduce((a, s) => a + s.mag, 0)
        const hideChip = !hasGen && !hasLoad
        if (hideChip && !showDonut) return null
        // Single-sided buses display net + arrow (more compact); both-
        // sided display two values side-by-side. Accent picks the side
        // with the larger magnitude so the chip border still encodes
        // the dominant direction at a glance.
        const net = overlay.gen - overlay.load
        const isOut = net > 0
        const accent = isOut ? '#16a34a' : '#d97706'
        return (
          <div
            className="absolute"
            style={{
              top: '100%', left: 0, marginTop: 3,
              display: 'flex', gap: 4, alignItems: 'center',
              pointerEvents: 'auto',
            }}
          >
            {showDonut && (() => {
              // Build a stacked-donut SVG. Each carrier gets a ring slice
              // proportional to its share of the total magnitude. Inner
              // hole keeps the chart readable at small size (12×12).
              const R = 6, r = 3.5, cx = 7, cy = 7
              let acc = 0
              const slices = carrierSlices.map(s => {
                const frac = s.mag / totalMag
                const start = acc
                acc += frac
                return { ...s, frac, start, end: acc }
              })
              // Tooltip lists each carrier's gen / load contribution so a
              // hover reveals the full breakdown without clicking through.
              const lines: string[] = []
              for (const [carrier, v] of overlay.byCarrier) {
                if (Math.abs(v.gen) + Math.abs(v.load) <= 0.05) continue
                lines.push(`${carrier}: gen ${fmtMW(v.gen)} · load ${fmtMW(v.load)}`)
              }
              const tooltip = `Per-carrier activity at this snapshot:\n${lines.join('\n')}`
              return (
                <svg width={14} height={14} viewBox="0 0 14 14"
                  style={{ display: 'block', flexShrink: 0 }}
                >
                  {/* Native SVG <title> child is what browsers honour on
                      hover for SVG — there's no `title` HTML attribute on
                      <svg>. */}
                  <title>{tooltip}</title>
                  {slices.map(s => {
                    // SVG arc path: outer arc + inner arc (donut slice).
                    const a0 = s.start * 2 * Math.PI - Math.PI / 2
                    const a1 = s.end   * 2 * Math.PI - Math.PI / 2
                    const x0o = cx + R * Math.cos(a0), y0o = cy + R * Math.sin(a0)
                    const x1o = cx + R * Math.cos(a1), y1o = cy + R * Math.sin(a1)
                    const x0i = cx + r * Math.cos(a1), y0i = cy + r * Math.sin(a1)
                    const x1i = cx + r * Math.cos(a0), y1i = cy + r * Math.sin(a0)
                    const large = s.frac > 0.5 ? 1 : 0
                    // A single carrier at fraction = 1 (degenerate
                    // showDonut === false case) renders as a full ring;
                    // we don't reach here in that path.
                    const d = [
                      `M ${x0o} ${y0o}`,
                      `A ${R} ${R} 0 ${large} 1 ${x1o} ${y1o}`,
                      `L ${x0i} ${y0i}`,
                      `A ${r} ${r} 0 ${large} 0 ${x1i} ${y1i}`,
                      'Z',
                    ].join(' ')
                    return <path key={s.carrier} d={d} fill={colourForCarrier(s.carrier)} />
                  })}
                </svg>
              )
            })()}
            {!hideChip && (
              <div
                title={`Gen ${fmtMW(overlay.gen)} · Load ${fmtMW(overlay.load)}\nNet ${isOut ? 'out of' : 'into'} bus: ${fmtMW(Math.abs(net))}`}
                style={{
                  display: 'flex', gap: 4, alignItems: 'center',
                  padding: '2px 6px',
                  background: 'var(--color-bg)',
                  border: `1px solid ${accent}`,
                  borderRadius: 3,
                  fontSize: 9, fontFamily: 'monospace', fontWeight: 600,
                  boxShadow: '0 1px 2px rgba(0,0,0,0.06)',
                }}
              >
                {hasGen && (
                  <>
                    <span style={{ fontSize: 11, lineHeight: 1, color: '#16a34a' }}>▲</span>
                    <span style={{ color: '#16a34a' }}>{fmtMW(overlay.gen)}</span>
                  </>
                )}
                {hasGen && hasLoad && (
                  <span style={{ color: 'var(--color-muted)', fontWeight: 400 }}>·</span>
                )}
                {hasLoad && (
                  <>
                    <span style={{ fontSize: 11, lineHeight: 1, color: '#d97706' }}>▼</span>
                    <span style={{ color: '#d97706' }}>{fmtMW(overlay.load)}</span>
                  </>
                )}
              </div>
            )}
          </div>
        )
      })()}
    </div>
  )
}

const H2_BUS_CARRIERS = ['h2', 'hydrogen', 'h₂']

function isH2BusCarrier(carrier: string): boolean {
  const c = carrier.toLowerCase()
  return H2_BUS_CARRIERS.some(k => c === k || c.startsWith(k))
}

// ── Asset SVG icons ────────────────────────────────────────────────────────────
function AssetIcon({ category, carriers, busCarrier = '', size = 30 }: { category: AssetCategory; carriers: string[]; busCarrier?: string; size?: number }) {
  const s = size
  const cx = s / 2, cy = s / 2
  const lc = carriers.map(c => c.toLowerCase())
  const hasSolar = lc.some(c => c.includes('solar') || c.includes('pv') || c.includes('rooftop'))
  const hasWind  = lc.some(c => c.includes('wind'))
  const isH2Bus  = isH2BusCarrier(busCarrier)

  if (category === 'Renewables') {
    if (hasSolar && !hasWind) {
      // Sun with rays
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="currentColor">
          <circle cx={cx} cy={cy} r={s * 0.2} />
          {[0, 45, 90, 135, 180, 225, 270, 315].map(a => {
            const r = (a * Math.PI) / 180
            return (
              <line key={a}
                x1={cx + Math.cos(r) * s * 0.28} y1={cy + Math.sin(r) * s * 0.28}
                x2={cx + Math.cos(r) * s * 0.43} y2={cy + Math.sin(r) * s * 0.43}
                stroke="currentColor" strokeWidth={s * 0.07} strokeLinecap="round"
              />
            )
          })}
        </svg>
      )
    }
    // Wind turbine (default or wind/mixed)
    return (
      <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`}>
        <line x1={cx} y1={s * 0.9} x2={cx} y2={s * 0.38}
          stroke="currentColor" strokeWidth={s * 0.08} strokeLinecap="round" />
        <circle cx={cx} cy={s * 0.38} r={s * 0.07} fill="currentColor" />
        {[0, 120, 240].map(a => {
          const rad = ((a - 90) * Math.PI) / 180
          return (
            <path key={a}
              d={`M${cx} ${s * 0.38}
                  L${cx + Math.cos(rad + 1.4) * s * 0.06} ${s * 0.38 + Math.sin(rad + 1.4) * s * 0.06}
                  L${cx + Math.cos(rad) * s * 0.3} ${s * 0.38 + Math.sin(rad) * s * 0.3}Z`}
              fill="currentColor"
            />
          )
        })}
      </svg>
    )
  }

  if (category === 'Thermal') {
    return (
      <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="currentColor">
        <path d={`M${cx} ${s*0.92}
          C${cx-s*0.3} ${s*0.92} ${cx-s*0.36} ${s*0.72} ${cx-s*0.34} ${s*0.56}
          C${cx-s*0.36} ${s*0.4} ${cx-s*0.2} ${s*0.28} ${cx-s*0.06} ${s*0.2}
          C${cx-s*0.08} ${s*0.3} ${cx-s*0.01} ${s*0.34} ${cx+s*0.03} ${s*0.28}
          C${cx+s*0.06} ${s*0.2} ${cx+s*0.1} ${s*0.12} ${cx+s*0.2} ${s*0.08}
          C${cx+s*0.16} ${s*0.18} ${cx+s*0.22} ${s*0.3} ${cx+s*0.16} ${s*0.4}
          C${cx+s*0.3} ${s*0.28} ${cx+s*0.32} ${s*0.14} ${cx+s*0.22} ${s*0.08}
          C${cx+s*0.42} ${s*0.22} ${cx+s*0.38} ${s*0.48} ${cx+s*0.34} ${s*0.56}
          C${cx+s*0.36} ${s*0.72} ${cx+s*0.3} ${s*0.92} ${cx} ${s*0.92}Z`} />
        <path d={`M${cx} ${s*0.84}
          C${cx-s*0.13} ${s*0.84} ${cx-s*0.15} ${s*0.72} ${cx-s*0.05} ${s*0.62}
          C${cx-s*0.03} ${s*0.68} ${cx+s*0.03} ${s*0.68} ${cx+s*0.05} ${s*0.62}
          C${cx+s*0.15} ${s*0.72} ${cx+s*0.13} ${s*0.84} ${cx} ${s*0.84}Z`}
          fill="white" opacity="0.3" />
      </svg>
    )
  }

  if (category === 'Load') {
    if (isH2Bus) {
      // H₂ demand: factory/industrial icon with H₂ label
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="currentColor">
          {/* Factory building body */}
          <rect x={cx-s*0.3} y={cy-s*0.05} width={s*0.6} height={s*0.42} rx={s*0.03} fill="currentColor" opacity="0.15" stroke="currentColor" strokeWidth={s*0.055} />
          {/* Chimney left */}
          <rect x={cx-s*0.22} y={cy-s*0.32} width={s*0.1} height={s*0.28} rx={s*0.02} />
          {/* Chimney right */}
          <rect x={cx+s*0.04} y={cy-s*0.26} width={s*0.1} height={s*0.22} rx={s*0.02} />
          {/* Smoke puff left */}
          <circle cx={cx-s*0.17} cy={cy-s*0.38} r={s*0.06} opacity={0.5} />
          {/* Door */}
          <rect x={cx-s*0.07} y={cy+s*0.18} width={s*0.14} height={s*0.2} rx={s*0.02} fill="white" opacity="0.7" />
          {/* H₂ text */}
          <text x={cx} y={cy+s*0.03} textAnchor="middle" fontSize={s*0.22} fontWeight="bold" fontFamily="monospace" fill="white" opacity="0.9">H₂</text>
        </svg>
      )
    }
    const hw = s * 0.3, hbot = s * 0.86, htop = s * 0.46
    return (
      <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="currentColor">
        <path d={`M${cx} ${s*0.1} L${cx+hw+s*0.08} ${htop} L${cx+hw} ${htop} L${cx+hw} ${hbot} L${cx-hw} ${hbot} L${cx-hw} ${htop} L${cx-hw-s*0.08} ${htop}Z`}
          fill="currentColor" opacity="0.15" />
        <path d={`M${cx} ${s*0.1} L${cx+hw+s*0.08} ${htop} L${cx+hw} ${htop} L${cx+hw} ${hbot} L${cx-hw} ${hbot} L${cx-hw} ${htop} L${cx-hw-s*0.08} ${htop}Z`}
          fill="none" stroke="currentColor" strokeWidth={s*0.055} strokeLinejoin="round" />
        <path d={`M${cx+s*0.08} ${s*0.42} L${cx-s*0.07} ${s*0.6} L${cx+s*0.02} ${s*0.6} L${cx-s*0.08} ${s*0.8} L${cx+s*0.07} ${s*0.6} L${cx-s*0.02} ${s*0.6}Z`} />
      </svg>
    )
  }

  if (category === 'Storage') {
    if (isH2Bus) {
      // H₂ tank: pressure vessel / cylinder icon
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="currentColor">
          {/* Tank body — vertical cylinder */}
          <rect x={cx-s*0.22} y={cy-s*0.3} width={s*0.44} height={s*0.52} rx={s*0.08} fill="none" stroke="currentColor" strokeWidth={s*0.07} />
          {/* Top dome */}
          <path d={`M${cx-s*0.22} ${cy-s*0.3} A${s*0.22} ${s*0.14} 0 0 1 ${cx+s*0.22} ${cy-s*0.3}`} fill="currentColor" opacity="0.3" />
          {/* Bottom dome */}
          <path d={`M${cx-s*0.22} ${cy+s*0.22} A${s*0.22} ${s*0.14} 0 0 0 ${cx+s*0.22} ${cy+s*0.22}`} fill="currentColor" opacity="0.15" />
          {/* Valve stem on top */}
          <rect x={cx-s*0.04} y={cy-s*0.45} width={s*0.08} height={s*0.16} rx={s*0.02} />
          <rect x={cx-s*0.12} y={cy-s*0.47} width={s*0.24} height={s*0.04} rx={s*0.02} />
          {/* H₂ label inside tank */}
          <text x={cx} y={cy+s*0.07} textAnchor="middle" fontSize={s*0.24} fontWeight="bold" fontFamily="monospace" fill="currentColor" opacity="0.9">H₂</text>
        </svg>
      )
    }
    const bx = cx - s*0.33, by = cy - s*0.2, bw = s*0.58, bh = s*0.4
    return (
      <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="currentColor">
        <rect x={bx} y={by} width={bw} height={bh} rx={s*0.04} fill="none" stroke="currentColor" strokeWidth={s*0.07} />
        <rect x={bx+bw} y={cy-s*0.11} width={s*0.1} height={s*0.22} rx={s*0.02} />
        {[0, 1, 2].map(i => (
          <rect key={i}
            x={bx + s*0.07 + i*(s*0.14)} y={by+s*0.07}
            width={s*0.1} height={bh-s*0.14} rx={s*0.02}
            opacity={i < 2 ? 1 : 0.35}
          />
        ))}
        <path d={`M${cx+s*0.06} ${cy-s*0.1} L${cx-s*0.04} ${cy} L${cx+s*0.02} ${cy} L${cx-s*0.06} ${cy+s*0.1} L${cx+s*0.04} ${cy} L${cx-s*0.02} ${cy}Z`}
          fill="white" opacity="0.9" />
      </svg>
    )
  }

  // Generic: power symbol (circle with gap + stick)
  return (
    <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="none" stroke="currentColor">
      <path d={`M${cx} ${cy-s*0.12} L${cx} ${cy-s*0.4}`} strokeWidth={s*0.09} strokeLinecap="round" />
      <path d={`M${cx-s*0.28} ${cy-s*0.28} A${s*0.33} ${s*0.33} 0 1 0 ${cx+s*0.28} ${cy-s*0.28}`}
        strokeWidth={s*0.09} strokeLinecap="round" />
    </svg>
  )
}

const H2_NODE_COLORS: Record<AssetCategory, string> = {
  Load:       '#7c3aed',  // violet — same as H₂ bus colour
  Storage:    '#6d28d9',  // deeper violet for H₂ tank
  Thermal:    '#dc2626',  // unchanged
  Renewables: '#16a34a',  // unchanged
}

const H2_CAT_LABELS: Partial<Record<AssetCategory, string>> = {
  Load:    'H₂ Demand',
  Storage: 'H₂ Storage',
}

// ── Asset group node ───────────────────────────────────────────────────────────
function AssetGroupNode({ id, data, selected }: NodeProps) {
  const d = data as unknown as AssetGroupData
  const isH2 = (d.category === 'Load' || d.category === 'Storage') && isH2BusCarrier(d.busCarrier ?? '')
  const cfgBase = CATEGORY_CONFIG[d.category]
  const color = isH2 ? H2_NODE_COLORS[d.category] : cfgBase.color
  const [hovered, setHovered] = useState(false)
  const overlapping = useContext(OverlapContext)
  const isOverlapping = overlapping.has(id)

  // Per-snapshot dispatch overlay for this asset group. When the results
  // overlay is enabled (after a successful solve, on the blank canvas), we
  // sum the per-asset dispatch into a single value matching this group's
  // (bus, category) bucket and show it below the static capacity. Hidden
  // otherwise to keep the card uncluttered.
  // Storage groups additionally surface SoC % at this snapshot (BESS-style
  // battery cards always show the depth-of-charge regardless of whether
  // the unit is charging, discharging, or idle).
  const results = useCanvasResults()
  const dispatchMW = results.enabled
    ? results.byAssetGroup.get(`${d.busName}|${d.category}`)
    : undefined
  const socPct = results.enabled && d.category === 'Storage'
    ? results.byAssetGroupSoC.get(`${d.busName}|Storage`)
    : undefined
  // Tier colour for the SoC indicator: red below 20 %, amber 20–80 %, green above.
  // Matches the standard BESS health bands used in dispatch dashboards.
  const socColor = socPct == null ? color
    : socPct < 20 ? '#dc2626'
    : socPct < 80 ? '#d97706'
    : '#16a34a'
  // Sign convention by category:
  //   Thermal / Renewables → positive = injection (↑ arrow)
  //   Load                 → always consumption (↓ arrow); value treated as |v|
  //   Storage              → positive = discharge ↑, negative = charge ↓
  let dispatchArrow = ''
  let dispatchColor = color
  if (dispatchMW != null && Number.isFinite(dispatchMW)) {
    if (d.category === 'Load') {
      dispatchArrow = '↓'  // load consumes — always a sink
      dispatchColor = '#dc2626'
    } else if (d.category === 'Storage') {
      dispatchArrow = dispatchMW >= 0 ? '↑' : '↓'
      dispatchColor = dispatchMW >= 0 ? '#16a34a' : '#d97706'
    } else {
      // Thermal / Renewables — positive = injection
      dispatchArrow = dispatchMW >= 0 ? '↑' : '↓'
      dispatchColor = '#16a34a'
    }
  }

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="select-none cursor-pointer"
      style={{
        width: 68,
        backgroundColor: 'var(--color-bg)',
        border: `${selected ? 2 : 1.2}px solid ${color}`,
        borderRadius: 7,
        boxShadow: isOverlapping
          ? `0 0 0 2px rgba(239,68,68,0.4), 0 2px 8px rgba(0,0,0,0.12)`
          : selected
          ? `0 0 0 2px ${color}22, 0 2px 8px rgba(0,0,0,0.12)`
          : hovered ? '0 2px 7px rgba(0,0,0,0.12)' : '0 1px 4px rgba(0,0,0,0.08)',
        filter: selected ? `drop-shadow(0 0 3px ${color}88)` : undefined,
        transform: hovered && !selected ? 'translate(-1px, -2px)' : undefined,
        transition: 'transform 120ms, box-shadow 120ms',
        padding: '5px 4px 4px',
      }}
    >
      <Handle type="source" position={Position.Left} style={CENTER_HANDLE} />
      <Handle type="target" position={Position.Left} style={CENTER_HANDLE} />

      {/* Icon */}
      <div style={{
        width: 28, height: 28, margin: '0 auto 3px',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        backgroundColor: hovered || selected ? `${color}22` : `${color}12`,
        borderRadius: 5,
        color,
        transition: 'background-color 120ms',
      }}>
        <AssetIcon category={d.category} carriers={d.carriers} busCarrier={d.busCarrier ?? ''} size={18} />
      </div>

      {/* Type label */}
      <div style={{ fontSize: 8.5, fontWeight: 600, color: 'var(--color-text)', textAlign: 'center', lineHeight: 1.15 }}>
        {(isH2 && H2_CAT_LABELS[d.category]) ?? CAT_DISPLAY_LABELS[d.category] ?? d.category}
      </div>

      {/* Count · capacity. When the results overlay is on, show the
          EFFECTIVE capacity for the selected snapshot's investment
          period: parent.p_nom + Σ vintage.p_nom_opt for vintages with
          build_year ≤ this snapshot's period. Without this the static
          d.totalMW always shows the input p_nom (e.g. 300 MW for solar)
          even when scrubbing to a period where a vintage has activated
          (e.g. 593 MW in 2028 because Solar@2028 came online). */}
      {(() => {
        const effective = results.enabled
          ? results.byAssetGroupCapacity.get(`${d.busName}|${d.category}`)
          : undefined
        const mw = (effective != null && Number.isFinite(effective)) ? effective : d.totalMW
        return (
          <div style={{ fontSize: 8, color: 'var(--color-muted)', marginTop: 1, textAlign: 'center' }}>
            {d.assetCount} · {mw >= 1000 ? `${(mw / 1000).toFixed(1)} GW` : `${mw.toFixed(0)} MW`}
          </div>
        )
      })()}

      {/* Per-snapshot dispatch overlay — only renders when the results
          overlay is enabled and this group has matching dispatch data. */}
      {dispatchMW != null && Number.isFinite(dispatchMW) && (
        <div
          style={{
            fontSize: 8.5,
            fontWeight: 600,
            color: dispatchColor,
            marginTop: 2,
            textAlign: 'center',
            lineHeight: 1.15,
          }}
          title={
            d.category === 'Storage'
              ? `Dispatch at this snapshot (positive = discharge): ${dispatchMW.toFixed(2)} MW`
              : d.category === 'Load'
                ? `Consumption at this snapshot: ${dispatchMW.toFixed(2)} MW`
                : `Generation at this snapshot: ${dispatchMW.toFixed(2)} MW`
          }
        >
          {dispatchArrow} {fmtMW(Math.abs(dispatchMW))}
        </div>
      )}

      {/* SoC % — Storage-only. Shown even at hours where dispatch is 0
          (idle hours are exactly when knowing the state of charge matters
          for sizing / cycling intuition). */}
      {socPct != null && Number.isFinite(socPct) && (
        <div
          style={{
            fontSize: 8,
            fontWeight: 500,
            color: socColor,
            marginTop: 1,
            textAlign: 'center',
            lineHeight: 1.15,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 3,
          }}
          title={`State of charge at this snapshot: ${socPct.toFixed(1)} %`}
        >
          {/* Tiny gauge bar — visual cue alongside the number */}
          <span
            aria-hidden
            style={{
              display: 'inline-block',
              width: 22,
              height: 4,
              background: '#e5e7eb',
              borderRadius: 2,
              overflow: 'hidden',
            }}
          >
            <span
              style={{
                display: 'block',
                height: '100%',
                width: `${Math.max(0, Math.min(100, socPct))}%`,
                background: socColor,
              }}
            />
          </span>
          <span>SoC {socPct.toFixed(0)} %</span>
        </div>
      )}

      {/* Carrier pills */}
      {d.carriers.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 1.5, marginTop: 3, justifyContent: 'center' }}>
          {d.carriers.slice(0, 3).map(c => (
            <div key={c} style={{
              backgroundColor: `${color}18`,
              color,
              fontSize: 7,
              padding: '0px 3px',
              borderRadius: 999,
              whiteSpace: 'nowrap',
              maxWidth: 58,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              lineHeight: 1.4,
            }}>{c}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Edge context menu icons ────────────────────────────────────────────────────
const UndoIcon = () => (
  <svg width="15" height="15" viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M5.5 3L3 5.5 5.5 8" />
    <path d="M3 5.5A6.5 6.5 0 1 1 3 9.5" />
  </svg>
)
const ClearRouteIcon = () => (
  <svg width="15" height="15" viewBox="0 0 15 15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <line x1="1.5" y1="7.5" x2="9" y2="7.5" />
    <line x1="10" y1="4" x2="14" y2="11" />
    <line x1="14" y1="4" x2="10" y2="11" />
  </svg>
)

// ── Editable edge ──────────────────────────────────────────────────────────────
function EditableEdge({ id, sourceX, sourceY, targetX, targetY, data, selected }: EdgeProps) {
  const { setEdges, screenToFlowPosition } = useReactFlow()
  const edgeMenu = useContext(EdgeMenuContext)
  const highlight = useContext(HighlightContext)
  const ed = (data as unknown as EdgeData) ?? ({} as EdgeData)
  const waypoints: WP[] = ed.waypoints ?? []
  // Results overlay — only meaningful for line/link/transformer edges where
  // active-power flow is a defined concept. Asset edges (gen→bus, load→bus
  // etc.) are excluded because they don't have a time-series counterpart.
  //
  // Two lookups: `lineOverlay` covers Line + Transformer (both via
  // `byLine`), `linkOverlay` covers Link (via `byLink` — populated by V1
  // of the multi-carrier video work). The two are mutually exclusive per
  // edge (an edge is either a Line/Transformer or a Link), so the colour
  // + badge code branches on which lookup hit.
  const results = useCanvasResults()
  const edgeName = id.replace(/^(line-|link-|tr-)/, '')
  const lineOverlay = results.enabled && (ed.type === 'line' || ed.type === 'transformer')
    ? results.byLine.get(edgeName)
    : undefined
  const linkOverlay = results.enabled && ed.type === 'link'
    ? results.byLink.get(edgeName)
    : undefined
  // Loading % from whichever overlay matched — used for the colour band on
  // the path AND the badge border.
  const overlayLoadingPct = lineOverlay?.loadingPct ?? linkOverlay?.loadingPct
  // When overlay is on, tint the path by loading band; otherwise keep the
  // existing voltage-tier / carrier colour. Asset edges are unaffected.
  const baseColor = ed.color ?? '#374151'
  const color = (overlayLoadingPct != null && ed.type !== 'asset')
    ? loadingColor(overlayLoadingPct)
    : baseColor
  const isAsset = ed.type === 'asset'
  const isLink = ed.type === 'link'
  const isTransformer = ed.type === 'transformer'
  const sw = ed.type === 'line' ? Math.max(2.5, Math.min(6, (ed.s_nom ?? 100) / 200))
           : isAsset ? 2 : 2.5
  const dashArray = isLink ? '8 5' : isAsset ? '6 3' : undefined
  const isHighlightedEdge = (
    (ed.type === 'line' && highlight?.type === 'Line' && highlight.name === edgeName) ||
    (ed.type === 'link' && highlight?.type === 'Link' && highlight.name === edgeName)
  )
  const [hovered, setHovered] = useState(false)
  const active = selected || hovered

  // Grace-period hover guard. Without it, mouseLeave fires on the path the
  // moment the cursor crosses onto a handle (the handles are siblings of
  // the path in DOM order, so they sit on top). hovered → false un-renders
  // the handles, the cursor falls through to the path, mouseEnter fires,
  // handles re-render — infinite flicker. The 80 ms timeout lets the
  // marker's own mouseEnter cancel the pending hide before it commits.
  // Same trick used in MapCanvas's EditableLine.
  const leaveTimerRef = useRef<number | null>(null)
  const onAreaEnter = useCallback(() => {
    if (leaveTimerRef.current !== null) {
      clearTimeout(leaveTimerRef.current)
      leaveTimerRef.current = null
    }
    setHovered(true)
  }, [])
  const onAreaLeave = useCallback(() => {
    if (leaveTimerRef.current !== null) clearTimeout(leaveTimerRef.current)
    leaveTimerRef.current = window.setTimeout(() => {
      setHovered(false)
      leaveTimerRef.current = null
    }, 80)
  }, [])
  useEffect(() => () => {
    if (leaveTimerRef.current !== null) clearTimeout(leaveTimerRef.current)
  }, [])

  const allPoints = useMemo(() => [
    { x: sourceX, y: sourceY }, ...waypoints, { x: targetX, y: targetY }
  ], [sourceX, sourceY, targetX, targetY, waypoints])

  const midPoint = useMemo(() => {
    const mid = Math.floor((allPoints.length - 1) / 2)
    return { x: (allPoints[mid].x + allPoints[mid + 1].x) / 2, y: (allPoints[mid].y + allPoints[mid + 1].y) / 2 }
  }, [allPoints])

  // True geometric midpoint along the drawn polyline — at exactly half the
  // total path length, including any waypoints. Used by the transformer IEC
  // symbol so it always sits on the visible line, halfway between the two
  // buses regardless of routing. Also returns the local angle (radians) of
  // the segment containing that midpoint so consumers can rotate aligned
  // glyphs to match the line direction.
  const pathMidpoint = useMemo(() => {
    if (allPoints.length < 2) return { x: 0, y: 0, angleRad: 0 }
    const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
      Math.hypot(b.x - a.x, b.y - a.y)
    let total = 0
    for (let i = 0; i < allPoints.length - 1; i++) total += dist(allPoints[i], allPoints[i + 1])
    if (total === 0) return { x: allPoints[0].x, y: allPoints[0].y, angleRad: 0 }
    const half = total / 2
    let acc = 0
    for (let i = 0; i < allPoints.length - 1; i++) {
      const a = allPoints[i]
      const b = allPoints[i + 1]
      const seg = dist(a, b)
      if (acc + seg >= half) {
        const t = (half - acc) / seg
        return {
          x: a.x + t * (b.x - a.x),
          y: a.y + t * (b.y - a.y),
          angleRad: Math.atan2(b.y - a.y, b.x - a.x),
        }
      }
      acc += seg
    }
    const last = allPoints[allPoints.length - 1]
    return { x: last.x, y: last.y, angleRad: 0 }
  }, [allPoints])

  const pathRef    = useRef<SVGPathElement | null>(null)
  const hitRef     = useRef<SVGPathElement | null>(null)
  const outerGRef  = useRef<SVGGElement | null>(null)
  const isDragging = useRef(false)

  // Keep a ref to edgeMenu.open so the native contextmenu handler never goes stale
  const edgeMenuOpenRef = useRef(edgeMenu.open)
  useEffect(() => { edgeMenuOpenRef.current = edgeMenu.open }, [edgeMenu.open])

  // Native contextmenu listener in CAPTURE phase — fires before React Flow's
  // own synthetic event interceptors, guaranteeing the menu always opens.
  useEffect(() => {
    const el = outerGRef.current
    if (!el) return
    const onCtxMenu = (e: MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      edgeMenuOpenRef.current(id, e.clientX, e.clientY)
    }
    el.addEventListener('contextmenu', onCtxMenu, true)
    return () => el.removeEventListener('contextmenu', onCtxMenu, true)
  }, [id])

  const liveWps = useRef<WP[]>([...waypoints])
  useEffect(() => { liveWps.current = [...waypoints] }, [waypoints])

  // After every React render, if we are mid-drag, immediately restore the live
  // drag path so a React re-render cannot overwrite the imperatively-updated path
  // and create a ghost at the pre-drag position.
  useLayoutEffect(() => {
    if (!isDragging.current) return
    const d = buildD(liveWps.current)
    pathRef.current?.setAttribute('d', d)
    hitRef.current?.setAttribute('d', d)
  })

  const buildD = (wps: WP[]) =>
    [{ x: sourceX, y: sourceY }, ...wps, { x: targetX, y: targetY }]
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')

  const pathD = buildD(waypoints)

  const onHandlePointerDown = (wpIdx: number, segIdx: number | null) =>
    (e: React.PointerEvent<SVGCircleElement>) => {
      if (e.button !== 0) return  // ignore right-click
      e.stopPropagation()
      e.preventDefault()
      edgeMenu.close()
      isDragging.current = true
      document.body.style.cursor = 'grabbing'
      const el = e.currentTarget
      el.setPointerCapture(e.pointerId)
      if (segIdx !== null) {
        const p0 = allPoints[segIdx], p1 = allPoints[segIdx + 1]
        liveWps.current = [
          ...liveWps.current.slice(0, segIdx),
          { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 },
          ...liveWps.current.slice(segIdx),
        ]
        // Immediately paint new path so the pre-drag straight segment can't ghost
        const d0 = buildD(liveWps.current)
        pathRef.current?.setAttribute('d', d0)
        hitRef.current?.setAttribute('d', d0)
      }
      const onMove = (pe: PointerEvent) => {
        const pos = screenToFlowPosition({ x: pe.clientX, y: pe.clientY })
        liveWps.current = liveWps.current.map((wp, i) => i === wpIdx ? pos : wp)
        const d = buildD(liveWps.current)
        pathRef.current?.setAttribute('d', d)
        hitRef.current?.setAttribute('d', d)
        el.setAttribute('cx', String(pos.x))
        el.setAttribute('cy', String(pos.y))
      }
      const onUp = () => {
        isDragging.current = false
        document.body.style.cursor = ''
        el.releasePointerCapture(e.pointerId)
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        const finalWps = [...liveWps.current]
        setEdges(eds => eds.map(edge => {
          if (edge.id !== id) return edge
          const hist: WP[][] = [...((edge.data as unknown as EdgeData).history ?? [[]])]
          if (hist.length >= 50) hist.shift()
          hist.push(JSON.parse(JSON.stringify(finalWps)))
          return { ...edge, data: { ...edge.data, waypoints: finalWps, history: hist } }
        }))
        edgeMenu.scheduleSave()
      }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    }

  const onRemoveWaypoint = (wpIdx: number) => (e: React.MouseEvent) => {
    e.stopPropagation()
    setEdges(eds => eds.map(edge => {
      if (edge.id !== id) return edge
      const newWps = ((edge.data as unknown as EdgeData).waypoints ?? [])
        .filter((_: WP, i: number) => i !== wpIdx)
      const hist: WP[][] = [...((edge.data as unknown as EdgeData).history ?? [[]])]
      if (hist.length >= 50) hist.shift()
      hist.push(JSON.parse(JSON.stringify(newWps)))
      return { ...edge, data: { ...edge.data, waypoints: newWps, history: hist } }
    }))
    edgeMenu.scheduleSave()
  }

  return (
    <>
      <g ref={outerGRef}>
        {isHighlightedEdge && (
          <path d={pathD}
            stroke="#2f81f7"
            strokeWidth={sw + 8}
            fill="none"
            strokeLinecap="round" strokeLinejoin="round"
            opacity={0.85}
            pointerEvents="none"
            className="highlight-edge-pulse"
          />
        )}
        {/* Wide invisible hit-area — pointer-events="stroke" fires regardless of paint visibility */}
        <path ref={hitRef} d={pathD}
          stroke="#000" strokeOpacity={0} strokeWidth={22} fill="none"
          pointerEvents="stroke"
          style={{ cursor: active ? 'crosshair' : 'pointer' }}
          onMouseEnter={onAreaEnter}
          onMouseLeave={onAreaLeave}
        />
        <path ref={pathRef} d={pathD}
          stroke={active ? color : `${color}cc`}
          strokeWidth={active ? sw + 0.5 : sw}
          fill="none"
          strokeDasharray={dashArray}
          strokeLinecap="round" strokeLinejoin="round"
          opacity={active ? 1 : 0.85}
          pointerEvents="none"
        />
        {allPoints.slice(0, -1).map((p, i) => {
          const q = allPoints[i + 1]
          const isBadgeSeg = isLink && i === Math.floor((allPoints.length - 1) / 2)
          if (isBadgeSeg) return null
          const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2
          return (
            <g key={`mid-${i}`} opacity={active ? 0.85 : 0}>
              {/* Transparent hit area — keeps cursor stable without visual jitter */}
              <circle cx={mx} cy={my} r={10} fill="transparent"
                style={{ cursor: 'crosshair', pointerEvents: active ? 'all' : 'none' }}
                onPointerDown={onHandlePointerDown(i, i)}
                onMouseEnter={onAreaEnter}
                onMouseLeave={onAreaLeave}
              />
              {/* Visual dot — pointer-events=none so cursor is owned by the hit area */}
              <circle cx={mx} cy={my} r={4} fill="white" stroke={color} strokeWidth={1.5}
                style={{ pointerEvents: 'none' }}
              />
            </g>
          )
        })}
        {active && waypoints.map((wp, i) => (
          <g key={`wp-${i}`}>
            <circle cx={wp.x} cy={wp.y} r={10} fill="transparent"
              style={{ cursor: 'grab', pointerEvents: 'all' }}
              onPointerDown={onHandlePointerDown(i, null)}
              onDoubleClick={onRemoveWaypoint(i)}
              onMouseEnter={onAreaEnter}
              onMouseLeave={onAreaLeave}
            />
            <circle cx={wp.x} cy={wp.y} r={5} fill={color} stroke="white" strokeWidth={2}
              style={{ pointerEvents: 'none' }}
            />
          </g>
        ))}
      </g>
      {isLink && ed.carrier && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${midPoint.x}px, ${midPoint.y}px)`,
              pointerEvents: 'none',
            }}
          >
            {/* Carrier badge stays the carrier's IDENTITY colour
                (baseColor) even when the results overlay is on and
                `color` has been remapped to the loading-band hue —
                otherwise an H2 link in overload reads as a red "H₂"
                badge, defeating the badge's purpose of conveying
                carrier-at-a-glance. QA-flagged cross-talk. */}
            <CarrierBadge carrier={ed.carrier} color={baseColor} />
          </div>
        </EdgeLabelRenderer>
      )}
      {/* Results overlay badge — flow value + direction arrow + loading %.
          The arrow is rotated to point in SCREEN direction toward the
          downstream bus, so a glance tells the user which bus feeds which.
          Sign of p0 (positive = flow bus0→bus1) decides whether the arrow
          follows or opposes the segment vector. Hidden on asset edges. */}
      {lineOverlay && !isAsset && (() => {
        // Use the segment that contains the midpoint as the reference
        // direction. With straight lines this is just (target - source); with
        // waypoints it's the middle segment that the label sits on, so the
        // arrow stays parallel to the line under the chip.
        const mid = Math.floor((allPoints.length - 1) / 2)
        const a = allPoints[mid]
        const b = allPoints[mid + 1]
        // Decide whether we're displaying active (P) or reactive (Q) power.
        // Q is only meaningful in AC PF; when results.kind is 'q' but q0 is
        // null (data not loaded yet), fall back to p0 so the chip doesn't
        // flicker between empty and present states during the round-trip.
        const showQ = results.kind === 'q' && lineOverlay.q0 != null
        const value = showQ ? (lineOverlay.q0 as number) : lineOverlay.p0
        const unit = showQ ? 'MVAr' : 'MW'
        let dx = b.x - a.x
        let dy = b.y - a.y
        if (value < 0) { dx = -dx; dy = -dy }  // arrow points downstream
        // atan2 returns radians from +x axis. SVG/CSS rotation also starts
        // from +x with positive = clockwise (because y grows downward), so
        // no sign flip is needed.
        const angleDeg = (Math.abs(dx) + Math.abs(dy) > 0)
          ? Math.atan2(dy, dx) * 180 / Math.PI
          : 0
        // Loading band still keyed by active-power loading even in Q view —
        // the band's job is to flag overload, which is a thermal-rating
        // concept, so Q-only data doesn't redefine it.
        const bandColor = loadingColor(lineOverlay.loadingPct)
        const fmtVal = (mw: number) => showQ ? `${Math.abs(mw).toFixed(1)} MVAr` : fmtMW(Math.abs(mw))
        return (
          <EdgeLabelRenderer>
            <div
              className="nodrag nopan"
              style={{
                position: 'absolute',
                transform: `translate(-50%, -50%) translate(${midPoint.x}px, ${midPoint.y + (isLink || isTransformer ? 16 : 0)}px)`,
                pointerEvents: 'none',
                padding: '1px 6px',
                display: 'flex', alignItems: 'center', gap: 4,
                background: 'var(--color-bg)',
                border: `1px solid ${bandColor}`,
                borderRadius: 3,
                fontSize: 9, fontFamily: 'monospace', fontWeight: 600,
                color: bandColor,
                boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
                whiteSpace: 'nowrap',
              }}
              title={`${showQ ? 'q0' : 'p0'} = ${value.toFixed(2)} ${unit} (signed) · loading ${lineOverlay.loadingPct.toFixed(1)}% (by active power)`}
            >
              {/* SVG arrow — rotated to point at the receiving bus. */}
              <svg width="11" height="11" viewBox="0 0 11 11" style={{ display: 'block' }}>
                <g transform={`rotate(${angleDeg} 5.5 5.5)`}>
                  <line x1="1" y1="5.5" x2="9" y2="5.5" stroke={bandColor} strokeWidth="1.6" strokeLinecap="round" />
                  <polyline points="6.5,2.5 9.5,5.5 6.5,8.5" fill="none" stroke={bandColor} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </g>
              </svg>
              <span>{fmtVal(value)}</span>
              <span style={{ opacity: 0.65 }}>· {lineOverlay.loadingPct.toFixed(0)}%</span>
            </div>
          </EdgeLabelRenderer>
        )
      })()}

      {/* Link-edge results overlay badge. Mirrors the Line badge above:
          shows p0 (signed MW), direction arrow rotated to point at the
          receiving bus, loading %, and a carrier suffix so the user can
          tell H2 / heat / DC flows apart at a glance during playback. */}
      {linkOverlay && isLink && (() => {
        const mid = Math.floor((allPoints.length - 1) / 2)
        const a = allPoints[mid]
        const b = allPoints[mid + 1]
        const value = linkOverlay.p0
        let dx = b.x - a.x
        let dy = b.y - a.y
        if (value < 0) { dx = -dx; dy = -dy }  // arrow points downstream
        const angleDeg = (Math.abs(dx) + Math.abs(dy) > 0)
          ? Math.atan2(dy, dx) * 180 / Math.PI
          : 0
        const bandColor = loadingColor(linkOverlay.loadingPct)
        // Sit the badge ABOVE the line on link edges (Line badges sit on
        // the centreline). The 16-px offset matches the existing isLink
        // / isTransformer offset on the line badge so when both flavours
        // are on screen, they line up vertically.
        return (
          <EdgeLabelRenderer>
            <div
              className="nodrag nopan"
              style={{
                position: 'absolute',
                transform: `translate(-50%, -50%) translate(${midPoint.x}px, ${midPoint.y + 16}px)`,
                pointerEvents: 'none',
                padding: '1px 6px',
                display: 'flex', alignItems: 'center', gap: 4,
                background: 'var(--color-bg)',
                border: `1px solid ${bandColor}`,
                borderRadius: 3,
                fontSize: 9, fontFamily: 'monospace', fontWeight: 600,
                color: bandColor,
                boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
                whiteSpace: 'nowrap',
              }}
              title={`p0 = ${value.toFixed(2)} MW (signed) · loading ${linkOverlay.loadingPct.toFixed(1)}% · carrier ${linkOverlay.carrier || 'unspecified'}`}
            >
              <svg width="11" height="11" viewBox="0 0 11 11" style={{ display: 'block' }}>
                <g transform={`rotate(${angleDeg} 5.5 5.5)`}>
                  <line x1="1" y1="5.5" x2="9" y2="5.5" stroke={bandColor} strokeWidth="1.6" strokeLinecap="round" />
                  <polyline points="6.5,2.5 9.5,5.5 6.5,8.5" fill="none" stroke={bandColor} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </g>
              </svg>
              <span>{fmtMW(Math.abs(value))}</span>
              <span style={{ opacity: 0.65 }}>· {linkOverlay.loadingPct.toFixed(0)}%</span>
              {linkOverlay.carrier && (
                <span style={{ opacity: 0.45, fontWeight: 500 }}>· {linkOverlay.carrier}</span>
              )}
            </div>
          </EdgeLabelRenderer>
        )
      })()}
      {/* IEC two-interlocking-circles symbol at the midpoint of every
          transformer edge. Drawn in SVG (not via EdgeLabelRenderer) so it
          scales naturally with the React-Flow viewport zoom. */}
      {isTransformer && (() => {
        // IEC two-interlocking-circles symbol. Three improvements over the
        // earlier inline version:
        //  1. Sits at the TRUE geometric midpoint along the path length
        //     (via pathMidpoint) instead of the middle-segment midpoint
        //     — so it stays between the two buses even after waypoint edits.
        //  2. Rotated to align with the local line direction so on diagonal
        //     connections the circles sit perpendicular to the line, not
        //     stuck on a horizontal axis.
        //  3. Slightly larger radius (8px from 6px) and overlap proportional
        //     to radius, so it reads as one symbol at typical zoom levels.
        const r = 8
        const overlap = r * 0.6  // distance from center to each circle's center
        const deg = (pathMidpoint.angleRad * 180) / Math.PI
        return (
          <g
            pointerEvents="none"
            transform={`translate(${pathMidpoint.x} ${pathMidpoint.y}) rotate(${deg})`}
          >
            <circle cx={-overlap} cy={0} r={r}
              fill="#ffffff" stroke={color} strokeWidth={1.6} />
            <circle cx={overlap}  cy={0} r={r}
              fill="#ffffff" stroke={color} strokeWidth={1.6} />
          </g>
        )
      })()}
    </>
  )
}


// ── Bus parameter editor panel ────────────────────────────────────────────────
interface BusEditorProps {
  busName: string
  anchorX: number
  anchorY: number
  bus: Bus
  allBuses: Bus[]
  getConnectedCount: (busName: string) => { total: number; lines: number; links: number; generators: number; loads: number; storage: number }
  onApply: (fields: Partial<Bus>) => void
  onClose: () => void
}

function BusEditor({ busName, anchorX, anchorY, bus, allBuses, getConnectedCount, onApply, onClose }: BusEditorProps) {
  const [name, setName]           = useState(bus.name ?? '')
  const [vNom, setVNom]           = useState(bus.v_nom != null ? String(bus.v_nom) : '')
  const [bx, setBx]               = useState(bus.x != null ? String(bus.x) : '')
  const [by, setBy]               = useState(bus.y != null ? String(bus.y) : '')
  const [control, setControl]     = useState(bus.control ?? 'PQ')
  const [carrier, setCarrier]     = useState(bus.carrier ?? 'AC')
  const [subNet, setSubNet]       = useState(bus.sub_network ?? '')
  const [errors, setErrors]       = useState<Record<string, string>>({})

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const isRename = name.trim() !== busName
  const impact = isRename ? getConnectedCount(busName) : null

  const PANEL_W = 260
  const left = anchorX + 24 + PANEL_W > window.innerWidth ? anchorX - PANEL_W - 12 : anchorX + 12
  const top  = Math.max(10, Math.min(anchorY - 24, window.innerHeight - 500))

  const validate = () => {
    const errs: Record<string, string> = {}
    if (!name.trim())              errs.name  = 'Name cannot be empty'
    if (name.trim().length > 64)   errs.name  = 'Name cannot exceed 64 chars'
    if (isRename && allBuses.some(b => b.name === name.trim())) {
      errs.name = `Bus "${name.trim()}" already exists`
    }
    if (vNom !== '') {
      const n = parseFloat(vNom)
      if (isNaN(n) || n <= 0)      errs.v_nom = 'Voltage must be a positive number'
    }
    if (bx !== '' && !isFinite(parseFloat(bx))) errs.x = 'Must be a valid number'
    if (by !== '' && !isFinite(parseFloat(by))) errs.y = 'Must be a valid number'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const handleApply = () => {
    if (!validate()) return
    const fields: Partial<Bus> = { name: name.trim(), carrier, control: control as Bus['control'] }
    if (vNom !== '') fields.v_nom = parseFloat(vNom)
    if (bx   !== '') fields.x     = parseFloat(bx)
    if (by   !== '') fields.y     = parseFloat(by)
    if (subNet !== '') fields.sub_network = subNet
    onApply(fields)
  }

  const inp = 'w-full rounded border border-border px-2 py-1 text-xs outline-none focus:border-[#2f81f7] focus:ring-1 focus:ring-[#2f81f7]/30'
  const lbl = 'block text-[11px] font-medium text-text mb-0.5'
  const err = 'text-[10px] text-danger mt-0.5'

  return (
    <div
      className="fixed z-[9999] bg-bg rounded-[10px] shadow-xl select-none"
      style={{ width: PANEL_W, left, top, border: '1px solid #d1d5db', boxShadow: '0 6px 24px rgba(0,0,0,0.13)', padding: 16 }}
      onMouseDown={e => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-text truncate">{busName}</span>
        <button onClick={onClose} aria-label="Close bus editor" className="text-muted hover:text-text text-base leading-none ml-2">×</button>
      </div>

      <div className="flex flex-col gap-2.5">
        {/* Name */}
        <div>
          <label className={lbl}>Name</label>
          <input className={inp} value={name} onChange={e => setName(e.target.value)} />
          {errors.name && <p className={err}>{errors.name}</p>}
          {!errors.name && impact && impact.total > 0 && (
            <p className="text-[10px] text-muted mt-0.5 leading-tight">
              Renaming updates {impact.total} component{impact.total !== 1 ? 's' : ''} (
              {[
                impact.lines   > 0 && `${impact.lines} line${impact.lines   !== 1 ? 's' : ''}`,
                impact.links   > 0 && `${impact.links} link${impact.links   !== 1 ? 's' : ''}`,
                impact.generators > 0 && `${impact.generators} gen${impact.generators !== 1 ? 's' : ''}`,
                impact.loads   > 0 && `${impact.loads} load${impact.loads   !== 1 ? 's' : ''}`,
                impact.storage > 0 && `${impact.storage} storage`,
              ].filter(Boolean).join(', ')})
            </p>
          )}
        </div>
        {/* Voltage */}
        {bus.v_nom != null && (
          <div>
            <label className={lbl}>Voltage (kV)</label>
            <input className={inp} type="number" min="0" step="0.1" value={vNom} onChange={e => setVNom(e.target.value)} />
            {errors.v_nom && <p className={err}>{errors.v_nom}</p>}
          </div>
        )}
        {/* Coordinates */}
        {bus.x != null && (
          <div>
            <label className={lbl}>X — longitude</label>
            <input className={inp} type="number" step="0.01" value={bx} onChange={e => setBx(e.target.value)} />
            {errors.x && <p className={err}>{errors.x}</p>}
          </div>
        )}
        {bus.y != null && (
          <div>
            <label className={lbl}>Y — latitude</label>
            <input className={inp} type="number" step="0.01" value={by} onChange={e => setBy(e.target.value)} />
            {errors.y && <p className={err}>{errors.y}</p>}
          </div>
        )}
        {/* Carrier — unified dropdown (groups from n.carriers + PyPSA-Eur
            catalog). Was previously a free-text datalist with 5 hardcoded
            options, which let users invent typo'd carriers like "Ac" or
            "Heatt" with no validation; the dropdown forces a pick from
            either the project's carriers or the curated catalog. */}
        <CarrierSelect
          label="Carrier"
          value={carrier}
          onChange={setCarrier}
          wrapperClassName=""
          className="w-full"
        />
        {/* Control */}
        {bus.control != null && (
          <div>
            <label className={lbl}>Control mode</label>
            <select className={inp} value={control} onChange={e => setControl(e.target.value)}>
              <option value="PQ">PQ</option>
              <option value="PV">PV</option>
              <option value="Slack">Slack</option>
            </select>
          </div>
        )}
        {/* Sub-network */}
        {bus.sub_network != null && (
          <div>
            <label className={lbl}>Sub-network</label>
            <input className={inp} value={subNet} onChange={e => setSubNet(e.target.value)} />
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex justify-end gap-2 mt-4">
        <button
          onClick={onClose}
          className="px-3 py-1 text-xs rounded border border-border text-text hover:bg-panel transition-colors"
        >Cancel</button>
        <button
          onClick={handleApply}
          className="px-3 py-1 text-xs rounded bg-[#2f81f7] text-white hover:bg-[#2563eb] transition-colors"
        >Apply</button>
      </div>
    </div>
  )
}


// ── New Connection Dialog (Connect Buses mode) ────────────────────────────────
// Single dialog that creates either a Line or a Transformer between two
// already-picked buses. The Type radio at the top swaps the field set; the
// transformer branch validates voltages against the connected buses before
// submit so the user gets immediate feedback (the backend re-validates).

type ConnectionKind = 'line' | 'transformer'

interface LineConnectionParams {
  kind: 'line'
  name: string; carrier: 'AC' | 'DC'; s_nom: number; x: number; length: number
}
interface TransformerConnectionParams {
  kind: 'transformer'
  name: string; type: string
  v_nom_0: number; v_nom_1: number
  s_nom: number; x: number; tap_ratio: number
}
type ConnectionParams = LineConnectionParams | TransformerConnectionParams

interface NewConnectionDialogProps {
  source: string; target: string
  sourceVNom: number; targetVNom: number
  anchorX: number; anchorY: number
  onConfirm: (params: ConnectionParams) => void
  onClose: () => void
}

const TRANSFORMER_PRESETS_UI: Record<string, { v_nom_0: string; v_nom_1: string; s_nom: string; x: string }> = {
  '380/220 kV': { v_nom_0: '380', v_nom_1: '220', s_nom: '600', x: '0.08' },
  '380/110 kV': { v_nom_0: '380', v_nom_1: '110', s_nom: '600', x: '0.10' },
  '380/132 kV': { v_nom_0: '380', v_nom_1: '132', s_nom: '600', x: '0.10' },
  '220/110 kV': { v_nom_0: '220', v_nom_1: '110', s_nom: '300', x: '0.10' },
  '220/132 kV': { v_nom_0: '220', v_nom_1: '132', s_nom: '300', x: '0.10' },
  '132/33 kV':  { v_nom_0: '132', v_nom_1: '33',  s_nom: '100', x: '0.12' },
  '132/20 kV':  { v_nom_0: '132', v_nom_1: '20',  s_nom: '60',  x: '0.12' },
  '110/33 kV':  { v_nom_0: '110', v_nom_1: '33',  s_nom: '80',  x: '0.12' },
  '110/20 kV':  { v_nom_0: '110', v_nom_1: '20',  s_nom: '60',  x: '0.12' },
  '33/11 kV':   { v_nom_0: '33',  v_nom_1: '11',  s_nom: '25',  x: '0.10' },
  '20/0.4 kV':  { v_nom_0: '20',  v_nom_1: '0.4', s_nom: '1',   x: '0.06' },
}

function NewConnectionDialog({
  source, target, sourceVNom, targetVNom, anchorX, anchorY, onConfirm, onClose,
}: NewConnectionDialogProps) {
  const [kind, setKind] = useState<ConnectionKind>('line')
  const [name, setName] = useState('')
  // Line fields
  const [carrier, setCarrier] = useState<'AC' | 'DC'>('AC')
  const [sNom, setSNom] = useState('100')
  const [reactance, setReactance] = useState('0.1')
  const [length, setLength] = useState('1')
  // Transformer fields
  const [trType, setTrType] = useState('Custom')
  const [trVNom0, setTrVNom0] = useState(sourceVNom > 0 ? String(sourceVNom) : '')
  const [trVNom1, setTrVNom1] = useState(targetVNom > 0 ? String(targetVNom) : '')
  const [trSNom, setTrSNom] = useState('100')
  const [trX, setTrX] = useState('0.1')
  const [trTapRatio, setTrTapRatio] = useState('1.0')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Picking a preset snaps v_nom and typical electrical defaults; "Custom"
  // leaves the fields untouched so the user can type manually.
  const onTypeChange = (v: string) => {
    setTrType(v)
    const preset = TRANSFORMER_PRESETS_UI[v]
    if (preset) {
      setTrVNom0(preset.v_nom_0); setTrVNom1(preset.v_nom_1)
      setTrSNom(preset.s_nom); setTrX(preset.x)
    }
  }

  // Voltage-mismatch warning for the transformer flow. Either orientation is
  // acceptable (high/low can be on either bus). Tolerance mirrors the backend
  // (_VNOM_TOL_KV) so the UI feedback agrees with what the API would accept.
  const vNom0Num = parseFloat(trVNom0)
  const vNom1Num = parseFloat(trVNom1)
  const TOL = 0.5
  const sameOrientation =
    Math.abs(sourceVNom - vNom0Num) <= TOL && Math.abs(targetVNom - vNom1Num) <= TOL
  const swappedOrientation =
    Math.abs(sourceVNom - vNom1Num) <= TOL && Math.abs(targetVNom - vNom0Num) <= TOL
  const sameBusVoltages = Math.abs(sourceVNom - targetVNom) <= TOL
  const voltageOK = !isFinite(vNom0Num) || !isFinite(vNom1Num) || vNom0Num <= 0 || vNom1Num <= 0
                    || sameOrientation || swappedOrientation
  const trDisableSubmit = kind === 'transformer' && !voltageOK

  const W = 260
  const left = anchorX + W > window.innerWidth ? anchorX - W - 8 : anchorX + 8
  const top  = Math.max(8, Math.min(anchorY - 20, window.innerHeight - 460))

  const inp = 'w-full rounded border border-border px-2 py-1 text-xs outline-none focus:border-[#2f81f7] focus:ring-1 focus:ring-[#2f81f7]/30'

  const submit = () => {
    if (kind === 'line') {
      onConfirm({
        kind: 'line',
        name: name.trim() || `${source}–${target}`,
        carrier,
        s_nom: parseFloat(sNom) || 100,
        x: parseFloat(reactance) || 0.1,
        length: parseFloat(length) || 1,
      })
    } else {
      onConfirm({
        kind: 'transformer',
        name: name.trim() || `${source}–${target}`,
        type: trType === 'Custom' ? '' : trType,
        v_nom_0: parseFloat(trVNom0) || 0,
        v_nom_1: parseFloat(trVNom1) || 0,
        s_nom: parseFloat(trSNom) || 100,
        x: parseFloat(trX) || 0.1,
        tap_ratio: parseFloat(trTapRatio) || 1.0,
      })
    }
  }

  return (
    <div className="fixed z-[9999] bg-bg rounded-[10px] shadow-xl select-none"
      style={{ width: W, left, top, border: '1px solid #d1d5db', padding: 14 }}
      onMouseDown={e => e.stopPropagation()}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold text-text">{kind === 'line' ? 'New Line' : 'New Transformer'}</span>
        <button onClick={onClose} aria-label="Close new connection dialog" className="text-muted hover:text-text"><XIcon size={13} /></button>
      </div>
      <div className="text-[10px] text-muted mb-2.5 leading-snug">
        <span className="font-medium text-text">{source}</span>
        {sourceVNom > 0 && <span className="text-muted"> ({sourceVNom} kV)</span>}
        {' → '}
        <span className="font-medium text-text">{target}</span>
        {targetVNom > 0 && <span className="text-muted"> ({targetVNom} kV)</span>}
      </div>

      {/* Type radio */}
      <div className="flex items-center gap-2 mb-2.5 p-0.5 bg-panel rounded">
        {(['line', 'transformer'] as const).map(k => (
          <button
            key={k}
            onClick={() => setKind(k)}
            className={`flex-1 py-1 text-[11px] font-medium rounded transition-colors ${
              kind === k ? 'bg-bg text-text shadow-sm' : 'text-muted hover:text-text'
            }`}
          >
            {k === 'line' ? 'Line' : 'Transformer'}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-muted">Name</span>
          <input className={inp} placeholder={`${source}–${target}`} value={name} onChange={e => setName(e.target.value)} />
        </label>

        {kind === 'line' && (
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Carrier</span>
              <select className={inp} value={carrier} onChange={e => setCarrier(e.target.value as 'AC' | 'DC')}>
                <option value="AC">AC</option>
                <option value="DC">DC</option>
              </select>
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">s_nom (MVA)</span>
              <input className={inp} type="number" step="any" value={sNom} onChange={e => setSNom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Reactance x (pu)</span>
              <input className={inp} type="number" step="any" value={reactance} onChange={e => setReactance(e.target.value)} />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Length (km)</span>
              <input className={inp} type="number" step="any" value={length} onChange={e => setLength(e.target.value)} />
            </label>
          </div>
        )}

        {kind === 'transformer' && (
          <>
            <label className="flex flex-col gap-0.5">
              <span className="text-[10px] text-muted">Voltage step</span>
              <select className={inp} value={trType} onChange={e => onTypeChange(e.target.value)}>
                {Object.keys(TRANSFORMER_PRESETS_UI).map(p => <option key={p} value={p}>{p}</option>)}
                <option value="Custom">Custom…</option>
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-muted">v_nom₀ (kV)</span>
                <input className={inp} type="number" step="any" value={trVNom0} onChange={e => { setTrVNom0(e.target.value); setTrType('Custom') }} />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-muted">v_nom₁ (kV)</span>
                <input className={inp} type="number" step="any" value={trVNom1} onChange={e => { setTrVNom1(e.target.value); setTrType('Custom') }} />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-muted">s_nom (MVA)</span>
                <input className={inp} type="number" step="any" value={trSNom} onChange={e => setTrSNom(e.target.value)} />
              </label>
              <label className="flex flex-col gap-0.5">
                <span className="text-[10px] text-muted">Reactance x (pu)</span>
                <input className={inp} type="number" step="any" value={trX} onChange={e => setTrX(e.target.value)} />
              </label>
              <label className="flex flex-col gap-0.5 col-span-2">
                <span className="text-[10px] text-muted">Tap ratio</span>
                <input className={inp} type="number" step="any" value={trTapRatio} onChange={e => setTrTapRatio(e.target.value)} />
              </label>
            </div>
            {!voltageOK && (
              <div className="text-[10px] text-danger bg-danger/10 border border-danger/30 rounded px-2 py-1.5 leading-snug">
                <strong>Voltage mismatch.</strong> The transformer expects {trVNom0 || '?'}/{trVNom1 || '?'} kV
                but bus '{source}' is {sourceVNom} kV and '{target}' is {targetVNom} kV.
              </div>
            )}
            {voltageOK && sameBusVoltages && (
              <div className="text-[10px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-1.5 leading-snug">
                Both buses are {sourceVNom} kV — this models an isolation or phase-shifting transformer.
              </div>
            )}
          </>
        )}

        <div className="flex gap-2 mt-1">
          <button
            onClick={submit}
            disabled={trDisableSubmit}
            className="flex-1 py-1.5 bg-[#2f81f7] text-white rounded text-xs font-semibold hover:bg-[#1a6fd6] disabled:opacity-50 disabled:hover:bg-[#2f81f7] transition-colors"
          >
            {kind === 'line' ? 'Add Line' : 'Add Transformer'}
          </button>
          <button onClick={onClose}
            className="px-3 py-1.5 border border-border rounded text-xs text-text hover:bg-panel transition-colors">
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

const nodeTypes = { bus: BusNode, assetGroup: AssetGroupNode }
const edgeTypes = { network: EditableEdge }

// ── Results pill ───────────────────────────────────────────────────────────────
// Bottom-centre frosted pill mirroring the design's canvas status indicator.
// Idle → clock + prompt; running → spinner; completed → green check + the
// objective value (mono); failed / aborted → muted error line. Reads
// simulationStore directly so it tracks the header Run button with no
// prop-drilling through the (large) main canvas component.
function ResultsPill() {
  const status = useSimulationStore(s => s.status)
  const objective = useSimulationStore(s => s.objective)
  const solveTime = useSimulationStore(s => s.solveTime)

  const fmtObj = (v: number) => {
    const a = Math.abs(v)
    if (a >= 1e9) return `€${(v / 1e9).toFixed(2)} B`
    if (a >= 1e6) return `€${(v / 1e6).toFixed(2)} M`
    if (a >= 1e3) return `€${(v / 1e3).toFixed(1)} k`
    return `€${v.toFixed(0)}`
  }

  return (
    <div
      className="flex items-center gap-1.5 border border-border rounded-full px-3 py-1.5 shadow-md text-[11px]"
      style={{ background: 'color-mix(in srgb, var(--color-bg) 92%, transparent)', backdropFilter: 'blur(8px)' }}
    >
      {status === 'running' ? (
        <>
          <Loader2 size={12} className="animate-spin text-accent" />
          <span className="font-medium text-text">Solving…</span>
        </>
      ) : status === 'completed' ? (
        <>
          <CheckCircle2 size={12} className="text-success" />
          <span className="font-medium text-text">Results</span>
          {objective != null && (
            <span className="font-mono text-text tabular-nums">{fmtObj(objective)}</span>
          )}
          {solveTime != null && (
            <span className="font-mono text-muted tabular-nums">· {solveTime.toFixed(2)} s</span>
          )}
        </>
      ) : status === 'failed' ? (
        <>
          <XCircle size={12} className="text-danger" />
          <span className="font-medium text-danger">Solve failed</span>
        </>
      ) : status === 'aborted' ? (
        <>
          <XCircle size={12} className="text-muted" />
          <span className="font-medium text-muted">Solve aborted</span>
        </>
      ) : (
        <>
          <Clock size={12} className="text-muted" />
          <span className="font-medium text-text">Results</span>
          <span className="text-muted">· Run a simulation to enable</span>
        </>
      )}
    </div>
  )
}

// ── Main canvas ────────────────────────────────────────────────────────────────
export default function TopologyCanvas() {
  const {
    setSelectedComponent, resultsOverlayEnabled, setResultsOverlay, canvasMode, setCanvasMode,
    highlightedComponent, setHighlightedComponent,
    resultSource, setResultSource, resultsSnapshotIdx,
    flowOverlayKind, setFlowOverlayKind,
    pendingNodePosition, setPendingNodePosition,
    currentProject,
  } = useUIStore()
  const queryClient = useQueryClient()

  // Stage 2 (AC PF) status — drives the LOPF / AC PF source toggle in the
  // canvas toolbar AND the non-converged banner at the top of the canvas.
  // null ⇒ status endpoint hasn't responded yet (treat as unavailable so
  // the toggle stays hidden). Refetch via the SSE done-handler invalidation
  // ('results' prefix matches this key).
  const { data: acPfStatus } = useQuery({
    queryKey: nk(currentProject, 'results', 'ac_pf', 'status'), queryFn: resultsApi.getAcPfStatus,
  })
  const acPfAvailable = !!acPfStatus?.available
  // Snapshots payload — used to look up the ISO key for the currently-selected
  // snapshot so we can correlate it to the convergence map.
  const { data: snap } = useQuery({ queryKey: nk(currentProject, 'snapshots'), queryFn: networkApi.getSnapshots })
  const currentSnapIso = snap?.snapshots?.[Math.min(resultsSnapshotIdx, (snap?.count ?? 1) - 1)] ?? ''
  const currentSnapConverged = !acPfAvailable || resultSource !== 'ac_pf'
    ? true
    : (acPfStatus?.converged_per_snapshot?.[currentSnapIso] ?? true)

  const { data: buses = [] }      = useQuery({ queryKey: nk(currentProject, 'buses'),         queryFn: networkApi.getBuses })
  const { data: lines = [] }      = useQuery({ queryKey: nk(currentProject, 'lines'),         queryFn: networkApi.getLines })
  const { data: links = [] }      = useQuery({ queryKey: nk(currentProject, 'links'),         queryFn: networkApi.getLinks })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'), queryFn: networkApi.getTransformers })
  const { data: generators = [] } = useQuery({ queryKey: nk(currentProject, 'generators'),    queryFn: networkApi.getGenerators })
  const { data: loads = [] }      = useQuery({ queryKey: nk(currentProject, 'loads'),         queryFn: networkApi.getLoads })
  const { data: sus = [] }        = useQuery({ queryKey: nk(currentProject, 'storage_units'), queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }     = useQuery({ queryKey: nk(currentProject, 'stores'),        queryFn: networkApi.getStores })

  const saveBusMut = useMutation({
    mutationFn: ({ name, fields }: { name: string; fields: Partial<Bus> }) => {
      // Spread the full cached bus so the backend's remove+add cycle
      // (in `_update_component`) doesn't reset the bus's other fields
      // (`v_mag_pu_min/max/set`, `country`, `unit`, `sub_network`, …) to
      // schema defaults. The canvas BusEditor only surfaces a subset of
      // bus fields; without the spread, editing voltage on the schematic
      // would wipe everything else. Same pattern as PropertiesPanel.BusPanel.
      const cachedBuses = queryClient.getQueryData<Bus[]>(nk(useUIStore.getState().currentProject, 'buses')) ?? []
      const current = cachedBuses.find(b => b.name === name)
      if (!current) {
        // Refuse the partial PUT on a cache miss — degrading to `fields` alone
        // would let the backend's remove+add reset every omitted bus field to
        // its schema default. The BusEditor only opens on a rendered (loaded)
        // bus, so this is near-unreachable; throw rather than corrupt, matching
        // MapCanvas's drag handler.
        throw new Error(`Bus '${name}' not loaded yet — try the edit again in a moment.`)
      }
      const body: Partial<Bus> = { ...current, ...fields }
      return networkApi.updateBus(name, body)
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'buses') }),
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : 'Failed to update bus'),
  })

  // Cache of user-dragged positions — updated in onNodesChange, never triggers re-renders
  const posCache = useRef<Record<string, { x: number; y: number }>>({})
  const rfInstance = useRef<{
    fitView: (opts?: { padding?: number; duration?: number; maxZoom?: number; minZoom?: number }) => void
    flowToScreenPosition: (pos: { x: number; y: number }) => { x: number; y: number }
    setCenter: (x: number, y: number, opts?: { duration?: number; zoom?: number }) => void
    getViewport: () => { x: number; y: number; zoom: number }
    zoomIn: () => void; zoomOut: () => void
  } | null>(null)
  const hasFitted = useRef(false)
  // The layoutEpoch this canvas last APPLIED `savedStateRef` for. A plain
  // boolean "layoutComputed" latch can't tell "laid out project A" from
  // "laid out project B" — on a same-canvas project switch it stayed `true`
  // and stranded the new project's buses at the {400,400} fallback. Keying
  // off the epoch instead means: re-apply the saved layout exactly once per
  // freshly-loaded layout document, while uncached buses are ALWAYS seeded.
  const layoutSeededEpoch = useRef(-1)
  // Mirror of `layoutSeededEpoch` for the edge-sync effect. When a fresh layout
  // is loaded (project switch bumps `layoutEpoch` + repoints `savedStateRef`),
  // the next edge-sync makes the new saved state AUTHORITATIVE — dropping
  // waypoints for any edge ID not present in it, so a stale in-memory edge
  // object (same ID across projects, e.g. `line-AC1`) can't keep the previous
  // project's waypoints. On plain edge-signature changes WITHIN the same epoch
  // (v_nom colour refresh, add/remove), in-memory waypoints still win so an
  // in-session drag is never clobbered.
  const edgeSeededEpoch = useRef(-1)
  // Seed from the module-level cache first (survives a view-switch unmount),
  // falling back to localStorage for the unsaved / no-project case. This makes
  // the schematic correct on the FIRST render after a map→blank switch.
  const savedStateRef = useRef<PersistedState | null>(
    layoutMemCache.get(layoutCacheKey(currentProject)) ?? loadDiagramState(currentProject),
  )
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Server-side latent layout load ──────────────────────────────────────
  // `layoutEpoch` is bumped whenever a fresh layout document is loaded for the
  // active project. It is a dependency of the `allRfNodes` memo and the
  // node-sync effect so a project switch (which doesn't remount this
  // component) re-seeds posCache from the new project's layout.json.
  // `layoutLoadedFor` guards against re-fetching when currentProject is
  // unchanged (the store selector re-renders on many unrelated updates).
  const [layoutEpoch, setLayoutEpoch] = useState(0)
  // Track which project's layout has been SUCCESSFULLY applied to the canvas
  // — NOT just which project we started fetching for. The distinction matters
  // under React StrictMode (dev): the effect runs twice on mount and the
  // first cleanup cancels its in-flight fetch. If we set this ref eagerly
  // at the start of the effect, the second invocation early-returns and we
  // never fetch again — savedStateRef stays at its initial value (stale
  // localStorage or null), the layout from layout.json never lands on the
  // canvas, and the user sees "positions lost after refresh".
  //
  // Setting this ref ONLY after a successful fetch resolution means the
  // strict-mode second invocation correctly re-fetches, and the OTHER
  // optimisation (skip when same project) still kicks in for legitimate
  // re-renders that aren't a project switch.
  const layoutLoadedFor = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (layoutLoadedFor.current === currentProject) {
      return
    }
    // Wipe posCache SYNCHRONOUSLY (before the async fetch) so the previous
    // project's stale positions can't leak onto same-named buses if the
    // `['buses']` refetch resolves before our layout fetch does.
    posCache.current = {}
    // Re-point savedStateRef at the NEW project SYNCHRONOUSLY: module cache →
    // the new project's per-project localStorage → null. This is authoritative
    // — it must NEVER leave the previous project's value in place, or the new
    // project inherits the old one's edge waypoints (lines/links/transformers
    // all share `EdgeData.waypoints`) until the async fetch resolves, and
    // worse keeps them if the server has no layout. Bump the epoch so the
    // node-sync (allRfNodes) and edge-sync effects re-apply the new state.
    savedStateRef.current =
      layoutMemCache.get(layoutCacheKey(currentProject))
      ?? loadDiagramState(currentProject)
      ?? null
    setLayoutEpoch(e => e + 1)
    let cancelled = false
    fetchLayoutFor(currentProject).then(ps => {
      if (cancelled) return
      // The server is authoritative for a project's layout. Apply the fetched
      // layout when present; otherwise fall back to the new project's own
      // sources — module cache (an in-session drag not yet round-tripped to
      // layout.json) then per-project localStorage (offline / not-yet-synced)
      // — else null. So a project with no/empty server layout shows ITS OWN
      // state or none, never the previous project's. All fallbacks are keyed to
      // `currentProject`, so none can be the prior project's value. Always
      // re-assign + bump the epoch.
      const resolved = ps
        ?? layoutMemCache.get(layoutCacheKey(currentProject))
        ?? loadDiagramState(currentProject)
        ?? null
      if (resolved) layoutMemCache.set(layoutCacheKey(currentProject), resolved)
      savedStateRef.current = resolved
      posCache.current = {}
      // Bumping the epoch makes `layoutSeededEpoch.current !== layoutEpoch`,
      // so the next allRfNodes pass re-applies this freshly-loaded layout.
      setLayoutEpoch(e => e + 1)
      // Mark as loaded AFTER the application succeeds — the
      // strict-mode early-return at the top of the effect now correctly
      // gates on "we already wrote the right state", not on "we already
      // started a fetch that may have been cancelled".
      layoutLoadedFor.current = currentProject
    }).catch((e) => {
      console.warn('[layout-load] fetch failed', { currentProject, error: e })
    })
    return () => { cancelled = true }
  }, [currentProject])

  // Which asset group node IDs are currently visible (hidden by default)
  const [visibleGroups, setVisibleGroups] = useState<Set<string>>(new Set())
  const [overlappingNodes, setOverlappingNodes] = useState<Set<string>>(new Set())
  const [busEditor, setBusEditor] = useState<{ busName: string; anchorX: number; anchorY: number } | null>(null)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const resetConfirmTitleId = useId()

  // Connect Buses mode state
  const [connectSource, setConnectSource] = useState<string | null>(null)
  const [connectMousePos, setConnectMousePos] = useState<{ x: number; y: number } | null>(null)
  interface NewLineDialogState { source: string; target: string; anchorX: number; anchorY: number }
  const [newLineDialog, setNewLineDialog] = useState<NewLineDialogState | null>(null)

  // Clear connectSource whenever the canvas leaves connect mode. Without this,
  // a bus clicked as the connect-mode source keeps its infinite `animate-ping`
  // ring after the user switches modes via the mode-button, the 'V' / Escape
  // shortcut, or any other path that isn't the explicit right-click cancel
  // (onPaneContextMenu). Symptom: bus pulses green continuously after the
  // user moves on, with no way to dismiss except pane click.
  useEffect(() => {
    if (canvasMode !== 'connect' && connectSource) {
      setConnectSource(null)
      setConnectMousePos(null)
    }
  }, [canvasMode, connectSource])

  // Pending undo-deletes (line/link removals with 5s undo window).
  // Track the toast id alongside the timer so the unmount-flush below
  // can dismiss the stale "deleted" toast — otherwise its Undo button
  // outlives the component and points at a ref that's no longer wired
  // up to anything (clicking it after unmount silently noops).
  const pendingDeletesRef = useRef<Map<string, { timer: ReturnType<typeof setTimeout>; toastId?: string }>>(new Map())

  interface CtxMenu {
    x: number; y: number; busName: string
    categories: { cat: AssetCategory; count: number }[]
  }
  const [contextMenu, setContextMenu] = useState<CtxMenu | null>(null)

  // Close bus context menu when clicking elsewhere
  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [contextMenu])

  // ── Edge right-click context menu ─────────────────────────────────────────
  const [edgeCtxMenu, setEdgeCtxMenu] = useState<{ edgeId: string; x: number; y: number } | null>(null)

  const [nodes, setNodes, _onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange]  = useEdgesState<Edge<EdgeData>>([])
  const nodesRef = useRef<Node[]>(nodes)
  useEffect(() => { nodesRef.current = nodes }, [nodes])
  const edgesRef = useRef<Edge<EdgeData>[]>(edges)
  useEffect(() => { edgesRef.current = edges }, [edges])

  const scheduleSave = useCallback(() => {
    const nodeEntries: PersistedNode[] = nodesRef.current
      .filter(n => !n.id.startsWith('assetgrp-'))
      .map(n => ({ id: n.id, canvasX: n.position.x, canvasY: n.position.y }))
    const edgeEntries: PersistedEdge[] = edgesRef.current
      .filter(e => !e.id.startsWith('assetedge-'))
      .map(e => {
        const d = e.data as unknown as EdgeData
        return { id: e.id, waypoints: d?.waypoints ?? [], history: d?.history ?? [[]] }
      })
    const state: PersistedState = {
      version: STORAGE_VERSION, savedAt: Date.now(), nodes: nodeEntries, edges: edgeEntries,
    }
    // Read currentProject FRESH from the store — this callback is created once
    // (empty deps) so a closed-over value would be stale after a project switch.
    const proj = useUIStore.getState().currentProject
    // Capture into the module cache SYNCHRONOUSLY so the layout survives a
    // TopologyCanvas unmount (a blank↔map view switch) even when the switch
    // happens inside the 300 ms debounce window below.
    layoutMemCache.set(layoutCacheKey(proj), state)
    // Debounced server write — persistLayoutFor routes to layout.json when a
    // project is active, localStorage otherwise.
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      persistLayoutFor(proj, state)
    }, 300)
  }, [])

  // Flush a pending debounced save on unmount (e.g. switching to the map
  // canvas) so a layout change made inside the 300ms window still reaches the
  // server — the module cache already holds it, this just persists it.
  useEffect(() => () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      const proj = useUIStore.getState().currentProject
      const pending = layoutMemCache.get(layoutCacheKey(proj))
      if (pending) persistLayoutFor(proj, pending)
    }
  }, [])

  // Page-unload flush: when the user hits F5 / closes the tab, the React
  // unmount handler above runs BUT the axios PUT it triggers is cancelled
  // mid-flight because the page is navigating away. Without this listener,
  // a drag landed inside the 300ms debounce window — or even a drag the
  // debounce did fire for that hasn't yet reached the server — is silently
  // lost, and the next session re-seeds from a stale layout.json (or the
  // layout algorithm). `pagehide` is the modern, reliable cross-browser
  // signal for "this page is being unloaded" — including bfcache restore,
  // tab close, navigation, and F5. It fires synchronously, so we use a
  // network call that survives the unload (`fetch keepalive`).
  useEffect(() => {
    const onPageHide = () => {
      const proj = useUIStore.getState().currentProject
      // Always include whatever is currently in the cache (last drag's
      // payload was written synchronously by scheduleSave, even when the
      // debounced PUT hasn't fired yet). Falling back to localStorage too,
      // for the no-project case.
      const pending = layoutMemCache.get(layoutCacheKey(proj))
      if (pending) {
        // Clear any in-flight debounce — its fetch is about to be cancelled
        // anyway and the keepalive write below supersedes it.
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
        persistLayoutOnUnload(proj, pending)
      }
      // Flush pending edge-deletes (5s undo window) via fetch keepalive.
      // The unmount-flush effect uses axios, which the browser cancels on
      // page unload (F5 / tab close) — so a delete the user committed to
      // (by navigating away within the 5s window) would silently survive
      // on the backend. keepalive hands off to the browser's background
      // network slot so the DELETE completes even after the page is gone.
      const pendingDeletes = pendingDeletesRef.current
      if (pendingDeletes.size > 0) {
        pendingDeletes.forEach(({ timer }, edgeId) => {
          clearTimeout(timer)
          const isLink = edgeId.startsWith('link-')
          const name = edgeId.replace(/^(line-|link-)/, '')
          const url = `/api/network/${isLink ? 'links' : 'lines'}/${encodeURIComponent(name)}`
          try {
            fetch(url, {
              method: 'DELETE',
              // This site had no headers object at all — raw fetch bypasses the
              // axios CSRF interceptor, so the pending delete 403s on unload.
              headers: { ...rawFetchHeaders('DELETE') },
              keepalive: true,
            }).catch(() => { /* best effort */ })
          } catch { /* keepalive unsupported — drop on the floor */ }
        })
        pendingDeletes.clear()
      }
    }
    window.addEventListener('pagehide', onPageHide)
    // Some browsers (notably Safari) don't always fire `pagehide` on
    // navigation; `beforeunload` covers the rest. Both can fire — the
    // operation is idempotent so a double-write is harmless.
    window.addEventListener('beforeunload', onPageHide)
    return () => {
      window.removeEventListener('pagehide', onPageHide)
      window.removeEventListener('beforeunload', onPageHide)
    }
  }, [])

  const edgeMenuCtxValue = useMemo<EdgeMenuCtx>(() => ({
    open: (edgeId, x, y) => setEdgeCtxMenu({ edgeId, x, y }),
    close: () => setEdgeCtxMenu(null),
    scheduleSave,
  }), [scheduleSave])

  const connectModeCtxValue = useMemo<ConnectModeCtx>(() => ({
    active: canvasMode === 'connect',
    sourceId: connectSource,
  }), [canvasMode, connectSource])

  const createLineMut = useMutation({
    mutationFn: (params: { name: string; bus0: string; bus1: string; carrier: 'AC' | 'DC'; s_nom: number; x: number; length: number }) =>
      networkApi.createLine({ name: params.name, bus0: params.bus0, bus1: params.bus1, carrier: params.carrier, s_nom: params.s_nom, x: params.x, length: params.length, r: 0 } as Partial<Line>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
      toast.success('Line added')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const createTransformerMut = useMutation({
    mutationFn: (params: { name: string; bus0: string; bus1: string; type: string;
      v_nom_0: number; v_nom_1: number; s_nom: number; x: number; tap_ratio: number }) =>
      networkApi.createTransformer({
        name: params.name, bus0: params.bus0, bus1: params.bus1,
        type: params.type, s_nom: params.s_nom, x: params.x, tap_ratio: params.tap_ratio,
        v_nom_0: params.v_nom_0, v_nom_1: params.v_nom_1,
      } as Partial<Transformer>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'transformers') })
      toast.success('Transformer added')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  useEffect(() => {
    if (!edgeCtxMenu) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setEdgeCtxMenu(null) }
    const onClick = () => setEdgeCtxMenu(null)
    const onResize = () => setEdgeCtxMenu(null)
    window.addEventListener('keydown', onKey)
    document.addEventListener('click', onClick)
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('click', onClick)
      window.removeEventListener('resize', onResize)
    }
  }, [edgeCtxMenu])

  const linkColorMap = useMemo(() => {
    const carriers = [...new Set(links.map(l => l.carrier).filter(Boolean))]
    return Object.fromEntries(carriers.map((c, i) => [c, getLinkColor(c, i)]))
  }, [links])

  const busVNom = useMemo(() =>
    Object.fromEntries(buses.map(b => [b.name, b.v_nom ?? 1])),
    [buses])

  // ── Build all React Flow nodes (buses + asset group nodes) ─────────────────
  const allRfNodes = useMemo<Node[]>(() => {
    // Build asset descriptors for all buses
    const assetDescs = buildAssetDescriptors(
      buses as Bus[], generators as Generator[], loads as Load[],
      sus as StorageUnit[], stores as Store[],
    )

    // 1. Apply the loaded layout document EXACTLY ONCE per layoutEpoch — i.e.
    // once per project's layout.json being fetched. Re-applying on every memo
    // recompute would clobber the user's in-session drags. Keyed off the
    // epoch (not a boolean) so a same-canvas project switch correctly
    // re-applies the NEW project's saved positions. The saved entries are
    // written into posCache by their node id, independent of the current
    // `buses` array, so they land even if the `['buses']` refetch hasn't
    // resolved yet.
    if (layoutSeededEpoch.current !== layoutEpoch) {
      layoutSeededEpoch.current = layoutEpoch
      if (savedStateRef.current) {
        savedStateRef.current.nodes.forEach(pn => {
          posCache.current[pn.id] = { x: pn.canvasX, y: pn.canvasY }
        })
      }
    }
    // 2. Seed any bus not yet in posCache via the schematic layout algorithm.
    // This runs WHENEVER uncached buses exist (project switch, bus add) — not
    // gated by a one-time latch — so nothing is ever stranded at the
    // {400,400} fallback. Idempotent for already-cached buses. NB: the blank
    // canvas never seeds from bus.x/y — those are geographic coordinates
    // reserved for the map view + clustering. `runLayout` produces the
    // "latent coordinates", persisted server-side as layout.json.
    const stillUncached = (buses as Bus[]).filter(b => !posCache.current[b.name])
    if (stillUncached.length > 0) {
      const layoutPos = runLayout(
        (buses as Bus[]).map(b => ({ id: b.name, v_nom: b.v_nom ?? 1 })),
        assetDescs,
      )
      Object.entries(layoutPos).forEach(([id, p]) => {
        if (!posCache.current[id]) posCache.current[id] = p
      })
    }

    const busNodes: Node[] = (buses as Bus[]).map(b => ({
      id: b.name, type: 'bus',
      position: posCache.current[b.name] ?? { x: 400, y: 400 },
      data: { bus: b },
    }))

    // Build asset group nodes with metadata
    const agNodes: Node[] = []
    ;(buses as Bus[]).forEach(bus => {
      const busPos = posCache.current[bus.name] ?? { x: 400, y: 400 }
      const busGens   = (generators as Generator[]).filter(g => g.bus === bus.name)
      const busLoads  = (loads as Load[]).filter(l => l.bus === bus.name)
      const busSus    = (sus as StorageUnit[]).filter(s => s.bus === bus.name)
      const busStores = (stores as Store[]).filter(s => s.bus === bus.name)
      const thermalGens   = busGens.filter(g => !isRenewableCarrier(g.carrier))
      const renewableGens = busGens.filter(g =>  isRenewableCarrier(g.carrier))
      const storageAssets = [...busSus, ...busStores]

      const addGroup = (
        cat: AssetCategory, assets: unknown[], carriers: string[], totalMW: number,
      ) => {
        if (assets.length === 0) return
        const id = `assetgrp-${bus.name}-${cat}`
        const off = LAYOUT_ASSET_OFFSETS[cat]
        agNodes.push({
          id, type: 'assetGroup',
          position: posCache.current[id] ?? { x: busPos.x + off.dx, y: busPos.y + off.dy },
          data: {
            category: cat, busName: bus.name, busCarrier: bus.carrier ?? '',
            assetCount: assets.length, totalMW,
            carriers: [...new Set(carriers)].slice(0, 4),
          } satisfies AssetGroupData,
        })
      }

      addGroup('Thermal',    thermalGens,   thermalGens.map(g => g.carrier),   thermalGens.reduce((s, g) => s + (g.p_nom ?? 0), 0))
      addGroup('Renewables', renewableGens, renewableGens.map(g => g.carrier), renewableGens.reduce((s, g) => s + (g.p_nom ?? 0), 0))
      addGroup('Storage',    storageAssets, storageAssets.map(a => a.carrier), busSus.reduce((s, su) => s + (su.p_nom ?? 0), 0))
      addGroup('Load',       busLoads,      busLoads.map(l => l.carrier),      busLoads.reduce((s, l) => s + Math.abs(l.p_set ?? 0), 0))
    })

    return [...busNodes, ...agNodes.filter(n => visibleGroups.has(n.id))]
  // `layoutEpoch` forces a re-seed of posCache when a new project's layout
  // is loaded (the component doesn't remount on project switch).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buses, generators, loads, sus, stores, visibleGroups, layoutEpoch])

  // ── Network edges (lines / links / transformers) ───────────────────────────
  const rfNetworkEdges = useMemo(() => [
    ...lines.map(l => ({
      id: `line-${l.name}`, source: l.bus0, target: l.bus1, type: 'network',
      data: { s_nom: l.s_nom, v_nom: busVNom[l.bus0] ?? 1, type: 'line' as const, color: getLineColor(busVNom[l.bus0] ?? 1), waypoints: [] as WP[], history: [[]] as WP[][] } satisfies EdgeData,
    })),
    ...links.map(l => ({
      id: `link-${l.name}`, source: l.bus0, target: l.bus1, type: 'network',
      data: { s_nom: l.p_nom, type: 'link' as const, carrier: l.carrier, color: linkColorMap[l.carrier] ?? FALLBACK_COLORS[0], waypoints: [] as WP[], history: [[]] as WP[][] } satisfies EdgeData,
    })),
    ...transformers.map(t => ({
      id: `tr-${t.name}`, source: t.bus0, target: t.bus1, type: 'network',
      data: { type: 'transformer' as const, color: '#16a34a', waypoints: [] as WP[], history: [[]] as WP[][] } satisfies EdgeData,
    })),
  ], [lines, links, transformers, busVNom, linkColorMap])

  // ── Dashed connector edges from bus → asset group nodes ────────────────────
  const rfAssetEdges = useMemo<Edge[]>(() => {
    const edges: Edge[] = []
    buses.forEach(bus => {
      const busGens   = (generators as Generator[]).filter(g => g.bus === bus.name)
      const busLoads  = (loads as Load[]).filter(l => l.bus === bus.name)
      const busSus    = (sus as StorageUnit[]).filter(s => s.bus === bus.name)
      const busStores = (stores as Store[]).filter(s => s.bus === bus.name)

      const categories: [AssetCategory, number][] = [
        ['Thermal',    busGens.filter(g => !isRenewableCarrier(g.carrier)).length],
        ['Renewables', busGens.filter(g => isRenewableCarrier(g.carrier)).length],
        ['Storage',    busSus.length + busStores.length],
        ['Load',       busLoads.length],
      ]
      const busIsH2 = isH2BusCarrier(bus.carrier ?? '')
      categories.forEach(([cat, count]) => {
        if (count === 0) return
        const cfg = CATEGORY_CONFIG[cat]
        const edgeColor = busIsH2 && (cat === 'Load' || cat === 'Storage')
          ? H2_NODE_COLORS[cat]
          : cfg.color
        edges.push({
          id: `assetedge-${bus.name}-${cat}`,
          source: bus.name,
          target: `assetgrp-${bus.name}-${cat}`,
          type: 'network',
          data: { type: 'asset' as const, color: edgeColor, waypoints: [] as WP[], history: [[]] as WP[][] } satisfies EdgeData,
        })
      })
    })
    return edges.filter(e => visibleGroups.has(e.target))
  }, [buses, generators, loads, sus, stores, visibleGroups])

  const handleEdgeUndo = useCallback(() => {
    if (!edgeCtxMenu) return
    setEdges(prev => prev.map(e => {
      if (e.id !== edgeCtxMenu.edgeId) return e
      const hist: WP[][] = (e.data as unknown as EdgeData).history ?? [[]]
      if (hist.length <= 1) return e
      const newHist = hist.slice(0, -1)
      return { ...e, data: { ...e.data as object, waypoints: JSON.parse(JSON.stringify(newHist[newHist.length - 1])), history: newHist } as EdgeData }
    }))
    scheduleSave()
    setEdgeCtxMenu(null)
  }, [edgeCtxMenu, setEdges, scheduleSave])

  const handleEdgeClear = useCallback(() => {
    if (!edgeCtxMenu) return
    setEdges(prev => prev.map(e => {
      if (e.id !== edgeCtxMenu.edgeId) return e
      return { ...e, data: { ...e.data as object, waypoints: [], history: [[]] } as unknown as EdgeData }
    }))
    scheduleSave()
    setEdgeCtxMenu(null)
  }, [edgeCtxMenu, setEdges, scheduleSave])

  // Intercept node changes to keep posCache in sync with drag positions
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    changes.forEach(c => {
      if (c.type === 'position' && c.position) {
        posCache.current[c.id] = c.position
      }
    })
    _onNodesChange(changes)
  }, [_onNodesChange])

  // Drag-drop position handoff. CreationForm sets pendingNodePosition with the
  // new node's name + the drop's React-Flow coords. As soon as the new node
  // appears in the buses list (React Query refetched after createMut), we
  // apply the position to posCache + the live nodes array, then clear the
  // pending state so the next drop starts clean.
  useEffect(() => {
    if (!pendingNodePosition) return
    const exists = (buses as Bus[]).some(b => b.name === pendingNodePosition.name)
    if (!exists) return
    posCache.current[pendingNodePosition.name] = pendingNodePosition.position
    setNodes(prev => prev.map(n =>
      n.id === pendingNodePosition.name
        ? { ...n, position: pendingNodePosition.position }
        : n,
    ))
    scheduleSave()
    setPendingNodePosition(null)
  }, [buses, pendingNodePosition, setNodes, setPendingNodePosition, scheduleSave])

  // Sync nodes: only trigger a full rebuild when the node ID set changes, preserving
  // fitView behaviour (React Flow fits on the first render that has nodes, not on []).
  // Data-only changes (asset counts / MW) update in-place without a sig change.
  // Includes a structural-equality guard so unchanged data does NOT trigger
  // setNodes on every refetch — that previously fed a setState→render→setState
  // loop on empty/idle networks ("Maximum update depth exceeded").
  const prevNodeSig = useRef('')
  const prevDataSig = useRef('')
  const prevLayoutEpoch = useRef(layoutEpoch)
  useEffect(() => {
    const sig = allRfNodes.map(n => n.id).sort().join('|')
    const idsChanged = sig !== prevNodeSig.current
    prevNodeSig.current = sig
    // A layout-epoch bump means a fresh project layout was loaded into
    // posCache — force the full-rebuild branch even when the node ID set is
    // unchanged (same project name reload), so the new positions actually
    // land on the rendered nodes instead of falling through to data-only.
    const epochChanged = layoutEpoch !== prevLayoutEpoch.current
    prevLayoutEpoch.current = layoutEpoch

    if (idsChanged || epochChanged) {
      // Full rebuild: node set changed — carry over user-dragged positions
      setNodes(prev => {
        const posFromPrev: Record<string, { x: number; y: number }> = {}
        prev.forEach(n => { posFromPrev[n.id] = n.position })
        return allRfNodes.map(n => ({
          ...n,
          position: posCache.current[n.id] ?? posFromPrev[n.id] ?? n.position,
        })) as Node[]
      })
      prevDataSig.current = ''  // force a data refresh on next data-only tick
      // Fit view once when real nodes first appear (100ms lets RF measure node dimensions)
      if (sig !== '' && !hasFitted.current) {
        hasFitted.current = true
        setTimeout(() => rfInstance.current?.fitView({ padding: 0.25, duration: 400, maxZoom: 0.85 }), 100)
      }
    } else {
      // Data-only update: skip when the relevant data hasn't changed. Without
      // this guard, every parent re-render that produces a new useMemo result
      // (even structurally identical) calls setNodes → re-renders → loop.
      const dataSig = allRfNodes.map(n => `${n.id}:${JSON.stringify(n.data ?? {})}`).join('|')
      if (dataSig === prevDataSig.current) return
      prevDataSig.current = dataSig
      setNodes(prev => prev.map(n => {
        const fresh = allRfNodes.find(nn => nn.id === n.id)
        return fresh ? { ...n, data: fresh.data } : n
      }))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allRfNodes])

  // Sync edges: preserve waypoints + history for all edge types (network and asset)
  const prevEdgeSig = useRef('')
  useEffect(() => {
    const allRfEdges = [...rfNetworkEdges, ...rfAssetEdges]
    // Include color in sig so edge styles update when v_nom changes (even if IDs are stable)
    const sig = allRfEdges.map(e => `${e.id}:${(e.data as EdgeData).color}`).sort().join('|')
    // A fresh layout load (project switch) advances `layoutEpoch` and repoints
    // `savedStateRef`; reconcile even when the edge signature is unchanged so
    // the new project's saved state (or absence of it) is applied authoritatively.
    const freshLayout = edgeSeededEpoch.current !== layoutEpoch
    if (sig === prevEdgeSig.current && !freshLayout) return
    prevEdgeSig.current = sig
    edgeSeededEpoch.current = layoutEpoch
    const saved = savedStateRef.current
    setEdges(prev => {
      const wpMap: Record<string, WP[]> = {}
      const histMap: Record<string, WP[][]> = {}
      // Seed from saved state (only affects edges with no current in-memory state)
      if (saved) {
        saved.edges.forEach(pe => {
          if (pe.waypoints?.length) wpMap[pe.id] = pe.waypoints
          if (pe.history?.length > 1) histMap[pe.id] = pe.history
        })
      }
      // Current in-memory state wins over saved state — EXCEPT on a fresh
      // layout load (project switch / reload), where the NEW project's saved
      // state is AUTHORITATIVE and the in-memory `prev` edges are stale
      // leftovers from the PREVIOUS project. On `freshLayout` we therefore do
      // NOT carry `prev` waypoints over at all — `wpMap`/`histMap` are seeded
      // purely from the new project's saved edges above, so an edge with the
      // same id in both projects (e.g. a transformer `tr-foo`) but NO saved
      // waypoints in the new project correctly renders straight, instead of
      // inheriting the old project's bend (which dragged the transformer
      // symbol — drawn at the edge-path midpoint — off into empty space).
      // The earlier guard `!savedIds.has(e.id)` only dropped IDs ABSENT from
      // saved; but `scheduleSave` persists every edge with `waypoints: []`, so
      // a waypointless transformer IS in saved → the absent-check missed it.
      // Skipping the whole carry-over on freshLayout is the complete fix.
      // On intra-project signature changes (`!freshLayout`) in-memory always
      // wins so an in-session drag is never clobbered.
      if (!freshLayout) {
        prev.forEach(e => {
          const d = (e.data as unknown as EdgeData)
          if (d?.waypoints?.length) wpMap[e.id] = d.waypoints
          if (d?.history?.length > 1) histMap[e.id] = d.history
        })
      }
      return allRfEdges.map(e => ({
        ...e, data: { ...(e.data as object), waypoints: wpMap[e.id] ?? [], history: histMap[e.id] ?? [[]] },
      })) as Edge<EdgeData>[]
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfNetworkEdges, rfAssetEdges, layoutEpoch])

  // ReactFlow's handle-drag onConnect: route to the same dialog Connect
  // Mode uses, so the line is persisted via createLineMut instead of just
  // appearing as a transient visual edge. The previous implementation
  // (`setEdges(eds => addEdge(conn, eds))`) added the edge to local state
  // only — it vanished on the next `['lines']` refetch and no Line ever
  // hit the backend. Reject self-loops and assetgrp pseudo-nodes; ignore
  // connections without both endpoints (incomplete drag).
  const onConnect = useCallback((conn: Connection) => {
    const source = conn.source
    const target = conn.target
    if (!source || !target) return
    if (source === target) {
      toast.error("Can't connect a bus to itself")
      return
    }
    // Asset-group pseudo-nodes can't anchor Lines — they represent
    // aggregated generators/loads/storage rendered next to a bus, not
    // electrical buses themselves. Refuse the connection cleanly.
    if (source.startsWith('assetgrp-') || target.startsWith('assetgrp-')) {
      toast.error('Lines can only connect buses')
      return
    }
    // Anchor the dialog at the viewport centre — ReactFlow's onConnect
    // doesn't carry the drop-cursor position. Connect Mode passes the
    // exact click position; for handle-drag the dialog floats centrally.
    const rect = document.body.getBoundingClientRect()
    setNewLineDialog({
      source,
      target,
      anchorX: rect.width / 2,
      anchorY: rect.height / 2,
    })
  }, [setNewLineDialog])

  // Auto-pan to highlighted component — only if not already visible
  useEffect(() => {
    if (!highlightedComponent) return
    const { type, name, busName } = highlightedComponent
    const rf = rfInstance.current
    if (!rf) return

    let targetPos: { x: number; y: number } | null = null

    if (type === 'Bus') {
      const node = nodesRef.current.find(n => n.id === name)
      if (node) targetPos = node.position
    } else if (busName) {
      const node = nodesRef.current.find(n => n.id === busName)
      if (node) targetPos = node.position
    } else if (type === 'Line' || type === 'Link') {
      const prefix = type === 'Line' ? 'line-' : 'link-'
      const edge = edgesRef.current.find(e => e.id === `${prefix}${name}`)
      if (edge) {
        const src = nodesRef.current.find(n => n.id === edge.source)
        const tgt = nodesRef.current.find(n => n.id === edge.target)
        if (src && tgt) targetPos = {
          x: (src.position.x + tgt.position.x) / 2,
          y: (src.position.y + tgt.position.y) / 2,
        }
      }
    }

    if (!targetPos) return

    // Check if the target is already within the visible canvas area (with margin).
    // If visible — just highlight; if not — pan without changing zoom.
    const screenPos = rf.flowToScreenPosition(targetPos)
    const canvasEl = document.querySelector('.react-flow') as HTMLElement | null
    const w = canvasEl?.clientWidth ?? window.innerWidth
    const h = canvasEl?.clientHeight ?? window.innerHeight
    const margin = 80
    const isVisible = (
      screenPos.x >= margin && screenPos.x <= w - margin &&
      screenPos.y >= margin && screenPos.y <= h - margin
    )
    if (!isVisible) {
      const { zoom } = rf.getViewport()
      rf.setCenter(targetPos.x, targetPos.y, { duration: 500, zoom })
    }
  }, [highlightedComponent])

  // Auto-clear highlight after 8 s
  useEffect(() => {
    if (!highlightedComponent) return
    const t = setTimeout(() => setHighlightedComponent(null), 8000)
    return () => clearTimeout(t)
  }, [highlightedComponent, setHighlightedComponent])

  const onNodeClick = useCallback((e: React.MouseEvent, node: { id: string }) => {
    if (canvasMode === 'connect' && !node.id.startsWith('assetgrp-')) {
      if (!connectSource) {
        setConnectSource(node.id)
      } else if (connectSource !== node.id) {
        setNewLineDialog({ source: connectSource, target: node.id, anchorX: e.clientX, anchorY: e.clientY })
        setConnectSource(null)
      }
      return
    }
    if (node.id.startsWith('assetgrp-')) {
      const d = nodes.find(n => n.id === node.id)?.data as unknown as AssetGroupData
      if (d) setSelectedComponent({ type: 'AssetGroup', name: `${d.busName}::${d.category}` })
    } else {
      setSelectedComponent({ type: 'Bus', name: node.id })
    }
  }, [canvasMode, connectSource, setSelectedComponent, nodes])

  const onEdgeClick = useCallback((_: React.MouseEvent, edge: { id: string }) => {
    if (edge.id.startsWith('assetedge-')) return
    const type = edge.id.startsWith('link-') ? 'Link' : edge.id.startsWith('tr-') ? 'Transformer' : 'Line'
    setSelectedComponent({ type, name: edge.id.replace(/^(line-|link-|tr-)/, '') })
  }, [setSelectedComponent])

  const onNodeDrag = useCallback((_: React.MouseEvent, draggedNode: { id: string; position: { x: number; y: number } }) => {
    const dragR = draggedNode.id.startsWith('assetgrp-') ? ASSET_R : BUS_R
    const { x: dx, y: dy } = draggedNode.position
    const hits = new Set<string>()
    nodesRef.current.forEach(n => {
      if (n.id === draggedNode.id) return
      const nR = n.id.startsWith('assetgrp-') ? ASSET_R : BUS_R
      const dist = Math.sqrt((n.position.x - dx) ** 2 + (n.position.y - dy) ** 2)
      if (dist < dragR + nR + 10) { hits.add(draggedNode.id); hits.add(n.id) }
    })
    setOverlappingNodes(hits)
  }, [])

  const onNodeDragStop = useCallback((_: React.MouseEvent, node: { id: string; position: { x: number; y: number } }) => {
    // Blank-canvas drags are SCHEMATIC ONLY: they update the latent-coordinate
    // layout cache (persisted via scheduleSave) but must NOT mutate
    // bus.x / bus.y — those are the geographic coordinates reserved for the
    // satellite/hybrid map view and the clustering algorithms. The schematic
    // and the geographic coordinate sets are deliberately independent.
    posCache.current[node.id] = node.position
    setOverlappingNodes(new Set())
    scheduleSave()
  }, [scheduleSave])

  const runAutoLayout = useCallback(() => {
    const assetDescs = buildAssetDescriptors(
      buses as Bus[], generators as Generator[], loads as Load[],
      sus as StorageUnit[], stores as Store[],
    )
    const layoutPos = runLayout(
      (buses as Bus[]).map(b => ({ id: b.name, v_nom: b.v_nom ?? 1 })),
      assetDescs,
    )
    Object.entries(layoutPos).forEach(([id, p]) => { posCache.current[id] = p })
    setNodes(prev => prev.map(n => ({
      ...n,
      position: posCache.current[n.id] ?? n.position,
    })))
    setEdges(prev => prev.map(e => ({
      ...e,
      data: { ...e.data as object, waypoints: [], history: [[]] } as unknown as EdgeData,
    })))
    toast.success('Layout reset — waypoints cleared')
    scheduleSave()
    setTimeout(() => rfInstance.current?.fitView({ padding: 0.25, duration: 400, maxZoom: 0.85 }), 50)
  }, [buses, generators, loads, sus, stores, setNodes, setEdges, scheduleSave])

  const handleResetDiagram = useCallback(() => {
    try { localStorage.removeItem(storageKeyFor(currentProject)) } catch { /* noop */ }
    savedStateRef.current = null
    // Mark the current epoch as already-seeded: this handler imperatively
    // re-seeds posCache via runLayout below, and savedStateRef is now null,
    // so allRfNodes must NOT re-apply anything for this epoch.
    layoutSeededEpoch.current = layoutEpoch
    posCache.current = {}
    const assetDescs = buildAssetDescriptors(
      buses as Bus[], generators as Generator[], loads as Load[],
      sus as StorageUnit[], stores as Store[],
    )
    // Always the schematic layout algorithm — the blank canvas never seeds
    // from geographic bus.x/y. See the "latent coordinates" note at the top.
    const layoutPos = runLayout(
      (buses as Bus[]).map(b => ({ id: b.name, v_nom: b.v_nom ?? 1 })),
      assetDescs,
    )
    Object.entries(layoutPos).forEach(([id, p]) => { posCache.current[id] = p })
    setNodes(prev => prev.map(n => ({ ...n, position: posCache.current[n.id] ?? n.position })))
    setEdges(prev => prev.map(e => ({ ...e, data: { ...e.data as object, waypoints: [], history: [[]] } as unknown as EdgeData })))
    setShowResetConfirm(false)
    // Persist the fresh layout so the reset sticks — server-side for an
    // active project (otherwise the next project load would restore the OLD
    // layout.json), localStorage for the unsaved network.
    scheduleSave()
    toast.success('Diagram reset — positions and waypoints cleared')
    setTimeout(() => rfInstance.current?.fitView({ padding: 0.25, duration: 400, maxZoom: 0.85 }), 50)
  }, [buses, generators, loads, sus, stores, setNodes, setEdges, scheduleSave, layoutEpoch, currentProject])

  const onNodeDoubleClick = useCallback((e: React.MouseEvent, node: Node) => {
    if (node.id.startsWith('assetgrp-')) return
    setBusEditor({ busName: node.id, anchorX: e.clientX, anchorY: e.clientY })
  }, [])

  const getConnectedCount = useCallback((busName: string) => {
    const ls = (lines as import('../api/types').Line[]).filter(l => l.bus0 === busName || l.bus1 === busName).length
    const lk = (links as import('../api/types').Link[]).filter(l => l.bus0 === busName || l.bus1 === busName).length
    const gs = (generators as Generator[]).filter(g => g.bus === busName).length
    const ld = (loads as Load[]).filter(l => l.bus === busName).length
    const st = (sus as StorageUnit[]).filter(s => s.bus === busName).length
                + (stores as Store[]).filter(s => s.bus === busName).length
    return { total: ls + lk + gs + ld + st, lines: ls, links: lk, generators: gs, loads: ld, storage: st }
  }, [lines, links, generators, loads, sus, stores])

  const handleBusApply = useCallback((fields: Partial<Bus>) => {
    if (!busEditor) return
    const oldName = busEditor.busName
    const newName = fields.name?.trim() ?? oldName
    const isRename = newName !== oldName

    if (isRename) {
      // Move posCache to new key
      posCache.current[newName] = posCache.current[oldName] ?? { x: 400, y: 400 }
      delete posCache.current[oldName]
      // Move asset group cache keys
      Object.keys(posCache.current).forEach(k => {
        if (k.startsWith(`assetgrp-${oldName}-`)) {
          posCache.current[k.replace(`assetgrp-${oldName}-`, `assetgrp-${newName}-`)] = posCache.current[k]
          delete posCache.current[k]
        }
      })

      networkApi.renameBus(oldName, newName).then(() => {
        // Invalidate all component queries so data refreshes
        ;['buses', 'lines', 'links', 'generators', 'loads', 'storage_units', 'stores', 'transformers'].forEach(k =>
          queryClient.invalidateQueries({ queryKey: [k] })
        )
        // Update React Flow nodes: rename id + bus data
        setNodes(prev => prev.map(n => {
          if (n.id === oldName) {
            const bus = (n.data as { bus?: Bus }).bus
            return { ...n, id: newName, data: { bus: bus ? { ...bus, name: newName } : bus } }
          }
          if (n.id.startsWith(`assetgrp-${oldName}-`)) {
            return { ...n, id: n.id.replace(`assetgrp-${oldName}-`, `assetgrp-${newName}-`),
              data: { ...(n.data as object), busName: newName } }
          }
          return n
        }))
        // Update React Flow edges: source/target bus name
        setEdges(prev => prev.map(e => ({
          ...e,
          source: e.source === oldName ? newName : e.source,
          target: e.target === oldName ? newName : e.target,
          id: e.id.replace(`assetedge-${oldName}-`, `assetedge-${newName}-`),
        })))
        // Update visible asset groups
        setVisibleGroups(prev => {
          const next = new Set<string>()
          prev.forEach(id => next.add(id.startsWith(`assetgrp-${oldName}-`)
            ? id.replace(`assetgrp-${oldName}-`, `assetgrp-${newName}-`) : id))
          return next
        })
        // Persist immediately (no debounce) with the updated node id —
        // server-side for an active project, localStorage otherwise. Mirror it
        // into the module cache too, so a view-switch unmount before the next
        // scheduleSave restores the renamed layout without a stale-id flicker.
        const renamedProj = useUIStore.getState().currentProject
        const renamedState: PersistedState = {
          version: STORAGE_VERSION, savedAt: Date.now(),
          nodes: nodesRef.current
            .filter(n => !n.id.startsWith('assetgrp-'))
            .map(n => ({ id: n.id === oldName ? newName : n.id, canvasX: n.position.x, canvasY: n.position.y })),
          edges: edgesRef.current
            .filter(e => !e.id.startsWith('assetedge-'))
            .map(e => { const d = e.data as unknown as EdgeData; return { id: e.id, waypoints: d?.waypoints ?? [], history: d?.history ?? [[]] } }),
        }
        layoutMemCache.set(layoutCacheKey(renamedProj), renamedState)
        persistLayoutFor(renamedProj, renamedState)
        toast.success(`Bus renamed to "${newName}"`)
      }).catch(() => {
        // Revert posCache on failure
        posCache.current[oldName] = posCache.current[newName]
        delete posCache.current[newName]
      })
    } else {
      // Non-rename: update bus model parameters only — canvas position is never touched by Apply
      saveBusMut.mutate({ name: oldName, fields })
      scheduleSave()
    }
    setBusEditor(null)
  }, [busEditor, saveBusMut, setNodes, setEdges, queryClient, scheduleSave, generators, loads, sus, stores])

  const onNodeContextMenu = useCallback((e: React.MouseEvent, node: { id: string }) => {
    e.preventDefault()
    if (node.id.startsWith('assetgrp-')) return
    const busName = node.id
    const busGens   = (generators as Generator[]).filter(g => g.bus === busName)
    const busLoads  = (loads as Load[]).filter(l => l.bus === busName)
    const busSus    = (sus as StorageUnit[]).filter(s => s.bus === busName)
    const busStores = (stores as Store[]).filter(s => s.bus === busName)
    const categories: { cat: AssetCategory; count: number }[] = [
      { cat: 'Thermal',    count: busGens.filter(g => !isRenewableCarrier(g.carrier)).length },
      { cat: 'Renewables', count: busGens.filter(g => isRenewableCarrier(g.carrier)).length },
      { cat: 'Storage',    count: busSus.length + busStores.length },
      { cat: 'Load',       count: busLoads.length },
    ]
    setContextMenu({ x: e.clientX, y: e.clientY, busName, categories })
  }, [generators, loads, sus, stores])

  const toggleAssetGroup = useCallback((busName: string, cat: AssetCategory) => {
    const id = `assetgrp-${busName}-${cat}`
    setVisibleGroups(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setContextMenu(null)
  }, [])

  const onPaneContextMenu = useCallback((e: React.MouseEvent | MouseEvent) => {
    if (canvasMode === 'connect') {
      e.preventDefault()
      setConnectSource(null)
      setCanvasMode('select')
      toast('Connect Buses cancelled', { icon: '✕' })
    }
  }, [canvasMode, setCanvasMode])

  const deleteEdgeWithUndo = useCallback((edgeId: string) => {
    const edge = edgesRef.current.find(e => e.id === edgeId)
    if (!edge) return
    const isLink = edgeId.startsWith('link-')
    const name = edgeId.replace(/^(line-|link-)/, '')
    const savedEdge = { ...edge, data: { ...(edge.data as object) } as unknown as EdgeData }
    setEdges(prev => prev.filter(e => e.id !== edgeId))
    setEdgeCtxMenu(null)

    // Keep timer + toast duration in lockstep — the toast IS the undo window.
    const UNDO_MS = 5000
    const timer = setTimeout(async () => {
      pendingDeletesRef.current.delete(edgeId)
      try {
        if (isLink) {
          await networkApi.deleteLink(name)
          queryClient.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'links') })
        } else {
          await networkApi.deleteLine(name)
          queryClient.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
        }
      } catch (err) {
        toast.error(`Delete failed: ${(err as Error).message}`)
        setEdges(prev => [...prev, savedEdge as Edge<EdgeData>])
      }
    }, UNDO_MS)
    const toastId = toast.custom(
      (t) => (
        <div className={`flex items-center gap-3 bg-bg border border-border rounded-lg px-3 py-2 shadow-lg transition-opacity ${t.visible ? 'opacity-100' : 'opacity-0'}`}>
          <span className="text-sm text-text flex-1">{isLink ? 'Link' : 'Line'} &ldquo;{name}&rdquo; deleted</span>
          <button
            onClick={() => {
              const pending = pendingDeletesRef.current.get(edgeId)
              if (pending) { clearTimeout(pending.timer); pendingDeletesRef.current.delete(edgeId) }
              setEdges(prev => [...prev, savedEdge as Edge<EdgeData>])
              toast.dismiss(t.id)
            }}
            className="text-[#2f81f7] text-xs font-medium hover:underline shrink-0"
          >Undo</button>
        </div>
      ),
      { duration: UNDO_MS }
    )
    pendingDeletesRef.current.set(edgeId, { timer, toastId })
  }, [setEdges, queryClient])

  // Flush pending edge-deletes on unmount (tab switch / navigation). The
  // 5s setTimeout keeps running after unmount, so the delete still hits
  // the backend — but the optimistic restore in the error branch targets
  // an unmounted component, and the global toast lingers with an Undo
  // button that no longer clears the timer (the ref-Map entry was the
  // bridge). Net effect: race where Undo on a stale toast appeared to
  // restore the edge but the delete had already gone through. Flush
  // synchronously: cancel the timer, fire the DELETE immediately, and
  // dismiss the stale toast. The user navigated away — they've
  // implicitly committed to the delete.
  useEffect(() => () => {
    const pending = pendingDeletesRef.current
    if (pending.size === 0) return
    pending.forEach(({ timer, toastId }, edgeId) => {
      clearTimeout(timer)
      if (toastId) toast.dismiss(toastId)
      const isLink = edgeId.startsWith('link-')
      const name = edgeId.replace(/^(line-|link-)/, '')
      const p = isLink ? networkApi.deleteLink(name) : networkApi.deleteLine(name)
      p.then(() => queryClient.invalidateQueries({ queryKey: [isLink ? 'links' : 'lines'] }))
       .catch(() => { /* component is gone; nothing to restore */ })
    })
    pending.clear()
  }, [queryClient])

  const linkCarrierLegend = useMemo(() =>
    [...new Set(links.map(l => l.carrier).filter(Boolean))].map((c, i) => ({ carrier: c, color: getLinkColor(c, i) })),
    [links])

  return (
    <CanvasResultsProvider>
    <HighlightContext.Provider value={highlightedComponent}>
    <ConnectModeContext.Provider value={connectModeCtxValue}>
    <OverlapContext.Provider value={overlappingNodes}>
    <EdgeMenuContext.Provider value={edgeMenuCtxValue}>
    <style>{`
      @keyframes highlightPulse { 0%,100%{opacity:1} 50%{opacity:0.55} }
      .highlight-ring { animation: highlightPulse 1s ease-in-out 4; }
      @keyframes highlightRingOuter { 0%{opacity:0.85;transform:scale(1)} 60%{opacity:0.3;transform:scale(1.18)} 100%{opacity:0;transform:scale(1.35)} }
      .highlight-ring-outer { animation: highlightRingOuter 1.1s ease-out 3; }
      @keyframes edgePulse { 0%,100%{opacity:0.85} 50%{opacity:1} }
      .highlight-edge-pulse { animation: edgePulse 1s ease-in-out 4; }
    `}</style>
    <div
      className="h-full w-full relative"
      onMouseMove={canvasMode === 'connect' && connectSource ? (e) => setConnectMousePos({ x: e.clientX, y: e.clientY }) : undefined}
      // Drop detection for palette drags lives in AssetPalette itself — it
      // uses pointer events + document.elementFromPoint to locate the React
      // Flow canvas and the global rfInstance to convert screen → flow coords.
      // No HTML5 onDragOver/onDrop here; those proved unreliable across
      // browser/OS combinations the user runs in.
    >
      {/* Non-converged banner. Shown only on the AC PF view when the
          currently-selected snapshot did NOT converge. The canvas still
          renders the LOPF data (via the source toggle's auto-fallback in
          CanvasResultsContext when ac_pf snapshot is missing for that hour),
          so this banner explains why the displayed numbers aren't the AC
          values the user asked for. */}
      {resultsOverlayEnabled && acPfAvailable && resultSource === 'ac_pf' && !currentSnapConverged && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 z-[850] bg-warn/95 text-white text-[11px] rounded shadow-lg px-3 py-1.5 flex items-center gap-2 max-w-[640px]">
          <span className="text-base leading-none">⚠</span>
          <span>
            AC Power Flow did not converge for this snapshot ({currentSnapIso.slice(0, 16).replace('T', ' ')}).
            Showing values from the LP fallback — switch to a converged snapshot or back to LOPF view.
          </span>
        </div>
      )}
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick} onEdgeClick={onEdgeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        onPaneClick={() => { setContextMenu(null); setBusEditor(null); setHighlightedComponent(null) }}
        nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        // Canvas base colour + the design's signature ambient red/purple
        // radial wash. Set on the .react-flow root (which doesn't transform)
        // so the gradient stays anchored to the viewport while the dot grid
        // and nodes pan/zoom above it.
        style={{ backgroundColor: 'var(--color-canvas)', backgroundImage: 'var(--ambient)' }}
        onInit={(instance) => {
          rfInstance.current = instance
          ;(window as unknown as { rfInstance: unknown }).rfInstance = instance
        }}
      >
        <Background variant={BackgroundVariant.Dots} color="var(--color-canvas-dot)" gap={24} />
        <Controls />
        {/* Minimap — frosted card, voltage/category-coloured node dots. React
            Flow's built-in MiniMap does not render edges; nodes are given a
            matching stroke so the network shape still reads at a glance. */}
        <MiniMap
          pannable
          zoomable
          nodeBorderRadius={3}
          nodeStrokeWidth={3}
          maskColor="color-mix(in srgb, var(--color-canvas) 72%, transparent)"
          nodeColor={(n) => {
            if (n.type === 'assetGroup') return CATEGORY_CONFIG[(n.data as unknown as AssetGroupData).category]?.color ?? '#6b7280'
            return getLineColor((n.data as unknown as { bus?: Bus })?.bus?.v_nom ?? 1)
          }}
          nodeStrokeColor={(n) => {
            if (n.type === 'assetGroup') return CATEGORY_CONFIG[(n.data as unknown as AssetGroupData).category]?.color ?? '#6b7280'
            return getLineColor((n.data as unknown as { bus?: Bus })?.bus?.v_nom ?? 1)
          }}
        />

        {canvasMode === 'connect' && (
          <Panel position="bottom-center">
            <div className="bg-bg border border-border rounded-lg px-4 py-2 shadow-sm text-xs text-text">
              {connectSource
                ? <><span className="font-semibold text-accent">{connectSource}</span> selected — click target bus&nbsp;&nbsp;<span className="text-muted">(right-click to cancel)</span></>
                : 'Click a bus to start a connection'}
            </div>
          </Panel>
        )}

        {/* Results pill — bottom-centre canvas status indicator. Hidden in
            connect mode (the connect hint owns that slot) and on an empty
            canvas (the empty-state message already covers that case). */}
        {canvasMode !== 'connect' && (buses as Bus[]).length > 0 && (
          <Panel position="bottom-center">
            <ResultsPill />
          </Panel>
        )}

        <Panel position="top-left">
          <div className="flex items-start gap-2.5">
            {/* Vertical floating toolbar — frosted-glass card, design-system
                style. Build tools (add bus / connect / fit / layout / reset)
                in one group; view tools (results overlay + source toggles)
                in a second group below. */}
            <div className="flex flex-col gap-2">
              <div
                className="flex flex-col gap-0.5 border border-border rounded-[10px] p-1 shadow-md"
                style={{ background: 'color-mix(in srgb, var(--color-bg) 92%, transparent)', backdropFilter: 'blur(8px)' }}
              >
                <button
                  title="Add bus (auto-name, rename via Properties)"
                  className="flex items-center justify-center w-8 h-8 rounded-md text-ink-600 hover:bg-panel hover:text-accent transition-colors"
                  onClick={() => {
                    // Auto-name avoids the native `prompt()` which blocks the
                    // main thread and can't be styled. User renames via the
                    // right-panel Properties editor — the bus comes pre-selected
                    // so the rename field is one click away.
                    const existing = new Set((buses as Bus[]).map(b => b.name))
                    let i = (buses as Bus[]).length + 1
                    let name = `bus_${i}`
                    while (existing.has(name)) { i += 1; name = `bus_${i}` }
                    networkApi.createBus({ name, v_nom: 1, carrier: 'AC', x: 0, y: 0 } as Bus)
                      .then(() => {
                        queryClient.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'buses') })
                        setSelectedComponent({ type: 'Bus', name })
                      })
                      .catch((e: { response?: { data?: { detail?: string } } }) => {
                        toast.error(e.response?.data?.detail ?? `Failed to create ${name}`)
                      })
                  }}
                >
                  <Plus size={15} />
                </button>
                <button
                  onClick={() => setCanvasMode(canvasMode === 'connect' ? 'select' : 'connect')}
                  title="Connect buses — route a line between two buses"
                  className={`flex items-center justify-center w-8 h-8 rounded-md transition-colors
                    ${canvasMode === 'connect'
                      ? 'bg-accent-50 text-accent'
                      : 'text-ink-600 hover:bg-panel hover:text-accent'}`}
                >
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="3.5" cy="3.5" r="1.6"/><circle cx="12.5" cy="12.5" r="1.6"/><path d="M3.5 5v3a2 2 0 0 0 2 2h5a2 2 0 0 1 2 2v-0.5"/></svg>
                </button>
                <button
                  onClick={() => rfInstance.current?.fitView({ padding: 0.25, duration: 400, maxZoom: 0.85 })}
                  title="Fit view — centre the network in the canvas"
                  className="flex items-center justify-center w-8 h-8 rounded-md text-ink-600 hover:bg-panel hover:text-accent transition-colors"
                >
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M3 5V3h2M11 3h2v2M13 11v2h-2M5 13H3v-2"/><circle cx="8" cy="8" r="2"/></svg>
                </button>
                <button
                  onClick={runAutoLayout}
                  title="Auto-layout by voltage tier"
                  className="flex items-center justify-center w-8 h-8 rounded-md text-ink-600 hover:bg-panel hover:text-accent transition-colors"
                >
                  <svg width="15" height="15" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M1 3h10M1 6h7M1 9h4"/><path d="M9 7l2 2-2 2"/></svg>
                </button>
                {/* "Reset to model coordinates" button removed: the blank
                    canvas is decoupled from geographic bus.x/y by design. */}
                <button
                  onClick={() => setShowResetConfirm(true)}
                  title="Reset diagram — clear saved positions and waypoints"
                  className="flex items-center justify-center w-8 h-8 rounded-md text-ink-600 hover:bg-panel hover:text-danger transition-colors"
                >
                  <svg width="15" height="15" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M1 6a5 5 0 1 0 1-3M1 1v3h3"/></svg>
                </button>
              </div>

              <div
                className="flex flex-col gap-0.5 border border-border rounded-[10px] p-1 shadow-md"
                style={{ background: 'color-mix(in srgb, var(--color-bg) 92%, transparent)', backdropFilter: 'blur(8px)' }}
              >
                <button
                  onClick={() => setResultsOverlay(!resultsOverlayEnabled)}
                  title="Toggle results overlay"
                  className={`flex items-center justify-center w-8 h-8 rounded-md transition-colors
                    ${resultsOverlayEnabled ? 'bg-accent-50 text-accent' : 'text-ink-600 hover:bg-panel hover:text-accent'}`}
                >
                  <Layers size={15} />
                </button>
                {/* Result-source toggle (LOPF / AC PF). Only meaningful when
                    Stage 2 results are available AND the results overlay is on
                    — otherwise the canvas isn't reading either set. Mirrors
                    the LoadFlow tab's segmented control; same Zustand store
                    drives both so the choice stays consistent across views. */}
                {resultsOverlayEnabled && acPfAvailable && (
                  <div className="flex flex-col border border-border rounded-md overflow-hidden text-[9.5px]"
                    title={`AC PF: ${acPfStatus?.converged_count ?? 0}/${acPfStatus?.total_snapshots ?? 0} snapshots converged`}>
                    <button
                      type="button"
                      onClick={() => setResultSource('lopf')}
                      className={`px-1.5 py-1 transition-colors ${
                        resultSource === 'lopf' ? 'bg-accent text-white' : 'text-muted hover:text-text'
                      }`}
                    >LOPF</button>
                    <button
                      type="button"
                      onClick={() => setResultSource('ac_pf')}
                      className={`px-1.5 py-1 border-t border-border transition-colors ${
                        resultSource === 'ac_pf' ? 'bg-accent text-white' : 'text-muted hover:text-text'
                      }`}
                    >AC PF</button>
                  </div>
                )}
                {/* P / Q flow toggle. Only shown when overlay is on AND user is
                    on the AC PF source — Q values come from PyPSA's n.pf() and
                    are zero in the LP stage by construction, so a Q toggle on
                    LOPF would be misleading. */}
                {resultsOverlayEnabled && acPfAvailable && resultSource === 'ac_pf' && (
                  <div className="flex flex-col border border-border rounded-md overflow-hidden text-[9.5px]"
                    title="Flow overlay: active power (P, MW) or reactive power (Q, MVAr)">
                    <button
                      type="button"
                      onClick={() => setFlowOverlayKind('p')}
                      className={`px-1.5 py-1 transition-colors ${
                        flowOverlayKind === 'p' ? 'bg-accent text-white' : 'text-muted hover:text-text'
                      }`}
                    >P</button>
                    <button
                      type="button"
                      onClick={() => setFlowOverlayKind('q')}
                      className={`px-1.5 py-1 border-t border-border transition-colors ${
                        flowOverlayKind === 'q' ? 'bg-accent text-white' : 'text-muted hover:text-text'
                      }`}
                    >Q</button>
                  </div>
                )}
              </div>
            </div>

            {/* Voltage / asset legend — frosted card, design-system sizing. */}
            <div
              className="border border-border rounded-[10px] shadow-md w-[164px] p-3"
              style={{ background: 'color-mix(in srgb, var(--color-bg) 92%, transparent)', backdropFilter: 'blur(8px)' }}
            >
              <p className="text-[9px] font-bold text-muted uppercase tracking-[0.14em] mb-2">Buses &amp; Lines · voltage</p>
              {[['> 300 kV', '#ef4444'], ['200–300 kV', '#16a34a'], ['100–200 kV', '#2563eb'], ['< 100 kV', '#6b7280']].map(([label, color]) => (
                <div key={label} className="flex items-center gap-2 mb-1">
                  <svg width="20" height="3" style={{ flexShrink: 0 }}><line x1="0" y1="1.5" x2="20" y2="1.5" stroke={color} strokeWidth="2.5" strokeLinecap="round" /></svg>
                  <span className="text-[10.5px] text-ink-700">{label}</span>
                </div>
              ))}
              {linkCarrierLegend.length > 0 && (
                <>
                  <p className="text-[9px] font-bold text-muted uppercase tracking-[0.14em] mb-2 mt-3">Links · carrier</p>
                  {linkCarrierLegend.map(({ carrier, color }) => {
                    const { Icon, label } = getCarrierBadge(carrier)
                    return (
                      <div key={carrier} className="flex items-center gap-1.5 mb-1.5">
                        <div style={{
                          display: 'flex', alignItems: 'center', gap: 2,
                          backgroundColor: color, color: '#fff',
                          padding: '1px 5px', borderRadius: 6,
                          fontSize: 8, fontWeight: 700, fontFamily: 'var(--font-family-mono)',
                          border: '1px solid #fff',
                          flexShrink: 0,
                        }}>
                          <Icon size={8} strokeWidth={2.5} />
                          <span style={{ marginLeft: 1 }}>{label}</span>
                        </div>
                        <span className="text-[9.5px] text-muted truncate max-w-[72px]">{carrier}</span>
                      </div>
                    )
                  })}
                </>
              )}
              <p className="text-[9px] font-bold text-muted uppercase tracking-[0.14em] mb-2 mt-3">Asset groups</p>
              {(Object.entries(CATEGORY_CONFIG) as [AssetCategory, typeof CATEGORY_CONFIG[AssetCategory]][]).map(([cat, cfg]) => (
                <div key={cat} className="flex items-center gap-2 mb-1">
                  <cfg.Icon size={12} style={{ color: cfg.color, flexShrink: 0 }} />
                  <span className="text-[10.5px] text-ink-700">{cat}</span>
                </div>
              ))}
            </div>
          </div>
        </Panel>
      </ReactFlow>

      {buses.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <p className="text-muted text-sm bg-bg/90 px-4 py-2 rounded border border-border shadow-sm">
            No buses — import a network or add buses using the toolbar
          </p>
        </div>
      )}

      {contextMenu && (
        <div
          className="fixed z-50 bg-bg border border-border rounded-lg shadow-lg py-1 min-w-[200px]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={e => e.stopPropagation()}
        >
          <div className="px-3 py-1.5 text-[10px] font-bold text-muted uppercase tracking-wider border-b border-border mb-1">
            {contextMenu.busName}
          </div>
          <button
            onClick={() => { setSelectedComponent({ type: 'Bus', name: contextMenu.busName }); setContextMenu(null) }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left hover:bg-border/30 transition-colors text-text"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="6" cy="6" r="4.5"/><line x1="6" y1="4" x2="6" y2="6.5"/><circle cx="6" cy="8" r="0.5" fill="currentColor" stroke="none"/></svg>
            Properties
          </button>
          {(() => {
            const busName = contextMenu.busName
            const withAssets = contextMenu.categories.filter(c => c.count > 0)
            const allIds = withAssets.map(c => `assetgrp-${busName}-${c.cat}`)
            const allVisible = allIds.every(id => visibleGroups.has(id))
            const anyVisible = allIds.some(id => visibleGroups.has(id))
            return withAssets.length > 0 ? (
              <div className="px-3 py-1 flex gap-1.5 border-b border-border mb-1">
                <button
                  onClick={() => {
                    setVisibleGroups(prev => { const next = new Set(prev); allIds.forEach(id => next.add(id)); return next })
                    setContextMenu(null)
                  }}
                  disabled={allVisible}
                  className="flex-1 py-1 text-[10px] rounded border border-border text-text hover:bg-border/30 transition-colors disabled:opacity-35"
                >Show all</button>
                <button
                  onClick={() => {
                    setVisibleGroups(prev => { const next = new Set(prev); allIds.forEach(id => next.delete(id)); return next })
                    setContextMenu(null)
                  }}
                  disabled={!anyVisible}
                  className="flex-1 py-1 text-[10px] rounded border border-border text-text hover:bg-border/30 transition-colors disabled:opacity-35"
                >Hide all</button>
              </div>
            ) : null
          })()}
          {contextMenu.categories.map(({ cat, count }) => {
            const id = `assetgrp-${contextMenu.busName}-${cat}`
            const isVisible = visibleGroups.has(id)
            const cfg = CATEGORY_CONFIG[cat]
            const LABELS: Record<AssetCategory, string> = {
              Thermal: 'Thermal Generation', Renewables: 'Renewables',
              Storage: 'Storage', Load: 'Load',
            }
            return (
              <button
                key={cat}
                disabled={count === 0}
                onClick={() => toggleAssetGroup(contextMenu.busName, cat)}
                className={`flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left transition-colors
                  ${count > 0 ? 'hover:bg-border/30 cursor-pointer' : 'opacity-35 cursor-not-allowed'}
                  ${isVisible ? 'text-accent font-medium' : 'text-text'}`}
              >
                <cfg.Icon size={12} style={{ color: count > 0 ? cfg.color : undefined }} />
                <span>{isVisible ? 'Hide' : 'Show'} {LABELS[cat]}</span>
                <span className="ml-auto text-muted font-mono">{count}</span>
              </button>
            )
          })}
          <div style={{ borderTop: '1px solid #e5e7eb', margin: '2px 8px' }} />
          <button
            onClick={() => {
              const busName = contextMenu.busName
              setContextMenu(null)
              networkApi.deleteBusCascade(busName).then(() => {
                const keys = ['buses', 'lines', 'links', 'generators', 'loads', 'storage_units', 'stores', 'transformers', 'carriers', 'snapshots', 'undoInfo']
                const proj = useUIStore.getState().currentProject
                keys.forEach(k => queryClient.invalidateQueries({ queryKey: nk(proj, k) }))
                toast.custom(
                  (t) => (
                    <div style={{ opacity: t.visible ? 1 : 0, transition: 'opacity 0.15s' }}
                      className="flex items-center gap-3 bg-panel border border-border rounded-lg shadow-lg px-4 py-2.5 text-sm">
                      <Trash2 size={12} className="text-danger shrink-0" />
                      <span className="text-text">Deleted bus <span className="font-semibold">{busName}</span> + connected</span>
                      <button
                        onClick={() => {
                          toast.dismiss(t.id)
                          networkApi.undo().then(() => {
                            keys.forEach(k => queryClient.invalidateQueries({ queryKey: nk(proj, k) }))
                            toast.success('Undone')
                          }).catch((e: Error) => toast.error(e.message))
                        }}
                        className="ml-1 text-accent font-semibold text-xs hover:underline whitespace-nowrap"
                      >
                        Undo
                      </button>
                      <button onClick={() => toast.dismiss(t.id)} aria-label="Dismiss notification" className="p-0.5 text-muted hover:text-text transition-colors">
                        <XIcon size={11} />
                      </button>
                    </div>
                  ),
                  { duration: 6000 },
                )
              }).catch((e: Error) => toast.error(e.message))
            }}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left hover:bg-danger/10 transition-colors text-danger"
          >
            <Trash2 size={11} />
            Delete Bus…
          </button>
        </div>
      )}

      {edgeCtxMenu && (() => {
        const menuEdge = edges.find(e => e.id === edgeCtxMenu.edgeId)
        const ed = menuEdge ? (menuEdge.data as unknown as EdgeData) : null
        const histLen = ed?.history?.length ?? 1
        const undoSteps = histLen - 1
        const hasUndo = undoSteps > 0
        const hasWaypoints = (ed?.waypoints?.length ?? 0) > 0
        const isAssetEdge = edgeCtxMenu.edgeId.startsWith('assetedge-')
        const isLink = edgeCtxMenu.edgeId.startsWith('link-')
        const edgeName = edgeCtxMenu.edgeId.replace(/^(line-|link-)/, '')
        const MENU_W = 248, MENU_H = isAssetEdge ? 88 : 130
        const left = edgeCtxMenu.x + MENU_W > window.innerWidth  ? edgeCtxMenu.x - MENU_W : edgeCtxMenu.x
        const top  = edgeCtxMenu.y + MENU_H > window.innerHeight ? edgeCtxMenu.y - MENU_H : edgeCtxMenu.y
        const btnBase = 'flex items-center gap-2.5 w-full px-3.5 text-[13px] font-normal text-left transition-colors h-9'
        return (
          <div
            className="fixed z-50 bg-bg rounded-lg py-1"
            style={{ width: MENU_W, left, top, border: '0.5px solid #d1d5db', boxShadow: '0 4px 16px rgba(0,0,0,0.12)' }}
            onClick={e => e.stopPropagation()}
          >
            <button
              className={`${btnBase} ${hasUndo ? 'hover:bg-panel cursor-pointer text-text' : 'opacity-40 cursor-not-allowed text-text'}`}
              disabled={!hasUndo}
              title={!hasUndo ? 'No more steps to undo' : undefined}
              onClick={handleEdgeUndo}
            >
              <UndoIcon />
              <span className="flex-1">Undo last change</span>
              {hasUndo && (
                <span className="text-[11px] text-muted font-mono ml-1 shrink-0">
                  {undoSteps} step{undoSteps !== 1 ? 's' : ''}
                </span>
              )}
            </button>
            <div style={{ borderTop: '1px solid #e5e7eb', margin: '2px 10px' }} />
            <button
              className={`${btnBase} ${hasWaypoints ? 'hover:bg-panel cursor-pointer text-text' : 'opacity-40 cursor-not-allowed text-text'}`}
              disabled={!hasWaypoints}
              title={!hasWaypoints ? 'Line is already direct' : undefined}
              onClick={handleEdgeClear}
            >
              <ClearRouteIcon /> Clear waypoints — direct connection
            </button>
            {!isAssetEdge && (
              <>
                <div style={{ borderTop: '1px solid #e5e7eb', margin: '2px 10px' }} />
                <button
                  className={`${btnBase} hover:bg-danger/10 cursor-pointer text-danger`}
                  onClick={() => deleteEdgeWithUndo(edgeCtxMenu.edgeId)}
                >
                  <Trash2 size={13} />
                  Delete {isLink ? 'Link' : 'Line'} &ldquo;{edgeName}&rdquo;
                </button>
              </>
            )}
          </div>
        )
      })()}

      {busEditor && (() => {
        const bus = (buses as Bus[]).find(b => b.name === busEditor.busName)
        if (!bus) return null
        return (
          <BusEditor
            busName={busEditor.busName}
            anchorX={busEditor.anchorX}
            anchorY={busEditor.anchorY}
            bus={bus}
            allBuses={buses as Bus[]}
            getConnectedCount={getConnectedCount}
            onApply={handleBusApply}
            onClose={() => setBusEditor(null)}
          />
        )
      })()}

      {showResetConfirm && (
        <Dialog
          open={showResetConfirm}
          onClose={() => setShowResetConfirm(false)}
          aria-labelledby={resetConfirmTitleId}
          dismissOnBackdrop={false}
          panelClassName="bg-bg rounded-xl shadow-2xl p-6 w-[340px] border border-border"
        >
          <p id={resetConfirmTitleId} className="text-sm font-semibold text-text mb-2">Reset diagram?</p>
          <p className="text-xs text-muted mb-5 leading-relaxed">
            This will clear all saved node positions and edge waypoints. The layout will be recomputed from scratch. This cannot be undone.
          </p>
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setShowResetConfirm(false)}
              className="px-4 py-1.5 text-xs rounded border border-border text-text hover:bg-panel transition-colors"
            >Cancel</button>
            <button
              onClick={handleResetDiagram}
              className="px-4 py-1.5 text-xs rounded bg-[#dc2626] text-white hover:bg-[#b91c1c] transition-colors"
            >Reset</button>
          </div>
        </Dialog>
      )}
      {newLineDialog && (
        <NewConnectionDialog
          source={newLineDialog.source}
          target={newLineDialog.target}
          sourceVNom={busVNom[newLineDialog.source] ?? 0}
          targetVNom={busVNom[newLineDialog.target] ?? 0}
          anchorX={newLineDialog.anchorX}
          anchorY={newLineDialog.anchorY}
          onConfirm={(params) => {
            const bus0 = newLineDialog.source
            const bus1 = newLineDialog.target
            if (params.kind === 'line') {
              createLineMut.mutate({ ...params, bus0, bus1 })
            } else {
              createTransformerMut.mutate({ ...params, bus0, bus1 })
            }
            setNewLineDialog(null)
          }}
          onClose={() => setNewLineDialog(null)}
        />
      )}

      {/* Rubber-band line for Connect Buses mode */}
      {canvasMode === 'connect' && connectSource && connectMousePos && (() => {
        const srcNode = nodes.find(n => n.id === connectSource)
        if (!srcNode || !rfInstance.current) return null
        const sp = rfInstance.current.flowToScreenPosition({
          x: srcNode.position.x + BUS_NODE_MIN_W / 2, y: srcNode.position.y + BUS_NODE_H / 2,
        })
        return (
          <svg style={{ position: 'fixed', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 9000 }}>
            <line
              x1={sp.x} y1={sp.y} x2={connectMousePos.x} y2={connectMousePos.y}
              stroke="#2f81f7" strokeWidth={2} strokeDasharray="8 4" strokeLinecap="round"
            />
          </svg>
        )
      })()}
    </div>
    </EdgeMenuContext.Provider>
    </OverlapContext.Provider>
    </ConnectModeContext.Provider>
    </HighlightContext.Provider>
    </CanvasResultsProvider>
  )
}
