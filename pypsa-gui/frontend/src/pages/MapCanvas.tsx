import { Fragment, useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { MapContainer, TileLayer, Marker, Polyline, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { isRenewableCarrier } from '../utils/carriers'
import { uniformBadge, type BadgeDef } from '../utils/carrierBadges'
import { Ruler, Flame, Wind, BatteryCharging, Zap } from 'lucide-react'
import ReactDOMServer from 'react-dom/server'
import { useUIStore, type CanvasView } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { networkApi } from '../api/network'
import { appLog } from '../store/simulationStore'
import type { Bus, Generator, Line as LineT, Link as LinkT, Load, StorageUnit, Store, Transformer } from '../api/types'
import { CanvasResultsProvider, useCanvasResults, fmtMW, loadingColor } from '../components/CanvasResultsContext'
import { busLatLng, unplacedBusNames } from '../utils/geo'
import { nextBusToPlace, canSkip } from '../utils/placement'
import { partitionRescale, type RescalePreview } from '../utils/rescale'
import UnplacedBusesPanel from '../components/UnplacedBusesPanel'
import RescaleDialog from '../components/RescaleDialog'

// Draggable bus marker. Mimics the previous CircleMarker visually (12 px,
// 2 px coloured border, white fill) but uses a Marker + divIcon so leaflet
// gives us the `draggable` capability and a `dragend` event. The cursor
// changes to "grab" so users discover that the dot is draggable.
function busDivIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: 'pypsa-bus-marker',
    html: `<div style="width:12px;height:12px;border:2px solid ${color};background:#fff;border-radius:50%;box-sizing:border-box;cursor:grab;"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })
}

// IEC two-interlocking-circle transformer pictogram, sized for the map. Same
// glyph used on the schematic canvas — the visual identity stays consistent
// across the two views.
function transformerDivIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: 'pypsa-transformer-marker',
    html: `<svg width="22" height="14" viewBox="0 0 22 14" fill="none" stroke="${color}" stroke-width="1.6" style="cursor:pointer;">
      <circle cx="8" cy="7" r="6" fill="#ffffff"/>
      <circle cx="14" cy="7" r="6" fill="#ffffff"/>
    </svg>`,
    iconSize: [22, 14],
    iconAnchor: [11, 7],
  })
}

// ── Asset-group categorisation (mirror of TopologyCanvas) ─────────────────────
type AssetCategory = 'Thermal' | 'Renewables' | 'Storage' | 'Load'

// isRenewableCarrier imported from utils/carriers (single null-safe source).

interface CategoryStyle {
  Icon: typeof Flame; color: string
  // DEFAULT screen-pixel offset of the bubble from its bus marker, used until
  // the user drags the bubble (which persists a per-bus override). The offset
  // is baked into the divIcon's `iconAnchor` against a Marker anchored at the
  // bus's OWN lat/lng — so it's a constant on-screen vector that Leaflet keeps
  // glued to the bus through any zoom/pan, with no re-projection and no snap.
  // Earlier we positioned the bubble at an unprojected lat/lng recomputed on
  // every `zoomend`; that drifted during the zoom animation and snapped back
  // on completion. Anchoring to the bus + a pure-CSS offset is the fix.
  dx: number; dy: number
}
const CATEGORY_STYLE: Record<AssetCategory, CategoryStyle> = {
  Thermal:    { Icon: Flame,           color: '#dc2626', dx: -110, dy: -70 },
  Renewables: { Icon: Wind,            color: '#16a34a', dx:  110, dy: -70 },
  Storage:    { Icon: BatteryCharging, color: '#7c3aed', dx:  110, dy:  60 },
  Load:       { Icon: Zap,             color: '#d97706', dx:  -20, dy:  90 },
}
const CATEGORY_LABELS: Record<AssetCategory, string> = {
  Thermal: 'Thermal Generation', Renewables: 'Renewables',
  Storage: 'Storage', Load: 'Load',
}

// Per bus × category: how many assets, and which single carrier badge (if
// any) they all share. `badge` is null for a mixed or empty group, in which
// case the bubble falls back to the category's generic icon.
interface CategoryEntry { count: number; badge: BadgeDef | null }

// localStorage keys for the map's user layout — asset-group bubble offsets and
// line waypoints. Keyed PER PROJECT (the `default` slot is the unsaved /
// no-project network) so each project keeps its own map layout instead of one
// global blob shared across every project. localStorage already makes the
// layout survive reloads + canvas view switches; persisting it into the
// project bundle server-side (like the blank canvas's layout.json) is a
// deferred follow-up.
type AssetOffsets = Record<string, { dx: number; dy: number }>
type LatLngTuple = [number, number]
type LineWaypoints = Record<string, LatLngTuple[]>

// Resolve the active project for keying; reads the store imperatively so the
// load/save helpers below can stay plain module functions.
function mapLayoutSlot(): string {
  return useUIStore.getState().currentProject ?? 'default'
}

// Asset-group bubble positions — pixel offsets from the bus so a bus drag still
// sweeps its bubbles along, preserving the user's *relative* layout choice.
function loadAssetOffsets(): AssetOffsets {
  try { return JSON.parse(localStorage.getItem(`pypsa-gui:map:asset-offsets:${mapLayoutSlot()}`) ?? '{}') ?? {} }
  catch { return {} }
}
function saveAssetOffsets(offsets: AssetOffsets) {
  try { localStorage.setItem(`pypsa-gui:map:asset-offsets:${mapLayoutSlot()}`, JSON.stringify(offsets)) } catch { /* quota: ignore */ }
}

// User-routed line waypoints, keyed by "<edgeKind>:<name>" e.g. "line:L1",
// "link:L2", "tr:T1". Stored as raw lat/lng so they survive bus drags and
// zoom/pan without recomputation. PURELY VISUAL — never touch line.length /
// link.length, those stay haversine bus0→bus1 (or whatever the user typed).
function loadLineWaypoints(): LineWaypoints {
  try { return JSON.parse(localStorage.getItem(`pypsa-gui:map:line-waypoints:${mapLayoutSlot()}`) ?? '{}') ?? {} }
  catch { return {} }
}
function saveLineWaypoints(wps: LineWaypoints) {
  try { localStorage.setItem(`pypsa-gui:map:line-waypoints:${mapLayoutSlot()}`, JSON.stringify(wps)) } catch { /* quota: ignore */ }
}

// Small handle markers used by EditableLine. Waypoint dots use the *inverse*
// of the bus marker palette (solid fill + white ring) so a routing waypoint
// is never confused with a bus at a glance — buses are white-fill rings,
// waypoints are filled dots. Mirrors the blank canvas's `fill={color}
// stroke="white"` waypoint style at TopologyCanvas EditableEdge.
function waypointDivIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: 'pypsa-line-waypoint',
    html: `<div style="width:10px;height:10px;background:${color};border:2px solid #fff;border-radius:50%;box-sizing:border-box;cursor:grab;box-shadow:0 0 0 1px ${color}66;"></div>`,
    iconSize: [10, 10], iconAnchor: [5, 5],
  })
}
function addHandleDivIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: 'pypsa-line-add-handle',
    html: `<div style="width:12px;height:12px;border:1.5px dashed ${color};background:#fff;border-radius:50%;box-sizing:border-box;opacity:0.85;cursor:grab;"></div>`,
    iconSize: [12, 12], iconAnchor: [6, 6],
  })
}

