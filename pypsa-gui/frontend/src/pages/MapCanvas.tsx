import { Fragment, Suspense, lazy, useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { MapContainer, TileLayer, Marker, Polyline, Polygon, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'
import { isRenewableCarrier } from '../utils/carriers'
import { uniformBadge, type BadgeDef } from '../utils/carrierBadges'
import { Ruler, Flame, Wind, BatteryCharging, Zap, ExternalLink } from 'lucide-react'
import ReactDOMServer from 'react-dom/server'
import { useUIStore, type CanvasView } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import { updateAsset } from '../utils/assetWrite'
import { networkApi, type LengthsFromGeometryResult } from '../api/network'
import { projectsApi } from '../api/projects'
import { appLog } from '../store/simulationStore'
import type { Bus, Generator, Line as LineT, Link as LinkT, Load, ProjectInfo, StorageUnit, Store, Transformer } from '../api/types'
import { CanvasResultsProvider, useCanvasResults, fmtMW, loadingColor } from '../components/CanvasResultsContext'
import { busLatLng, fmtKm, lengthDisagrees, routeLengthKm, unplacedBusNames } from '../utils/geo'
import { nextBusToPlace, canSkip } from '../utils/placement'
import { ingestRescale } from '../utils/rescaleActions'
import { useRescaleStore } from '../store/rescaleStore'
import UnplacedBusesPanel from '../components/UnplacedBusesPanel'
// Lazy: the panel is needed only once a boundary has been drawn, and it
// keeps the main chunk within the 3D site view's size budget (plan §QA-2).
const SiteDraftPanel = lazy(() => import('../components/SiteDraftPanel'))
import { useSiteDraw, newSiteButtonVisible } from '../site3d/useSiteDraw'
import { useSitesStore } from '../site3d/sitesStore'
import { readActiveSite, writeActiveSite } from '../site3d/activeSite'
import type { LngLatTuple, Site } from '../site3d/types'
import type { MapBubble } from '../api/mapLayout'
import {
  bubbleKey, queueLengthFromGeometry, routeWaypoints, setLengthsDerivedSink, useMapLayoutLifecycle,
  useMapLayoutStore, type LatLngTuple,
} from './mapLayoutStore'

/**
 * HTML-attribute escaping for the divIcon's `html` string. The marker markup
 * is built as a string, so a bus called `A"B` would close the attribute early
 * and the drop hit-test would recover the wrong name. Escaping & first is
 * required — doing it later would double-escape the entities the other
 * replacements introduce.
 */
function escapeAttr(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// Draggable bus marker. Mimics the previous CircleMarker visually (12 px,
// 2 px coloured border, white fill) but uses a Marker + divIcon so leaflet
// gives us the `draggable` capability and a `dragend` event. The cursor
// changes to "grab" so users discover that the dot is draggable.
//
// `data-bus-name` is the drop hit-test's only handle on which bus was hit —
// the same attribute TopologyCanvas's BusNode publishes, so hooks/
// useAssetDrag.ts needs exactly one branch for both canvases (spec D25).
export function busDivIcon(color: string, name: string): L.DivIcon {
  return L.divIcon({
    className: 'pypsa-bus-marker',
    html: `<div data-bus-name="${escapeAttr(name)}" style="width:12px;height:12px;border:2px solid ${color};background:#fff;border-radius:50%;box-sizing:border-box;cursor:grab;"></div>`,
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

// The map's user layout — asset-group bubble offsets and line waypoints — is
// the project's `map_layout.json` sidecar, owned by `pages/mapLayoutStore.ts`:
// memory-first, a debounced PUT, flushed on the save paths, localStorage only
// as the fallback for a failed write. It travels with the project bundle like
// the blank canvas's layout.json, so a routed line survives a reload on
// another machine, a scenario fork, a snapshot and an export.
//
// Bubble offsets are pixel offsets from the bus (keyed `<bus>|<category>`), so
// a bus drag still sweeps its bubbles along, preserving the user's *relative*
// layout choice. Waypoints are keyed by edge id — "line:L1", "link:L2",
// "tr:T1" — and are the INTERIOR vertices of the route, so a bus drag keeps
// the bend and only the chord's ends move. The store speaks the map's
// `[lat, lng]` tuples and converts to the document's `[lng, lat]` at its
// boundary. Lengths follow geometry only by consent (plan 2, M2): with the
// project setting "Derive lengths from geometry" on, a route edit or a bus
// drag rewrites the affected Line/Link lengths server-side and offers the
// impedance rescale; off, nothing is written and a discrepancy badge flags a
// stored length that disagrees with its geometry, with "Use geometry" per
// branch.
type AssetOffsets = Record<string, MapBubble>

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
  mode: Exclude<CanvasView, 'blank' | 'site'>
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

// Site boundary drawing (WP2, design D5). Mounted inside <MapContainer> only
// while the draw hook owns the map's clicks — never alongside ClickToPlace,
// which `mapClickOwner` guarantees. Double-click zoom is off for the
// duration so the closing double-click does not also zoom the map.
function SiteDrawLayer({
  onVertex, onDoubleClick,
}: {
  onVertex: (lng: number, lat: number) => void
  onDoubleClick: (lng: number, lat: number) => void
}) {
  const map = useMapEvents({
    click: (e) => onVertex(e.latlng.lng, e.latlng.lat),
    dblclick: (e) => onDoubleClick(e.latlng.lng, e.latlng.lat),
  })
  useEffect(() => {
    map.doubleClickZoom.disable()
    const el = map.getContainer()
    const previous = el.style.cursor
    el.style.cursor = 'crosshair'
    return () => { map.doubleClickZoom.enable(); el.style.cursor = previous }
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
// stored offset, and hand it to the map layout store. bus.x / bus.y are NEVER
// touched.
interface AssetGroupLayerProps {
  busByName: Map<string, Bus>
  visibleGroups: Set<string>
  categoryCountsByBus: Map<string, Record<AssetCategory, CategoryEntry>>
  onSelect: (busName: string, cat: AssetCategory) => void
  /** Keyed `bubbleKey(bus, category)`. */
  offsets: AssetOffsets
  onOffsetChange: (key: string, offset: MapBubble) => void
}
function AssetGroupLayer({
  busByName, visibleGroups, categoryCountsByBus, onSelect, offsets, onOffsetChange,
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

        const offsetKey = bubbleKey(busName, cat)
        const offset = offsets[offsetKey] ?? CATEGORY_STYLE[cat]
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
                onOffsetChange(offsetKey, {
                  dx: offset.dx + (dropPx.x - busPx.x),
                  dy: offset.dy + (dropPx.y - busPx.y),
                })
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
// fps smoothness; on dragend we commit the new waypoints to the map layout
// store (`map_layout.json`). This component never writes length / r / x:
// with the project setting "Derive lengths from geometry" on, the STORE asks
// the server to rewrite the length once the route has landed (plan M2), and
// the rescale stays a preview the user accepts.
//
// `lengthBadge` is the discrepancy badge: shown when the stored length
// disagrees with the geometry (`lengthDisagrees`), with a one-click "Use
// geometry" that derives this one branch — an explicit act, allowed whatever
// the setting says.
export interface LengthBadge {
  stored: number
  geometry: number
  /** Absent when the project is read-only: the badge still informs. */
  onUseGeometry?: () => void
}

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
  lengthBadge?: LengthBadge
}

function EditableLine({
  id, source, target, waypoints, onUpdate, onSelect, color, weight, dashArray, tooltip, permanentLabel, lengthBadge,
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
        {/* Discrepancy badge (plan M2) — the same permanent-tooltip plumbing
            as the flow label, anchored below the line's centre so the two
            never overlap, and `interactive` so its button takes the click
            instead of the map. */}
        {lengthBadge && (
          <Tooltip permanent interactive direction="bottom" offset={[0, 6]} className="map-length-badge">
            <span
              className="map-length-badge__text"
              title={`stored ${fmtKm(lengthBadge.stored)}, geometry ${fmtKm(lengthBadge.geometry)}`}
            >
              ⚠ {fmtKm(lengthBadge.stored)} stored · {fmtKm(lengthBadge.geometry)} geometry
            </span>
            {lengthBadge.onUseGeometry && (
              <button
                type="button"
                className="map-length-badge__use"
                title={`Set ${id} length to its geometry (${fmtKm(lengthBadge.geometry)}) and preview the impedance rescale`}
                onClick={(e) => { e.stopPropagation(); lengthBadge.onUseGeometry?.() }}
              >
                Use geometry
              </button>
            )}
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

  // ── Sites (WP2) ────────────────────────────────────────────────────────────
  const draw = useSiteDraw()
  const sitesDoc = useSitesStore(s => s.docFor(currentProject))
  const removeSite = useSitesStore(s => s.removeSite)
  const setActiveSiteId = useUIStore(s => s.setActiveSiteId)
  const setCanvasView = useUIStore(s => s.setCanvasView)
  const readOnly = useUIStore(s => s.readOnly)
  // The boundary just closed, awaiting a name; or the site being edited.
  const [draftBoundary, setDraftBoundary] = useState<LngLatTuple[] | null>(null)
  const [editSite, setEditSite] = useState<Site | null>(null)
  // Popover on a clicked site polygon, in container pixels.
  const [sitePopover, setSitePopover] = useState<{ id: string; x: number; y: number } | null>(null)
  // A project switch drops any site UI left open for the previous project
  // (the store already resets the draft itself).
  useEffect(() => { setDraftBoundary(null); setEditSite(null); setSitePopover(null) }, [currentProject])
  // Keyboard while drawing: Enter closes, Escape cancels. Not while the user
  // is typing somewhere else, and not while a panel hides the map.
  useEffect(() => {
    if (!draw.drawing || !placementUiAllowed) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (e.key === 'Escape') { e.preventDefault(); draw.cancel() }
      else if (e.key === 'Enter') {
        e.preventDefault()
        const r = draw.finish()
        if (r.status === 'closed') setDraftBoundary(r.boundary)
        else toast.error('A site needs at least three corners')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [draw, placementUiAllowed])
  const openIn3D = (site: Site) => {
    setActiveSiteId(site.id)
    writeActiveSite(currentProject, site.id)
    setSitePopover(null)
    setCanvasView('site')
  }
  const deleteSite = (site: Site) => {
    setSitePopover(null)
    confirmToast(`Delete site "${site.name}"? Its boundary and placements are removed; the network is untouched.`, () => {
      removeSite(currentProject, site.id)
      if (readActiveSite(currentProject) === site.id) { writeActiveSite(currentProject, null); setActiveSiteId(null) }
    }, { confirmLabel: 'Delete' })
  }

  // Mirror `placing` into the shared rescale store so RescaleDialogHost (at
  // App.tsx level, see store/rescaleStore.ts) knows to withhold the modal
  // while click-to-place is running — a Dialog stealing focus mid-click
  // would break B5. Two effects rather than one: the first keeps the store
  // in lockstep with every `placing` transition; the second unconditionally
  // clears it on unmount, covering the case where the user switches the
  // canvas away from the map (App.tsx swaps MapCanvas out for TopologyCanvas
  // on `canvasView === 'blank'`) WHILE placement is still active — without
  // it, `placementActive` would stay stuck `true` and the dialog would never
  // open again for the rest of the session.
  useEffect(() => {
    useRescaleStore.getState().setPlacementActive(placing)
  }, [placing])
  useEffect(() => {
    return () => { useRescaleStore.getState().setPlacementActive(false) }
  }, [])

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

  // Impedance-rescale previews from this component's write paths (drag +
  // recalc) are queued into the app-wide store via `ingestRescale` — see
  // store/rescaleStore.ts / utils/rescaleActions.ts for why this is no
  // longer a local `useState` here. RescaleDialogHost (rendered once at
  // App.tsx level) owns the actual dialog + apply/decline handling.
  const recalcMut = useMutation({
    mutationFn: () => networkApi.recalculateLineLengths(),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: nk(useUIStore.getState().currentProject, 'lines') })
      toast.success(`Line lengths recalculated · ${r.updated} updated, ${r.skipped} skipped`)
      ingestRescale(qc, r.rescale)
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
    // The Asset-write chokepoint (utils/assetWrite.ts) owns fetch, spread,
    // PUT and invalidation. The old throw-on-cache-miss ("wait for the
    // buses query to settle") is gone: the chokepoint FETCHES on a miss
    // (ruling 3), so a drag racing the first buses round-trip now succeeds
    // instead of asking the user to retry — while the bare-fields PUT the
    // throw guarded against stays unrepresentable.
    mutationFn: async ({ name, lat, lng }: { name: string; lat: number; lng: number }) => {
      const resp = await updateAsset<Bus>(
        qc, useUIStore.getState().currentProject, 'buses', name, { x: lng, y: lat })
      return resp as Awaited<ReturnType<typeof networkApi.updateBus>>
    },
    onSuccess: (data, vars) => {
      // The backend's update_bus already recomputed the lengths of THIS bus's
      // connected branches (_recompute_lengths_for_bus, scoped to the moved
      // bus — lines by chord, or lines AND links from their geometry with the
      // project setting on) and logged a changelog entry; the chokepoint's
      // blanket invalidation covers the buses, lines and links refetch.
      const sources = data.length_sources ?? {}
      const n = Object.keys(sources).length
      appLog('INFO', `Bus '${vars.name}' moved · ${n} connected branch length${n === 1 ? '' : 's'} recalculated${
        Object.values(sources).includes('route') ? ' (routes followed)' : ''}.`)
      useMapLayoutStore.getState().applyLengthSources(useUIStore.getState().currentProject, data.length_sources)
      ingestRescale(qc, data.rescale)
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
  // The per-project map layout (bubble offsets + line waypoints) lives in the
  // map layout store, which loads the project's `map_layout.json` here and
  // flushes a pending write on unmount / project change / pagehide. The
  // document is selected by project, so a project switch re-renders with the
  // new project's routes without this component remounting.
  useMapLayoutLifecycle(currentProject)
  const mapLayoutDoc = useMapLayoutStore(s => s.docFor(currentProject))
  const setRouteWaypoints = useMapLayoutStore(s => s.setRouteWaypoints)
  const setBubble = useMapLayoutStore(s => s.setBubble)
  // User-overridden bubble offsets, keyed `bubbleKey(bus, category)`. Bus
  // drags do NOT change these (the offset is relative to bus pixel position,
  // so the bubble follows the bus visually).
  const assetOffsets: AssetOffsets = mapLayoutDoc.bubbles
  // Per-line/link/transformer interior waypoints in the map's [lat, lng],
  // keyed by edgeKind:name. Lengths follow them only through the server and
  // only by consent — see `updateWaypoints` below.
  const lineWaypoints = useMemo(() => routeWaypoints(mapLayoutDoc), [mapLayoutDoc])

  // Clear open asset-group bubbles when the active project changes — their
  // `bus::category` keys are project-specific, so a stale pinned bubble from
  // the previous project would otherwise linger (and mis-render if the two
  // projects share a bus name).
  const mapLayoutLoadedFor = useRef<string | null>(currentProject)
  useEffect(() => {
    if (mapLayoutLoadedFor.current === currentProject) return
    setVisibleGroups(new Set())
    mapLayoutLoadedFor.current = currentProject
  }, [currentProject])
  // ── Lengths from geometry (plan M2) ──────────────────────────────────────
  // The project setting lives in ProjectInfo (metadata.json); the list query
  // is the one every project surface already shares.
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'], queryFn: () => projectsApi.list(), staleTime: 30_000, enabled: !!currentProject,
  })
  const deriveLengths = (projects as ProjectInfo[]).find(p => p.name === currentProject)
    ?.settings?.derive_lengths_from_geometry ?? false
  // A ref so `updateWaypoints` stays referentially stable across toggles.
  const deriveLengthsRef = useRef(deriveLengths)
  deriveLengthsRef.current = deriveLengths
  // What every derivation's result gets: the store's queued call after a
  // route edit (through the sink), and the badge's explicit "Use geometry".
  const onLengthsDerived = useCallback((r: LengthsFromGeometryResult) => {
    const project = useUIStore.getState().currentProject
    qc.invalidateQueries({ queryKey: nk(project, 'lines') })
    qc.invalidateQueries({ queryKey: nk(project, 'links') })
    useMapLayoutStore.getState().applyLengthSources(project, r.sources)
    if (r.updated) {
      appLog('INFO', `${r.updated} branch length${r.updated === 1 ? '' : 's'} derived from map geometry (${
        Object.values(r.sources).filter(s => s === 'route').length} routed).`)
    }
    ingestRescale(qc, r.rescale)
  }, [qc])
  useEffect(() => {
    setLengthsDerivedSink((_project, r) => onLengthsDerived(r))
    return () => setLengthsDerivedSink(null)
  }, [onLengthsDerived])
  const useGeometryMut = useMutation({
    mutationFn: (keys: string[]) => networkApi.lengthsFromGeometry(keys),
    onSuccess: onLengthsDerived,
    onError: () => toast.error('Could not derive the length from the map geometry'),
  })
  // Read currentProject FRESH from the store — a project switch since the
  // last render would otherwise write under the previous project's key.
  // With the setting on, a Line/Link route edit also queues the branch for a
  // length rewrite that the store issues once the route's PUT has landed
  // (the server measures the route it holds, not this cache). Transformers
  // have a route but no length.
  const updateWaypoints = useCallback((edgeId: string, wps: LatLngTuple[]) => {
    const project = useUIStore.getState().currentProject
    setRouteWaypoints(project, edgeId, wps)
    if (deriveLengthsRef.current && project && !edgeId.startsWith('tr:')) queueLengthFromGeometry(project, edgeId)
  }, [setRouteWaypoints])
  const updateBubble = useCallback((key: string, offset: MapBubble) => {
    setBubble(useUIStore.getState().currentProject, key, offset)
  }, [setBubble])

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

        {/* Site boundaries (WP2). `bubblingMouseEvents={false}` so a click on a
            polygon never reaches the map's own click consumers. */}
        {sitesDoc.sites.map(site => (
          <Polygon
            key={site.id}
            positions={site.boundary.map(([lng, lat]) => [lat, lng] as [number, number])}
            bubblingMouseEvents={false}
            pathOptions={{ color: '#b3261e', weight: 2, fillColor: '#b3261e', fillOpacity: 0.08, dashArray: undefined }}
            eventHandlers={{
              // While drawing, a click over an existing site is still a
              // corner (overlapping and nested sites are legitimate); the
              // polygon swallows the event, so forward it.
              click: (e) => {
                if (draw.drawing) { draw.addVertex(e.latlng.lng, e.latlng.lat); return }
                setSitePopover({ id: site.id, x: e.containerPoint.x, y: e.containerPoint.y })
              },
              dblclick: (e) => {
                if (!draw.drawing) return
                const r = draw.closeByDoubleClick(e.latlng.lng, e.latlng.lat)
                if (r.status === 'closed') setDraftBoundary(r.boundary)
                else toast.error('A site needs at least three corners')
              },
            }}
          >
            <Tooltip sticky>{site.name} · {site.buses.length} bus{site.buses.length === 1 ? '' : 'es'}</Tooltip>
          </Polygon>
        ))}
        {draw.drawing && placementUiAllowed && (
          <SiteDrawLayer
            onVertex={(lng, lat) => draw.addVertex(lng, lat)}
            onDoubleClick={(lng, lat) => {
              const r = draw.closeByDoubleClick(lng, lat)
              if (r.status === 'closed') setDraftBoundary(r.boundary)
              else toast.error('A site needs at least three corners')
            }}
          />
        )}
        {draw.drawing && draw.draft.length > 0 && (
          <Polyline
            positions={draw.draft.map(([lng, lat]) => [lat, lng] as [number, number])}
            pathOptions={{ color: '#b3261e', weight: 2, dashArray: '6 6' }}
          />
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
          const wps = lineWaypoints[edgeId] ?? []
          const geometryKm = routeLengthKm([c0, ...wps, c1])
          return (
            <EditableLine
              key={edgeId}
              id={edgeId}
              source={c0}
              target={c1}
              waypoints={wps}
              onUpdate={(next) => updateWaypoints(edgeId, next)}
              onSelect={() => setSelectedComponent({ type: 'Line', name: line.name })}
              color={flow ? loadingColor(flow.loadingPct) : lineColor(v)}
              weight={flow ? 4 : 3}
              tooltip={flow
                ? `${line.name} · ${fmtMW(flow.p0)} · ${flow.loadingPct.toFixed(0)}% of ${flow.sNom.toFixed(0)} MVA · ${fmtKm(line.length)}`
                : `${line.name} · ${line.s_nom?.toFixed(0) ?? '—'} MVA · ${fmtKm(line.length)}`}
              permanentLabel={flow
                ? `${fmtMW(Math.abs(flow.p0))} (${flow.loadingPct.toFixed(0)}%)`
                : undefined}
              lengthBadge={lengthDisagrees(line.length, geometryKm) ? {
                stored: line.length, geometry: geometryKm,
                onUseGeometry: readOnly ? undefined : () => useGeometryMut.mutate([edgeId]),
              } : undefined}
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
          const wps = lineWaypoints[edgeId] ?? []
          const geometryKm = routeLengthKm([c0, ...wps, c1])
          // A link's length shows like a line's (plan M2); PyPSA's default 0
          // reads as "not measured" rather than "0 m".
          const linkLength = link.length > 0 ? ` · ${fmtKm(link.length)}` : ''
          return (
            <EditableLine
              key={edgeId}
              id={edgeId}
              source={c0}
              target={c1}
              waypoints={wps}
              onUpdate={(next) => updateWaypoints(edgeId, next)}
              onSelect={() => setSelectedComponent({ type: 'Link', name: link.name })}
              color={linkFlow ? loadingColor(linkFlow.loadingPct) : '#a16207'}
              weight={linkFlow ? 3 : 2}
              dashArray="6 4"
              tooltip={linkFlow
                ? `${link.name} · ${link.carrier || 'Link'} · ${fmtMW(linkFlow.p0)} · ${linkFlow.loadingPct.toFixed(0)}% of ${linkFlow.pNom.toFixed(0)} MW${linkLength}`
                : `${link.name} · ${link.carrier || 'Link'} · ${link.p_nom?.toFixed(0) ?? '—'} MW${linkLength}`}
              permanentLabel={linkFlow
                ? `${fmtMW(Math.abs(linkFlow.p0))} (${linkFlow.loadingPct.toFixed(0)}%)`
                : undefined}
              lengthBadge={link.length > 0 && lengthDisagrees(link.length, geometryKm) ? {
                stored: link.length, geometry: geometryKm,
                onUseGeometry: readOnly ? undefined : () => useGeometryMut.mutate([edgeId]),
              } : undefined}
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
          onOffsetChange={updateBubble}
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
              icon={busDivIcon(colour, bus.name)}
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
      {placementUiAllowed && !draw.drawing && (
        <UnplacedBusesPanel
          unplacedCount={unplaced.length}
          totalCount={(buses as Bus[]).length}
          placing={placing}
          onStartPlacing={() => setPlacing(true)}
        />
      )}

      {/* New site (WP2). Same visibility rule as the unplaced-buses panel;
          hidden while placing, since placement owns the map's clicks. */}
      {newSiteButtonVisible({ activeSlidePanel, paletteMode, placing, drawing: draw.drawing, draftOpen: !!(draftBoundary || editSite) }) && (
        <button
          type="button"
          onClick={() => { if (!draw.start()) toast.error(readOnly ? 'Project is read-only' : 'Finish placing buses first') }}
          disabled={readOnly}
          title={readOnly ? 'Project is read-only' : 'Draw a site boundary: click corners, double-click or Enter to close'}
          className="absolute z-[900] flex items-center gap-1.5 px-2.5 py-1.5 bg-bg border border-border rounded-md shadow text-[11px] font-medium text-text hover:bg-accent/5 disabled:opacity-50"
          // Right of Leaflet's zoom control (top-left, ~34 px wide), not on top of it.
          style={{ top: 12, left: 54 }}
        >
          + New site
        </button>
      )}
      {draw.drawing && placementUiAllowed && (
        <div
          className="absolute z-[900] left-1/2 -translate-x-1/2 flex items-center gap-3 px-3 py-2 bg-bg border border-border rounded-lg shadow-lg text-xs"
          style={{ bottom: 24 }}
        >
          <span className="text-text">
            Drawing a site — click the corners
            <span className="text-muted"> ({draw.draft.length} so far) · double-click or Enter to close · Esc to cancel</span>
          </span>
          <button type="button" onClick={() => draw.cancel()} className="px-2 py-0.5 border border-border rounded text-muted hover:text-text">Cancel</button>
        </div>
      )}
      {placementUiAllowed && (draftBoundary || editSite) && (
        <Suspense fallback={null}>
          <SiteDraftPanel
            boundary={editSite ? editSite.boundary : draftBoundary!}
            existing={editSite}
            buses={buses as Bus[]}
            onDone={() => { setDraftBoundary(null); setEditSite(null) }}
          />
        </Suspense>
      )}
      {sitePopover && (() => {
        const site = sitesDoc.sites.find(x => x.id === sitePopover.id)
        if (!site) return null
        return (
          <div
            role="menu"
            aria-label={`Site ${site.name}`}
            className="absolute z-[900] flex flex-col min-w-[10rem] rounded-md border border-border bg-bg shadow-lg text-[12px] overflow-hidden"
            style={{ left: sitePopover.x + 8, top: sitePopover.y + 8 }}
          >
            <div className="px-2.5 py-1.5 border-b border-border font-semibold text-text truncate">{site.name}</div>
            <button type="button" role="menuitem" onClick={() => openIn3D(site)} className="text-left px-2.5 py-1.5 hover:bg-accent/5 text-text">Open in 3D</button>
            <button type="button" role="menuitem" disabled={readOnly} onClick={() => { setSitePopover(null); setEditSite(site) }} className="text-left px-2.5 py-1.5 hover:bg-accent/5 text-text disabled:opacity-50">Edit buses…</button>
            <button type="button" role="menuitem" disabled={readOnly} onClick={() => deleteSite(site)} className="text-left px-2.5 py-1.5 hover:bg-accent/5 text-accent disabled:opacity-50">Delete site</button>
            <button type="button" role="menuitem" onClick={() => setSitePopover(null)} className="text-left px-2.5 py-1.5 hover:bg-accent/5 text-muted border-t border-border">Close</button>
          </div>
        )
      })()}

      {/* The rescale consent dialog used to render here. It's now a single
          app-wide instance (RescaleDialogHost, mounted once in App.tsx) so
          previews from PropertiesPanel / TopologyCanvas aren't silently
          dropped — see store/rescaleStore.ts. The `placing` → `placementActive`
          sync effect above this component's `placing` state declaration is
          how that shared instance still knows not to open mid-click. */}

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
            <button
              onClick={() => {
                useUIStore.getState().requestAssetDetail({ componentClass: 'Bus', name: ctxMenu.busName })
                setCtxMenu(null)
              }}
              className="flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left hover:bg-border/30 transition-colors text-text"
            >
              <ExternalLink size={12} />
              View results
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