// Builds a divIcon for an asset-group bubble. Uses lucide's React icon as
// inline SVG via renderToStaticMarkup so the badge looks identical to the
// blank-canvas equivalent.
//
// The (dx, dy) screen-pixel offset is baked into `iconAnchor`, so the bubble
// is drawn that many pixels from the Marker's lat/lng. Because the Marker is
// anchored at the BUS's own lat/lng (see AssetGroupLayer), Leaflet moves the
// bubble in perfect lockstep with the bus through every zoom/pan animation —
// the offset is pure CSS and never re-projected, so it can't drift or snap.
// `tooltipAnchor` is set to (dx, dy) so the hover tooltip opens from the
// bubble's centre rather than from the (invisible) bus anchor point.
//
// When `dispatchMw` is provided (results overlay on), the bubble also shows the
// summed dispatch at the current snapshot — ▲ for injection, ▼ for draw.
function assetGroupDivIcon(
  cat: AssetCategory, count: number, dx: number, dy: number, dispatchMw?: number | null,
  badge?: BadgeDef | null,
): L.DivIcon {
  const { Icon: CategoryIcon, color } = CATEGORY_STYLE[cat]
  // A group whose carriers all share one badge gets that badge's pictogram —
  // a solar-only group is a sun, not the generic renewables turbine. A mixed
  // group keeps the category icon, because no single icon is honest for it.
  const Icon = badge?.Icon ?? CategoryIcon
  // `color` via style, not the `color` prop: BadgeIcon (unlike CategoryIcon)
  // may be H2Icon, a custom SVG that doesn't accept a `color` prop. Both
  // lucide icons and H2Icon paint via `currentColor`, so setting the CSS
  // `color` on the root <svg> — even through renderToStaticMarkup's static
  // markup — resolves correctly once Leaflet inlines it into the live DOM.
  const iconSvg = ReactDOMServer.renderToStaticMarkup(
    <Icon size={14} style={{ color }} strokeWidth={2} />
  )
  const showDispatch = dispatchMw != null && Number.isFinite(dispatchMw)
  const dispatchHtml = showDispatch
    ? `<span style="opacity:0.5">·</span><span>${(dispatchMw as number) >= 0 ? '▲' : '▼'} ${fmtMW(Math.abs(dispatchMw as number))}</span>`
    : ''
  const W = showDispatch ? 116 : 60, H = 22
  return L.divIcon({
    className: 'pypsa-asset-group-marker',
    html: `<div style="display:flex;align-items:center;gap:4px;padding:3px 7px;background:#fff;border:1.5px solid ${color};border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.15);cursor:pointer;font-size:10px;font-weight:600;color:${color};white-space:nowrap;">
      ${iconSvg}
      <span>${count}</span>
      ${dispatchHtml}
    </div>`,
    iconSize: [W, H],
    iconAnchor: [W / 2 - dx, H / 2 - dy],
    tooltipAnchor: [dx, dy],
  })
}

// ── Tile-provider config ───────────────────────────────────────────────────────
// Two map base layers, matching the standard satellite/hybrid mental model:
//   • Satellite — Esri World Imagery only (pure imagery, no labels).
//   • Hybrid    — Esri World Imagery + two transparent Esri reference overlays
//                 (roads + place/facility labels), i.e. imagery WITH street and
//                 POI names on top, like Google's "Hybrid" mode.
// (The third mode, "Blank", is the schematic TopologyCanvas — it never reaches
// MapCanvas.) All tiles are free, no API key. Switch to Mapbox/MapTiler with a
// token if traffic outgrows the free Esri tiers.
const ESRI_IMAGERY_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const ESRI_ATTRIBUTION =
  'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community'
// Transparent label/road overlays designed to layer on top of World Imagery.
// Transportation = streets/highways; Boundaries & Places = city / facility /
// POI labels and administrative boundaries.
const ESRI_TRANSPORTATION_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}'
const ESRI_PLACES_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'

// Voltage → colour, mirrors the existing TopologyCanvas legend so the two
// canvases feel like the same network.
function lineColor(vNom: number): string {
  if (vNom > 300) return '#dc2626'
  if (vNom > 200) return '#16a34a'
  if (vNom > 100) return '#2563eb'
  return '#374151'
}

interface MapCanvasProps {
  // 'satellite' = Esri imagery only; 'hybrid' = Esri imagery + street/place labels.
  mode: Exclude<CanvasView, 'blank'>
}

// One-shot helper that fits the map view to the network bounds the first
// time data lands. Subsequent renders don't re-fit so the user's pan/zoom
// is preserved.
//
// `suspended` is true while click-to-place is running. Without it, placing the
// first bus makes `points.length === 1` and this calls setView(..., 11) —
// snapping the map to that bus while the user is lining up the next click.
function FitToNetwork({ buses, suspended }: { buses: Bus[]; suspended: boolean }) {
  const map = useMap()
  const fittedRef = useRef(false)
  useEffect(() => {
    if (fittedRef.current || suspended) return
    const points = buses.map(busLatLng).filter((p): p is [number, number] => p !== null)
    if (points.length === 0) return
    if (points.length === 1) {
      map.setView(points[0], 11)
    } else {
      map.fitBounds(L.latLngBounds(points), { padding: [40, 40] })
    }
    fittedRef.current = true
  }, [buses, map, suspended])
  return null
}

// Click-to-place. Mounted inside <MapContainer> only while placement is
// running, so the map has no click handler at all the rest of the time.
//
// Leaflet does not fire `click` at the end of a drag — the same guarantee the
// bus markers already rely on (see the comment above the bus Marker layer) —
// so panning to find a location cannot drop a bus by accident.
function ClickToPlace({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  const map = useMapEvents({
    click: (e) => onPick(e.latlng.lat, e.latlng.lng),
  })
  useEffect(() => {
    const el = map.getContainer()
    const previous = el.style.cursor
    el.style.cursor = 'crosshair'
    return () => { el.style.cursor = previous }
  }, [map])
  return null
}

// Asset-group bubbles (one per expanded bus×category). Each bubble is a Marker
// anchored at its BUS's lat/lng; the (dx, dy) screen-pixel offset is baked into
// the divIcon's `iconAnchor`. Because the anchor lat/lng is the bus's own,
// Leaflet translates the bubble in lockstep with the bus through every zoom and
// pan — the offset is pure CSS, never re-projected — so the bubble holds a
// rock-constant on-screen position relative to its bus and never drifts or
// snaps. No map-event listener / forced re-render is needed.
//
// Drag → on dragend we recover the drag delta in pixels, fold it into the
// stored offset, and persist it. bus.x / bus.y are NEVER touched.
interface AssetGroupLayerProps {
  busByName: Map<string, Bus>
  visibleGroups: Set<string>
  categoryCountsByBus: Map<string, Record<AssetCategory, CategoryEntry>>
  onSelect: (busName: string, cat: AssetCategory) => void
  offsets: AssetOffsets
  setOffsets: (o: AssetOffsets) => void
}
function AssetGroupLayer({
  busByName, visibleGroups, categoryCountsByBus, onSelect, offsets, setOffsets,
}: AssetGroupLayerProps) {
  const map = useMap()
  // Per-snapshot results overlay — populated only while the overlay is on.
  // byAssetGroup is keyed `${busName}|${category}`; the bubble shows its
  // summed dispatch (MW) at the selected snapshot when available.
  const results = useCanvasResults()

  return (
    <>
      {Array.from(visibleGroups).map(id => {
        const [busName, cat] = id.split('::') as [string, AssetCategory]
        const bus = busByName.get(busName)
        if (!bus) return null
        const c = busLatLng(bus)
        if (!c) return null
        const entry = categoryCountsByBus.get(busName)?.[cat]
        const count = entry?.count ?? 0
        if (count === 0) return null

        const offset = offsets[id] ?? CATEGORY_STYLE[cat]
        const dispatchMw = results.enabled
          ? results.byAssetGroup.get(`${busName}|${cat}`)
          : undefined

        return (
          <Marker
            key={id}
            position={c}
            draggable
            icon={assetGroupDivIcon(cat, count, offset.dx, offset.dy, dispatchMw, entry?.badge)}
            eventHandlers={{
              click: () => onSelect(busName, cat),
              dragend: (e) => {
                // Leaflet moved the marker's anchor lat/lng by the drag delta.
                // Recover that delta in screen pixels and fold it into the
                // stored offset; the next render re-pins the marker to the bus
                // lat/lng with the new offset baked into iconAnchor.
                const dropPx = map.latLngToContainerPoint((e.target as L.Marker).getLatLng())
                const busPx = map.latLngToContainerPoint(c)
                const newOffset = {
                  dx: offset.dx + (dropPx.x - busPx.x),
                  dy: offset.dy + (dropPx.y - busPx.y),
                }
                const next = { ...offsets, [id]: newOffset }
                setOffsets(next)
                saveAssetOffsets(next)
              },
            }}
          >
            <Tooltip direction="top" offset={[0, -10]}>
              {busName} · {CATEGORY_LABELS[cat]} ({count})
              {results.enabled && dispatchMw != null && (
                <span style={{ display: 'block', opacity: 0.85 }}>
                  {dispatchMw >= 0 ? '▲' : '▼'} {fmtMW(Math.abs(dispatchMw))}
                  {cat === 'Storage' && (() => {
                    const soc = results.byAssetGroupSoC.get(`${busName}|Storage`)
                    return soc != null && Number.isFinite(soc)
                      ? ` · SoC ${soc.toFixed(0)}%`
                      : ''
                  })()}
                </span>
              )}
            </Tooltip>
          </Marker>
        )
      })}
    </>
  )
}

// Polyline + per-vertex drag handles. Mirrors the blank-canvas EditableEdge
// UX: hover the line to reveal mid-segment "+" handles (drag to splice a new
// waypoint in) and waypoint handles (drag to move, double-click to remove).
// Right-click clears all waypoints for that line.
//
// During drag the polyline is updated imperatively via setLatLngs() for 60
// fps smoothness; on dragend we commit the new waypoints to React state +
// localStorage. The line's underlying length / r / x are NEVER touched.
interface EditableLineProps {
  id: string                            // "line:NAME" / "link:NAME" / "tr:NAME"
  source: LatLngTuple
  target: LatLngTuple
  waypoints: LatLngTuple[]
  onUpdate: (next: LatLngTuple[]) => void
  onSelect: () => void
  color: string
  weight: number
  dashArray?: string
  tooltip?: string
  // Optional permanent label rendered at the polyline's centre — used by the
  // results overlay to surface flow magnitude + loading % on the edge itself
  // so the user doesn't have to hover every line to read the numbers.
  permanentLabel?: string
}

function EditableLine({
  id, source, target, waypoints, onUpdate, onSelect, color, weight, dashArray, tooltip, permanentLabel,
}: EditableLineProps) {
  const [hovered, setHovered] = useState(false)
  const [ctxAt, setCtxAt] = useState<{ x: number; y: number } | null>(null)
  const polylineRef = useRef<L.Polyline | null>(null)
  // Live snapshot of waypoints used during a drag — committed on dragend so
  // polyline updates stay imperative + smooth instead of round-tripping
  // through React on every pointer event.
  const liveWps = useRef<LatLngTuple[]>([...waypoints])
  useEffect(() => { liveWps.current = [...waypoints] }, [waypoints])

  // Grace-period hover guard. Without it, mouseout fires on the polyline as
  // soon as the cursor crosses onto a handle marker, which removes the
  // handle, putting the cursor back on the polyline → mouseover → handle
  // reappears → flicker loop. The 80 ms timeout gives the marker's mouseover
  // a window to cancel the pending hide. Both polyline and marker share the
  // same enter/leave handlers below.
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

  const allPoints: LatLngTuple[] = [source, ...waypoints, target]
  const showHandles = hovered

  const paintLive = (wps: LatLngTuple[]) => {
    polylineRef.current?.setLatLngs([source, ...wps, target] as L.LatLngExpression[])
  }

  // Close ctxMenu on Escape / outside click.
  useEffect(() => {
    if (!ctxAt) return
    const close = () => setCtxAt(null)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setCtxAt(null) }
    document.addEventListener('click', close)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('click', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [ctxAt])

  return (
    <>
      <Polyline
        ref={polylineRef as unknown as React.Ref<L.Polyline>}
        positions={allPoints as L.LatLngExpression[]}
        pathOptions={{ color, weight, dashArray, opacity: 0.85 }}
        eventHandlers={{
          click: onSelect,
          mouseover: onAreaEnter,
          mouseout:  onAreaLeave,
          contextmenu: (e) => {
            const oe = e.originalEvent as MouseEvent
            oe.preventDefault()
            setCtxAt({ x: oe.clientX, y: oe.clientY })
          },
        }}
      >
        {tooltip && <Tooltip sticky>{tooltip}</Tooltip>}
        {/* Permanent tooltip on a Leaflet Polyline auto-anchors to the line's
            centre. Used by the results overlay to show flow magnitude + load %
            on the edge directly — no hover required. Kept SEPARATE from the
            sticky tooltip so the hover detail (full text) is still available
            on top of the always-visible short label. */}
        {permanentLabel && (
          <Tooltip permanent direction="center" className="map-edge-label">
            {permanentLabel}
          </Tooltip>
        )}
      </Polyline>

      {/* Existing waypoint handles. Shown whenever the line is hovered, plus
          permanently for any line that already has at least one waypoint —
          so the user can see at a glance which lines carry custom routing. */}
      {(showHandles || waypoints.length > 0) && waypoints.map((wp, i) => (
        <Marker
          key={`wp-${i}`}
          position={wp}
          draggable
          icon={waypointDivIcon(color)}
          eventHandlers={{
            mouseover: onAreaEnter,
            mouseout:  onAreaLeave,
            drag: (e) => {
              const ll = (e.target as L.Marker).getLatLng()
              liveWps.current = liveWps.current.map((w, idx) =>
                idx === i ? [ll.lat, ll.lng] as LatLngTuple : w)
              paintLive(liveWps.current)
            },
            dragend: () => onUpdate([...liveWps.current]),
            dblclick: (e) => {
              // L.DomEvent.stop prevents the dblclick from also zooming the
              // map (the default leaflet handler).
              L.DomEvent.stop(e.originalEvent as MouseEvent)
              onUpdate(waypoints.filter((_, idx) => idx !== i))
            },
          }}
        />
      ))}

      {/* Mid-segment "add" handles — only when hovering. Drag splices a new
          waypoint at the midpoint and binds it to the cursor for the rest
          of the gesture. */}
      {showHandles && allPoints.slice(0, -1).map((p0, segIdx) => {
        const p1 = allPoints[segIdx + 1]
        const mid: LatLngTuple = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2]
        return (
          <Marker
            key={`mid-${segIdx}`}
            position={mid}
            draggable
            icon={addHandleDivIcon(color)}
            eventHandlers={{
              mouseover: onAreaEnter,
              mouseout:  onAreaLeave,
              dragstart: () => {
                // Splice the new waypoint at segIdx so subsequent drag events
                // update THIS index. liveWps starts from current waypoints.
                liveWps.current = [
                  ...waypoints.slice(0, segIdx),
                  mid,
                  ...waypoints.slice(segIdx),
                ]
                paintLive(liveWps.current)
              },
              drag: (e) => {
                const ll = (e.target as L.Marker).getLatLng()
                liveWps.current = liveWps.current.map((w, idx) =>
                  idx === segIdx ? [ll.lat, ll.lng] as LatLngTuple : w)
                paintLive(liveWps.current)
              },
              dragend: () => onUpdate([...liveWps.current]),
            }}
          />
        )
      })}

      {/* Right-click context menu — small, single action: clear waypoints. */}
      {ctxAt && (
        <PolylineCtxMenu
          x={ctxAt.x} y={ctxAt.y}
          onResetWaypoints={waypoints.length > 0
            ? () => { onUpdate([]); setCtxAt(null) }
            : undefined}
          onClose={() => setCtxAt(null)}
          edgeId={id}
        />
      )}
    </>
  )
}

// Lightweight context menu rendered as a fixed-position div outside the map.
// Same pattern as the blank canvas's edge menu.
function PolylineCtxMenu({
  x, y, onResetWaypoints, onClose, edgeId,
}: {
  x: number; y: number
  onResetWaypoints?: () => void
  onClose: () => void
  edgeId: string
}) {
  return (
    <div
      className="fixed z-[700] bg-bg border border-border rounded-lg shadow-lg py-1 min-w-[160px]"
      style={{ left: x, top: y }}
      onClick={e => e.stopPropagation()}
    >
      <div className="px-3 py-1.5 text-[10px] font-bold text-muted uppercase tracking-wider border-b border-border mb-1">
        {edgeId}
      </div>
      <button
        onClick={() => {
          if (onResetWaypoints) onResetWaypoints()
          onClose()
        }}
        disabled={!onResetWaypoints}
        className="block w-full px-3 py-1.5 text-xs text-left hover:bg-border/30 transition-colors text-text disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Reset waypoints
      </button>
    </div>
  )
}

// Inner component — rendered inside <CanvasResultsProvider> so it (and every
// marker / line / asset bubble below) can read the per-snapshot results
// overlay via useCanvasResults().
function MapCanvasInner({ mode }: MapCanvasProps) {
  const { setSelectedComponent, currentProject, activeSlidePanel, paletteMode } = useUIStore()
  const qc = useQueryClient()
  // True unless a higher-priority overlay (a slide panel or the command
  // palette) is open. Gates every piece of placement UI — the empty-state /
  // chip panel, the placement strip, AND the ClickToPlace map-click handler
  // — so a click landing on map exposed behind an open modal can't silently
  // place a bus with no visible indicator, and so the strip can't float on
  // top of the command palette.
  const placementUiAllowed = !activeSlidePanel && paletteMode === null
  // Per-snapshot results overlay (LOPF / AC PF dispatch + line loading).
  // `enabled` is false unless the user turns the overlay on in SnapshotPicker.
  const results = useCanvasResults()
  const { data: buses = [] }        = useQuery({ queryKey: nk(currentProject, 'buses'),        queryFn: networkApi.getBuses })
  const { data: lines = [] }        = useQuery({ queryKey: nk(currentProject, 'lines'),        queryFn: networkApi.getLines })
  const { data: links = [] }        = useQuery({ queryKey: nk(currentProject, 'links'),        queryFn: networkApi.getLinks })
  const { data: transformers = [] } = useQuery({ queryKey: nk(currentProject, 'transformers'), queryFn: networkApi.getTransformers })
  const { data: generators = [] }   = useQuery({ queryKey: nk(currentProject, 'generators'),   queryFn: networkApi.getGenerators })
  const { data: loads = [] }        = useQuery({ queryKey: nk(currentProject, 'loads'),        queryFn: networkApi.getLoads })
  const { data: sus = [] }          = useQuery({ queryKey: nk(currentProject, 'storage_units'),queryFn: networkApi.getStorageUnits })
  const { data: stores = [] }       = useQuery({ queryKey: nk(currentProject, 'stores'),       queryFn: networkApi.getStores })

  // O(1) bus lookup for line endpoints — the alternative would be a linear
  // scan per line render, which gets noticeable on networks with hundreds of
  // edges.
  const busByName = useMemo(() => {
    const m = new Map<string, Bus>()
    for (const b of buses as Bus[]) m.set(b.name, b)
    return m
  }, [buses])

  // Buses still at PyPSA's (0, 0) default. Derived on every render (D2) — the
  // previous code toasted this once per mount and then forgot it, which is
  // most of why a network of unplaced buses read as a broken basemap.
  const unplaced = useMemo(() => unplacedBusNames(buses as Bus[]), [buses])

  // Set by UnplacedBusesPanel / the placement strip; consumed by ClickToPlace
  // and by FitToNetwork's `suspended` prop.
  const [placing, setPlacing] = useState(false)

  // Bus names the user has deferred via "Skip", in no particular order.
  // `placingBus` (below `unplaced` is already declared, so no use-before-
  // declare) is the first still-unplaced bus that hasn't been skipped,
  // falling back to the very first unplaced bus once every remaining one has
  // been skipped over — so skipping only ever REORDERS the queue, it never
  // drops a bus. Recomputed from `unplaced` on every render rather than a
  // stale local queue: placing a bus removes it from `unplaced` when the
  // buses query invalidates, which advances the picker on its own.
  //
  // This diverges from the task brief's `unplaced[skipped] ?? unplaced[0]`
  // numeric-index scheme. That index is a position into an array that
  // shrinks by one on every successful placement, so "skip" followed by a
  // "place" silently re-points the same index at whichever bus shifted into
  // that slot — not the bus the user actually meant to defer. It still
  // terminates and never resolves to nothing while buses remain (the
  // `?? unplaced[0]` fallback catches every out-of-bounds case), so no bus
  // is ever permanently unreachable — but the Skip button ends up disabled
  // for the rest of the session as soon as the numeric pointer runs off the
  // shrinking tail, even with several buses still left to place, which reads
  // as broken. Tracking skipped bus NAMES instead of an index makes "has
  // this specific bus been skipped" well-defined regardless of how the
  // array reshuffles, and keeps Skip enabled as long as more than one
  // unplaced bus remains.
  //
  // The derivation itself lives in utils/placement.ts (nextBusToPlace /
  // canSkip) — a separate pure module with its own test coverage, imported
  // here rather than re-inlined.
  const [skippedNames, setSkippedNames] = useState<Set<string>>(new Set())
  const placingBus = placing ? nextBusToPlace(unplaced, skippedNames) : undefined

  // Escape exits placement mode without waiting for the last bus. Clears the
  // skip set too, so a later "Place buses on the map" starts from a clean
  // queue rather than resuming an order the user may not remember setting.
  //
  // Ignore Escape events that originate from an editable element (a focused
  // input/textarea/select, or anything contenteditable). Without this check,
  // a bare `window` keydown listener catches EVERY Escape press — including
  // one meant to close the command palette's search box or blur a form
  // field — and silently ends the placement session underneath it.
  useEffect(() => {
    if (!placing) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const target = e.target as HTMLElement | null
      const tag = target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) return
      setPlacing(false); setSkippedNames(new Set())
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [placing])

  // Previewed impedance rescales awaiting a decision. Accumulated rather than
  // handled per-event: during click-to-place, prompting on every click would
  // make the mode unusable, so the batch is drained once at the end. RescaleDialog
  // itself is only rendered while `!placing` (below), so this queue is silent
  // furniture during placement and surfaces the moment placement ends — via
  // the completion effect just below, the Escape handler above, or the "Done"
  // button — whichever sets `placing` back to false first.
  const [pendingRescale, setPendingRescale] = useState<RescalePreview[]>([])

  // The only write path (B1): explicit, and only for previews the caller
  // already decided to apply (the auto-applied immaterial ones, or the user's
  // "Update" choice on the dialog).
  const applyRescale = useCallback(async (previews: RescalePreview[]) => {
    if (previews.length === 0) return
    await networkApi.rescaleImpedances(previews.map(p => ({
      name: p.name, r: p.new.r, x: p.new.x, b: p.new.b,
    })))
    qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
  }, [qc])

  // Immaterial changes are applied straight away; material ones queue for the
  // dialog. Blocked ones are surfaced by the dialog, never silently dropped.
  const ingestRescale = useCallback((previews: RescalePreview[] | undefined) => {
    if (!previews || previews.length === 0) return
    const { auto, ask, blocked } = partitionRescale(previews)
    void applyRescale(auto)
    if (ask.length || blocked.length) setPendingRescale(prev => [...prev, ...ask, ...blocked])
  }, [applyRescale])

  const recalcMut = useMutation({
    mutationFn: () => networkApi.recalculateLineLengths(),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
      toast.success(`Line lengths recalculated · ${r.updated} updated, ${r.skipped} skipped`)
      ingestRescale(r.rescale)
    },
    onError: () => toast.error('Could not recalculate line lengths'),
  })

  // Persist a map-side bus drag. Updates the canonical geographic
  // coordinates (bus.x = lng, bus.y = lat). Spreads the cached bus first so
  // the PUT carries the bus's full state — _update_component on the backend
  // does remove + add and would otherwise reset every other field to its
  // Pydantic default. The schematic / blank canvas is decoupled: it keeps
  // its own per-bus position cache in localStorage and is unaffected by
  // changes here for any bus the user has already laid out there.
  const updateBusPosMut = useMutation({
    mutationFn: ({ name, lat, lng }: { name: string; lat: number; lng: number }) => {
      const cached = (qc.getQueryData<Bus[]>(nk(useUIStore.getState().currentProject, 'buses')) ?? []).find(b => b.name === name)
      if (!cached) {
        // Refuse the partial PUT — without the cached row's full fields,
        // the backend's _update_component (remove + add) would reset
        // every omitted attribute (v_nom, carrier, control, sub_network,
        // country, …) to its Pydantic default. The buses query hydrates
        // on mount and a drag normally can't fire before then, but a
        // slow first network round-trip would expose the trap. Reject
        // explicitly rather than silently corrupt the bus.
        throw new Error(
          `Bus '${name}' not yet loaded from backend — wait for the buses query to settle and try the drag again.`
        )
      }
      const payload: Partial<Bus> = { ...cached, x: lng, y: lat }
      return networkApi.updateBus(name, payload)
    },
    onSuccess: (data, vars) => {
      // The backend's update_bus already recomputed the lengths of THIS bus's
      // connected lines (_recompute_lengths_for_bus, scoped to the moved bus)
      // and logged a changelog entry. We previously also called the global
      // recalculateLineLengths() here, which rewrote EVERY line in the network
      // (O(all lines) per drag) and produced a SECOND changelog entry per drag.
      // Drop that redundant whole-fleet pass — just refresh the two affected
      // caches so the connected lines re-render with their new lengths.
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'buses') })
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
      appLog('INFO', `Bus '${vars.name}' moved · connected line lengths recalculated.`)
      ingestRescale(data.rescale)
    },
    onError: (e: Error) => toast.error(`Move failed: ${e.message}`),
  })

  const handleRecalc = () => {
    confirmToast(
      'Recalculate every line\'s length from haversine distance? Overwrites existing length values (affects length-scaled capital costs).',
      () => recalcMut.mutate(),
      { confirmLabel: 'Recalculate' },
    )
  }

  // Leave placement mode when there is nothing left to place. The toast
  // OFFERS the line-length recalculation (D6) rather than running it —
  // `handleRecalc` still confirms before it writes, so accepting the offer
  // stays two deliberate clicks away from the model edit. Declared here
  // (below `handleRecalc`) rather than beside the rest of the placement
  // state above it, so the reference below isn't a use-before-define.
  useEffect(() => {
    // `(buses as Bus[]).length > 0` matters: switching project mid-placement
    // changes the query key, `data` falls back to `[]` for an instant, and
    // `unplaced.length === 0` is ALSO true for a genuinely empty bus list —
    // without this guard the user gets a false "Every bus now has a
    // location" toast offering a line-length rewrite against the NEW
    // project's (empty) network.
    if (placing && unplaced.length === 0 && (buses as Bus[]).length > 0) {
      setPlacing(false)
      setSkippedNames(new Set())
      toast.success(
        (t) => (
          <span className="flex items-center gap-2">
            Every bus now has a location.
            <button
              type="button"
              onClick={() => { toast.dismiss(t.id); handleRecalc() }}
              className="px-2 py-0.5 rounded border border-border text-[11px] hover:bg-border/30"
            >Recalculate line lengths</button>
          </span>
        ),
        { duration: 8000 },
      )
    }
  }, [placing, unplaced.length, buses])

  // ── Per-bus asset-category counts + resolved icon (for the right-click menu
  // + group markers). Mirrors the blank canvas: Thermal / Renewables /
  // Storage / Load. Count AND icon per bus × category. The badge is resolved
  // here, once, so the decision lives in one place and consumers just render
  // it. A parallel map keyed the same way would be the same drift risk one
  // level down.
  const categoryCountsByBus = useMemo(() => {
    const carriers = new Map<string, Record<AssetCategory, string[]>>()
    const out = new Map<string, Record<AssetCategory, CategoryEntry>>()
    for (const b of buses as Bus[]) {
      carriers.set(b.name, { Thermal: [], Renewables: [], Storage: [], Load: [] })
    }
    for (const g of generators as Generator[]) {
      const r = carriers.get(g.bus); if (!r) continue
      if (isRenewableCarrier(g.carrier)) r.Renewables.push(g.carrier)
      else r.Thermal.push(g.carrier)
    }
    for (const l of loads as Load[]) { const r = carriers.get(l.bus); if (r) r.Load.push(l.carrier ?? '') }
    for (const s of sus as StorageUnit[]) { const r = carriers.get(s.bus); if (r) r.Storage.push(s.carrier) }
    for (const s of stores as Store[]) { const r = carriers.get(s.bus); if (r) r.Storage.push(s.carrier) }
    for (const [busName, byCat] of carriers) {
      out.set(busName, {
        Thermal:    { count: byCat.Thermal.length,    badge: uniformBadge(byCat.Thermal) },
        Renewables: { count: byCat.Renewables.length, badge: uniformBadge(byCat.Renewables) },
        Storage:    { count: byCat.Storage.length,    badge: uniformBadge(byCat.Storage) },
        Load:       { count: byCat.Load.length,       badge: uniformBadge(byCat.Load) },
      })
    }
    return out
  }, [buses, generators, loads, sus, stores])

  // Right-click context menu state (matches TopologyCanvas's contextMenu).
  interface MapCtxMenu { x: number; y: number; busName: string }
  const [ctxMenu, setCtxMenu] = useState<MapCtxMenu | null>(null)
  // Set of "bus::category" pairs whose asset-group bubble is currently shown
  // on the map. Independent from the blank canvas's set — same UX, separate
  // state, mirroring the layout-decoupling we did earlier.
  const [visibleGroups, setVisibleGroups] = useState<Set<string>>(new Set())
  // User-overridden bubble offsets, keyed by "bus::category". Lazy-init from
  // localStorage so dragged positions persist across reloads. Bus drags do
  // NOT change these (the offset is relative to bus pixel position, so the
  // bubble follows the bus visually).
  const [assetOffsets, setAssetOffsets] = useState<AssetOffsets>(loadAssetOffsets)

  // Per-line/link/transformer waypoints (lat/lng), keyed by edgeKind:name.
  // Updated on every waypoint drag + persisted to localStorage. Visual only:
  // the line's `length` field on the backend stays untouched.
  const [lineWaypoints, setLineWaypoints] = useState<LineWaypoints>(loadLineWaypoints)

  // Reload the per-project map layout (bubble offsets + line waypoints) when
  // the active project changes — this component isn't remounted on a project
  // switch, so the lazy-init useState above wouldn't pick up the new project.
  //
  // Defensive ordering (matches the StrictMode-safe pattern in TopologyCanvas's
  // layout-fetch effect): set the "loaded for" marker AFTER the work completes,
  // not before. The work here is synchronous (localStorage reads + setState),
  // so it doesn't actually race in dev under StrictMode — but keeping the same
  // shape across both canvases avoids "why is this one different?" confusion
  // when the next maintainer reads the two files side by side.
  const mapLayoutLoadedFor = useRef<string | null>(currentProject)
  useEffect(() => {
    if (mapLayoutLoadedFor.current === currentProject) return
    setAssetOffsets(loadAssetOffsets())
    setLineWaypoints(loadLineWaypoints())
    // Clear open asset-group bubbles too — their `bus::category` keys are
    // project-specific, so a stale pinned bubble from the previous project
    // would otherwise linger (and mis-render if the two projects share a bus
    // name, since its saved offset key no longer matches).
    setVisibleGroups(new Set())
    mapLayoutLoadedFor.current = currentProject
  }, [currentProject])
  const updateWaypoints = useCallback((edgeId: string, wps: LatLngTuple[]) => {
    setLineWaypoints(prev => {
      const next = { ...prev }
      if (wps.length === 0) delete next[edgeId]
      else next[edgeId] = wps
      saveLineWaypoints(next)
      return next
    })
  }, [])

  const toggleGroup = useCallback((busName: string, cat: AssetCategory) => {
    const id = `${busName}::${cat}`
    setVisibleGroups(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setCtxMenu(null)
  }, [])

  // Close menu on Escape / outside click.
  useEffect(() => {
    if (!ctxMenu) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setCtxMenu(null) }
    const onClick = () => setCtxMenu(null)
    window.addEventListener('keydown', onKey)
    document.addEventListener('click', onClick)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('click', onClick)
    }
  }, [ctxMenu])

  // Default to roughly central Europe so first render isn't a featureless
  // ocean. FitToNetwork takes over as soon as bus data arrives.
  const initialCenter: [number, number] = [50.0, 10.0]

  return (
    <div className="relative h-full w-full">
      <MapContainer
        center={initialCenter}
        zoom={5}
        scrollWheelZoom
        style={{ height: '100%', width: '100%' }}
      >
        {mode === 'satellite' ? (
          // Pure satellite imagery — no labels.
          <TileLayer url={ESRI_IMAGERY_URL} attribution={ESRI_ATTRIBUTION} maxZoom={19} />
        ) : (
          // Hybrid — imagery with transparent street + place/POI label overlays
          // drawn ON TOP (like Google's "Hybrid"). Order matters: roads first,
          // labels last (labels win). All three carry the Esri attribution
          // (Leaflet dedupes identical strings) for ToS compliance.
          <>
            <TileLayer url={ESRI_IMAGERY_URL} attribution={ESRI_ATTRIBUTION} maxZoom={19} />
            <TileLayer url={ESRI_TRANSPORTATION_URL} attribution={ESRI_ATTRIBUTION} maxZoom={19} />
            <TileLayer url={ESRI_PLACES_URL} attribution={ESRI_ATTRIBUTION} maxZoom={19} />
          </>
        )}

        <FitToNetwork buses={buses as Bus[]} suspended={placing} />

        {placing && placingBus && placementUiAllowed && (
          <ClickToPlace onPick={(lat, lng) => {
            // `unplaced` only shrinks after the PUT round-trips and the buses
            // query refetches, so without this guard two quick clicks both
            // target the current `placingBus` — the second overwrites the
            // first instead of advancing to the next bus. Ignoring the click
            // while a placement PUT is already in flight makes each click
            // advance the queue by exactly one bus.
            if (updateBusPosMut.isPending) return
            updateBusPosMut.mutate({ name: placingBus, lat, lng })
          }} />
        )}

        {/* Lines — colour by the lower of the two bus voltages. Routable. */}
        {(lines as LineT[]).map(line => {
          const b0 = busByName.get(line.bus0)
          const b1 = busByName.get(line.bus1)
          if (!b0 || !b1) return null
          const c0 = busLatLng(b0)
          const c1 = busLatLng(b1)
          if (!c0 || !c1) return null
          const edgeId = `line:${line.name}`
          const v = Math.min(b0.v_nom ?? 0, b1.v_nom ?? 0)
          // Results overlay: recolour by loading band + surface flow in the
          // tooltip. Falls back to the voltage-class colour when off.
          const flow = results.enabled ? results.byLine.get(line.name) : undefined
          return (
            <EditableLine
              key={edgeId}
              id={edgeId}
              source={c0}
              target={c1}
              waypoints={lineWaypoints[edgeId] ?? []}
              onUpdate={(wps) => updateWaypoints(edgeId, wps)}
              onSelect={() => setSelectedComponent({ type: 'Line', name: line.name })}
              color={flow ? loadingColor(flow.loadingPct) : lineColor(v)}
              weight={flow ? 4 : 3}
              tooltip={flow
                ? `${line.name} · ${fmtMW(flow.p0)} · ${flow.loadingPct.toFixed(0)}% of ${flow.sNom.toFixed(0)} MVA`
                : `${line.name} · ${line.s_nom?.toFixed(0) ?? '—'} MVA`}
              permanentLabel={flow
                ? `${fmtMW(Math.abs(flow.p0))} (${flow.loadingPct.toFixed(0)}%)`
                : undefined}
            />
          )
        })}

        {/* Links — dashed to distinguish from AC lines. Routable. When
            the results overlay is on, the link tints with the same
            loading-band palette as Lines so the playback animates H2 /
            heat / DC flows alongside the electrical AC flows. Weight
            stays one step thinner than Lines (3 with flow, 2 without)
            so the user can tell them apart visually even under colour
            tinting. */}
        {(links as LinkT[]).map(link => {
          const b0 = busByName.get(link.bus0)
          const b1 = busByName.get(link.bus1)
          if (!b0 || !b1) return null
          const c0 = busLatLng(b0)
          const c1 = busLatLng(b1)
          if (!c0 || !c1) return null
          const edgeId = `link:${link.name}`
          const linkFlow = results.enabled ? results.byLink.get(link.name) : undefined
          return (
            <EditableLine
              key={edgeId}
              id={edgeId}
              source={c0}
              target={c1}
              waypoints={lineWaypoints[edgeId] ?? []}
              onUpdate={(wps) => updateWaypoints(edgeId, wps)}
              onSelect={() => setSelectedComponent({ type: 'Link', name: link.name })}
              color={linkFlow ? loadingColor(linkFlow.loadingPct) : '#a16207'}
              weight={linkFlow ? 3 : 2}
              dashArray="6 4"
              tooltip={linkFlow
                ? `${link.name} · ${link.carrier || 'Link'} · ${fmtMW(linkFlow.p0)} · ${linkFlow.loadingPct.toFixed(0)}% of ${linkFlow.pNom.toFixed(0)} MW`
                : `${link.name} · ${link.carrier || 'Link'} · ${link.p_nom?.toFixed(0) ?? '—'} MW`}
              permanentLabel={linkFlow
                ? `${fmtMW(Math.abs(linkFlow.p0))} (${linkFlow.loadingPct.toFixed(0)}%)`
                : undefined}
            />
          )
        })}

        {/* Transformers — routable polyline + IEC two-circle pictogram at
            the polyline's midpoint (computed from the *visual* path including
            waypoints, so the symbol stays centred on the routed shape). */}
        {(transformers as Transformer[]).map(tr => {
          const b0 = busByName.get(tr.bus0)
          const b1 = busByName.get(tr.bus1)
          if (!b0 || !b1) return null
          const c0 = busLatLng(b0)
          const c1 = busLatLng(b1)
          if (!c0 || !c1) return null
          const edgeId = `tr:${tr.name}`
          const wps = lineWaypoints[edgeId] ?? []
          const path: LatLngTuple[] = [c0, ...wps, c1]
          // Midpoint = the geometric centre of the polyline (counts segment
          // boundaries, not arc length — close enough at typical scales).
          const midIdx = Math.floor((path.length - 1) / 2)
          const mid: LatLngTuple = [
            (path[midIdx][0] + path[midIdx + 1][0]) / 2,
            (path[midIdx][1] + path[midIdx + 1][1]) / 2,
          ]
          const trColor = '#16a34a'
          return (
            <Fragment key={edgeId}>
              <EditableLine
                id={edgeId}
                source={c0}
                target={c1}
                waypoints={wps}
                onUpdate={(next) => updateWaypoints(edgeId, next)}
                onSelect={() => setSelectedComponent({ type: 'Transformer', name: tr.name })}
                color={trColor}
                weight={2.5}
                tooltip={`${tr.name} · ${tr.v_nom_0 ?? '?'}/${tr.v_nom_1 ?? '?'} kV · ${tr.s_nom?.toFixed(0) ?? '—'} MVA`}
              />
              <Marker
                position={mid}
                icon={transformerDivIcon(trColor)}
                eventHandlers={{ click: () => setSelectedComponent({ type: 'Transformer', name: tr.name }) }}
              >
                <Tooltip>{tr.name}</Tooltip>
              </Marker>
            </Fragment>
          )
        })}

        {/* Asset-group bubbles — bus-anchored markers + iconAnchor pixel
            offset, so they stay glued to their bus at any zoom. Lives inside
            MapContainer so it can call useMap(). */}
        <AssetGroupLayer
          busByName={busByName}
          visibleGroups={visibleGroups}
          categoryCountsByBus={categoryCountsByBus}
          offsets={assetOffsets}
          setOffsets={setAssetOffsets}
          onSelect={(busName, cat) =>
            setSelectedComponent({ type: 'AssetGroup', name: `${busName}::${cat}` })}
        />

        {/* Buses on top — draggable. dragend writes the new lat/lng back to
            bus.x / bus.y. A drag gesture in leaflet does NOT fire a click
            on dragend, so the click → select-bus behaviour is unaffected. */}
        {(buses as Bus[]).map(bus => {
          const c = busLatLng(bus)
          if (!c) return null
          const colour = lineColor(bus.v_nom ?? 1)
          // Results overlay: generation feeding / load drawn from this bus at
          // the selected snapshot, appended to the permanent name label.
          const busOverlay = results.enabled ? results.byBus.get(bus.name) : undefined
          return (
            <Marker
              key={bus.name}
              position={c}
              draggable
              icon={busDivIcon(colour)}
              eventHandlers={{
                click: () => setSelectedComponent({ type: 'Bus', name: bus.name }),
                contextmenu: (e) => {
                  // originalEvent is the underlying DOM mouse event — needed
                  // for client-pixel coords + preventDefault on the native
                  // browser context menu.
                  const oe = e.originalEvent as MouseEvent
                  oe.preventDefault()
                  setCtxMenu({ x: oe.clientX, y: oe.clientY, busName: bus.name })
                },
                dragend: (e) => {
                  const ll = (e.target as L.Marker).getLatLng()
                  updateBusPosMut.mutate({ name: bus.name, lat: ll.lat, lng: ll.lng })
                },
              }}
            >
              <Tooltip permanent direction="top" offset={[0, -8]} className="map-bus-label">
                {bus.name}
                {/* Skip-zero rendering: a pure-load bus shows only "▼ 289 MW"
                    instead of "▲ 0 kW · ▼ 289 MW" — kills the "0 kW" clutter
                    the user flagged + keeps units consistent (both sides use
                    fmtMW's auto-scaling but a single visible side avoids
                    mixed-unit rows like "0 kW · 289 MW"). 0.05 MW = 50 kW
                    epsilon hides numerical-noise spillover. */}
                {busOverlay && (busOverlay.gen > 0.05 || busOverlay.load > 0.05) && (
                  <span style={{ display: 'block', fontWeight: 400, opacity: 0.85 }}>
                    {busOverlay.gen > 0.05 && (
                      <span style={{ color: '#16a34a' }}>▲ {fmtMW(busOverlay.gen)}</span>
                    )}
                    {busOverlay.gen > 0.05 && busOverlay.load > 0.05 && (
                      <span style={{ opacity: 0.5 }}> · </span>
                    )}
                    {busOverlay.load > 0.05 && (
                      <span style={{ color: '#d97706' }}>▼ {fmtMW(busOverlay.load)}</span>
                    )}
                  </span>
                )}
                {/* Per-carrier breakdown — appears as additional rows in the
                    permanent tooltip only when the bus hosts >1 carrier of
                    activity at this snapshot. Markers are small geographic
                    dots so the donut-style indicator we use on the schematic
                    canvas doesn't fit; the tooltip surface is the right
                    place for the detail here. */}
                {busOverlay?.byCarrier && busOverlay.byCarrier.size > 1 && (() => {
                  const rows: Array<{ carrier: string; gen: number; load: number; mag: number }> = []
                  for (const [carrier, v] of busOverlay.byCarrier) {
                    const mag = Math.abs(v.gen) + Math.abs(v.load)
                    if (mag > 0.05) rows.push({ carrier, gen: v.gen, load: v.load, mag })
                  }
                  if (rows.length <= 1) return null
                  rows.sort((a, b) => b.mag - a.mag)
                  return (
                    <span style={{ display: 'block', fontWeight: 400, opacity: 0.7, fontSize: 10 }}>
                      {rows.map(r => `${r.carrier}: ▲${fmtMW(r.gen)} ▼${fmtMW(r.load)}`).join(' · ')}
                    </span>
                  )
                })()}
              </Tooltip>
            </Marker>
          )
        })}
      </MapContainer>

      {/* Map toolbar (recalculate line lengths). Sits over the map at the
          top-left, below the zoom buttons that Leaflet renders on its own. */}
      <div
        className="absolute z-[400] flex flex-col gap-1.5"
        style={{ top: 88, left: 10 }}
      >
        <button
          onClick={handleRecalc}
          disabled={recalcMut.isPending}
          title="Rewrite line.length (km) from haversine distance between bus0/bus1 coords"
          className="w-8 h-8 flex items-center justify-center bg-bg border border-border rounded shadow text-text hover:text-accent hover:border-accent transition-colors disabled:opacity-40"
        >
          <Ruler size={14} />
        </button>
      </div>

      {/* Hidden while a slide panel or the command palette is open — both are
          higher-priority z-[500]/z-[300] overlays and the panel's z-[900]
          would otherwise float on top of them (same guard MapModeSwitcher
          applies for the same reason). */}
      {placementUiAllowed && (
        <UnplacedBusesPanel
          unplacedCount={unplaced.length}
          totalCount={(buses as Bus[]).length}
          placing={placing}
          onStartPlacing={() => setPlacing(true)}
        />
      )}

      {/* Rescale prompt for length changes that materially move r/x/b. NOT
          gated on `placementUiAllowed` — a modal Dialog isn't floating map
          furniture, it already sits above everything at z-[9999] and traps
          focus, so it doesn't need to hide behind a slide panel or the
          command palette. It IS gated on `!placing`: while placement is
          running, `ingestRescale` still queues into `pendingRescale`, but
          opening a modal mid-placement would break the click-to-place flow
          (B5) — the batch surfaces once placement ends, whichever way it
          ends (last bus placed, Escape, or "Done"). */}
      {!placing && (
        <RescaleDialog
          previews={pendingRescale.filter(p => p.skipped_reason === null)}
          blocked={pendingRescale.filter(p => p.skipped_reason !== null)}
          onAccept={async () => {
            await applyRescale(pendingRescale.filter(p => p.skipped_reason === null))
            setPendingRescale([])
          }}
          onDecline={() => setPendingRescale([])}
        />
      )}

      {/* Placement strip. z-[900], not the brief's z-[500]: Leaflet's own
          zoom control sits at z-800 (documented pitfall in this codebase —
          "Floating UI z-index < 800 over Leaflet map"), and z-500 would
          render the strip UNDER it. Also gated on `placementUiAllowed` — the
          UnplacedBusesPanel guard just above applies to this strip too, so a
          Cmd+K command-palette open (z-500) can't be covered by this strip's
          higher z-[900], and ClickToPlace (gated the same way) can't leave a
          bus-placing click live on the map with no visible indicator. */}
      {placing && placingBus && placementUiAllowed && (
        <div
          className="absolute z-[900] left-1/2 -translate-x-1/2 flex items-center gap-3 px-3 py-2
                     bg-bg border border-border rounded-lg shadow-lg text-xs"
          style={{ bottom: 24 }}
        >
          <span className="text-text">
            Placing <span className="font-mono font-medium">{placingBus}</span> — click the map
            <span className="text-muted"> ({unplaced.length} left)</span>
          </span>
          <button
            type="button"
            onClick={() => setSkippedNames(prev => new Set(prev).add(placingBus))}
            disabled={!canSkip(unplaced, skippedNames)}
            className="px-2 py-1 rounded border border-border text-text hover:bg-border/30
                       transition-colors disabled:opacity-35"
          >Skip</button>
          <button
            type="button"
            onClick={() => { setPlacing(false); setSkippedNames(new Set()) }}
            className="px-2 py-1 rounded bg-accent text-white hover:opacity-90 transition-opacity"
          >Done</button>
        </div>
      )}

      {/* Bus right-click context menu — same layout & options as the blank
          canvas equivalent. Local visibleGroups state means the two views
          stay decoupled (mirrors the layout decoupling). */}
      {ctxMenu && (() => {
        const counts = categoryCountsByBus.get(ctxMenu.busName)
        const cats: AssetCategory[] = ['Thermal', 'Renewables', 'Storage', 'Load']
        const withAssets = cats.filter(c => (counts?.[c]?.count ?? 0) > 0)
        const expandedIds = withAssets.map(c => `${ctxMenu.busName}::${c}`)
        const allVisible = expandedIds.length > 0 && expandedIds.every(id => visibleGroups.has(id))
        const anyVisible = expandedIds.some(id => visibleGroups.has(id))
        return (
          <div
            className="fixed z-[600] bg-bg border border-border rounded-lg shadow-lg py-1 min-w-[200px]"
            style={{ left: ctxMenu.x, top: ctxMenu.y }}
            onClick={e => e.stopPropagation()}
          >
            <div className="px-3 py-1.5 text-[10px] font-bold text-muted uppercase tracking-wider border-b border-border mb-1">
              {ctxMenu.busName}
            </div>
            <button
              onClick={() => {
                setSelectedComponent({ type: 'Bus', name: ctxMenu.busName })
                setCtxMenu(null)
              }}
              className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left hover:bg-border/30 transition-colors text-text"
            >
              Properties
            </button>
            {withAssets.length > 0 && (
              <div className="px-3 py-1 flex gap-1.5 border-b border-border mb-1">
                <button
                  onClick={() => {
                    setVisibleGroups(prev => {
                      const next = new Set(prev); expandedIds.forEach(id => next.add(id)); return next
                    })
                    setCtxMenu(null)
                  }}
                  disabled={allVisible}
                  className="flex-1 py-1 text-[10px] rounded border border-border text-text hover:bg-border/30 transition-colors disabled:opacity-35"
                >Show all</button>
                <button
                  onClick={() => {
                    setVisibleGroups(prev => {
                      const next = new Set(prev); expandedIds.forEach(id => next.delete(id)); return next
                    })
                    setCtxMenu(null)
                  }}
                  disabled={!anyVisible}
                  className="flex-1 py-1 text-[10px] rounded border border-border text-text hover:bg-border/30 transition-colors disabled:opacity-35"
                >Hide all</button>
              </div>
            )}
            {cats.map(cat => {
              const count = counts?.[cat]?.count ?? 0
              const id = `${ctxMenu.busName}::${cat}`
              const isVisible = visibleGroups.has(id)
              const cfg = CATEGORY_STYLE[cat]
              return (
                <button
                  key={cat}
                  disabled={count === 0}
                  onClick={() => toggleGroup(ctxMenu.busName, cat)}
                  className={`flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left transition-colors
                    ${count > 0 ? 'hover:bg-border/30 cursor-pointer' : 'opacity-35 cursor-not-allowed'}
                    ${isVisible ? 'text-accent font-medium' : 'text-text'}`}
                >
                  <cfg.Icon size={12} style={{ color: count > 0 ? cfg.color : undefined }} />
                  <span>{isVisible ? 'Hide' : 'Show'} {CATEGORY_LABELS[cat]}</span>
                  <span className="ml-auto text-muted font-mono">{count}</span>
                </button>
              )
            })}
          </div>
        )
      })()}
    </div>
  )
}

// Public entry point — wraps the map in <CanvasResultsProvider> so the bus
// markers, lines, and asset bubbles can read the per-snapshot results overlay.
// Mirrors how TopologyCanvas wraps the schematic canvas; only one of the two
// is ever mounted at a time, so there's no duplicate result fetching.
export default function MapCanvas(props: MapCanvasProps) {
  return (
    <CanvasResultsProvider>
      <MapCanvasInner {...props} />
    </CanvasResultsProvider>
  )
}
