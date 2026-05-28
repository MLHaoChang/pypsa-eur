import { useState } from 'react'
import {
  ChevronDown, ChevronRight, MousePointer,
  Flame, Wind, Zap, BatteryCharging, Droplets, Activity,
  ZoomIn, ZoomOut,
} from 'lucide-react'
import { useUIStore } from '../store/uiStore'

// ── Inline SVG pictograms ─────────────────────────────────────────────────────

const BusIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
    <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="10" cy="10" r="2.5" fill="currentColor" />
  </svg>
)

const LineIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <line x1="2" y1="10" x2="18" y2="10" />
    <line x1="6" y1="7" x2="6" y2="13" />
    <line x1="10" y1="7" x2="10" y2="13" />
    <line x1="14" y1="7" x2="14" y2="13" />
  </svg>
)

const ConnectIcon = (_props: { size?: number }) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <circle cx="3" cy="8" r="2.5" />
    <circle cx="13" cy="8" r="2.5" />
    <line x1="5.5" y1="8" x2="10.5" y2="8" />
  </svg>
)

const H2Icon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
    <text x="1" y="15" fontSize="11" fontWeight="700" fill="currentColor" fontFamily="monospace">H₂</text>
  </svg>
)

const LoadIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <rect x="4" y="3" width="12" height="14" rx="2" />
    <line x1="10" y1="7" x2="10" y2="13" />
    <polyline points="7,11 10,14 13,11" />
  </svg>
)

const FlywheelIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="10" cy="10" r="7" />
    <circle cx="10" cy="10" r="2.5" />
    <path d="M10 3 Q14 6 17 10" strokeLinecap="round" />
  </svg>
)

const CAESIcon = () => (
  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M4 12 Q7 8 10 12 Q13 16 16 12" />
    <path d="M4 8 Q7 4 10 8 Q13 12 16 8" />
  </svg>
)

// ── Asset type definitions ─────────────────────────────────────────────────────

interface PaletteItem {
  id: string
  label: string
  subtitle: string
  icon: React.ReactNode
}

interface PaletteSection {
  id: string
  label: string
  items: PaletteItem[]
}

const SECTIONS: PaletteSection[] = [
  {
    id: 'network', label: 'NETWORK',
    items: [
      { id: 'bus',  label: 'Bus (Node)',         subtitle: 'Voltage node',    icon: <BusIcon /> },
      { id: 'line', label: 'Transmission Line',  subtitle: 'AC line / branch', icon: <LineIcon /> },
    ],
  },
  {
    id: 'generation', label: 'GENERATION',
    items: [
      { id: 'thermal',   label: 'Thermal',   subtitle: 'Coal, CCGT, oil…',  icon: <Flame size={18} /> },
      { id: 'renewable', label: 'Renewable', subtitle: 'Wind, solar, hydro', icon: <Wind size={18} /> },
      { id: 'generator', label: 'Generator', subtitle: 'Attach to bus',      icon: <Zap size={18} /> },
    ],
  },
  {
    id: 'storage', label: 'STORAGE',
    items: [
      { id: 'battery',  label: 'Battery',         subtitle: 'Li-Ion / BESS',      icon: <BatteryCharging size={18} /> },
      { id: 'psh',      label: 'Pumped Hydro',    subtitle: 'Large-scale PSH',    icon: <Droplets size={18} /> },
      { id: 'hydrogen', label: 'Hydrogen',         subtitle: 'P2G / Electrolysis', icon: <H2Icon /> },
      { id: 'caes',     label: 'Compressed Air',  subtitle: 'CAES',               icon: <CAESIcon /> },
      { id: 'flywheel', label: 'Flywheel',         subtitle: 'Short-duration',     icon: <FlywheelIcon /> },
    ],
  },
  {
    id: 'consumption', label: 'CONSUMPTION',
    items: [
      { id: 'load', label: 'Lumped Load', subtitle: 'Constant PQ load', icon: <LoadIcon /> },
    ],
  },
]

// ── Component ─────────────────────────────────────────────────────────────────

export default function AssetPalette() {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const { canvasMode, setCanvasMode, setCreationItem } = useUIStore()
  // Manual pointer-event drag state. We bypass HTML5 drag-and-drop entirely
  // because <button draggable> and even <div draggable> initiate dragstart
  // unreliably on some browser/OS combinations — the user reported drops not
  // landing at all. Pointer events are bulletproof: mousedown→trackmove→up
  // works the same everywhere, and we control the visual feedback (ghost)
  // ourselves so the user always sees what's being dragged.
  //
  // Threshold: 3px of movement during pointer-down promotes the gesture
  // from "click" → "drag". Below the threshold, releasing acts as a click
  // (opens the slide-in panel without a drop position). At or above, the
  // pointerup checks the cursor's target — if over the React Flow canvas,
  // we open the panel with the drop coordinates attached.
  const [ghost, setGhost] = useState<{ label: string; x: number; y: number } | null>(null)

  const toggle = (id: string) =>
    setCollapsed(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })

  const row = 'flex items-center gap-2.5 w-full px-3 py-1.5 cursor-grab active:cursor-grabbing hover:bg-accent/8 hover:text-text transition-colors text-left group'

  // Start a pointer-drag for the given palette item. The closure captures
  // the start position; window-level pointermove/pointerup listeners run for
  // the duration of the gesture and unbind themselves on release.
  function beginDrag(e: React.PointerEvent, item: PaletteItem) {
    if (e.button !== 0) return  // left button only
    e.preventDefault()
    const startX = e.clientX
    const startY = e.clientY
    let moved = false

    const onMove = (ev: PointerEvent) => {
      const dx = Math.abs(ev.clientX - startX)
      const dy = Math.abs(ev.clientY - startY)
      if (!moved && (dx > 3 || dy > 3)) {
        moved = true
        document.body.style.cursor = 'grabbing'
      }
      if (moved) setGhost({ label: item.label, x: ev.clientX, y: ev.clientY })
    }

    const onUp = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      document.body.style.cursor = ''
      setGhost(null)

      // No movement ⇒ treat as a click. Open the slide-in panel without a
      // drop position (existing click-to-add behaviour).
      if (!moved) {
        setCreationItem({ id: item.id, label: item.label })
        return
      }

      // Find the drop target. We look for the React Flow canvas (.react-flow
      // class is rendered by the library on its wrapping div) under the
      // pointer at release time. document.elementFromPoint walks the actual
      // DOM hit-test, so this works through any z-index stacking.
      const target = document.elementFromPoint(ev.clientX, ev.clientY)
      const rfEl = target?.closest('.react-flow') as HTMLElement | null
      if (!rfEl) return  // dropped outside the canvas — cancel silently

      // Convert screen → React Flow coords via the global rfInstance handle
      // that TopologyCanvas sets in onInit. Falls back to null-safe so a
      // missing instance (canvas hasn't initialised yet) cancels the drop
      // rather than crashing.
      const rf = (window as unknown as {
        rfInstance?: { screenToFlowPosition: (p: { x: number; y: number }) => { x: number; y: number } }
      }).rfInstance
      const pos = rf?.screenToFlowPosition({ x: ev.clientX, y: ev.clientY })
      if (!pos) return

      setCreationItem({ id: item.id, label: item.label, dropPosition: pos })
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <aside className="w-[220px] shrink-0 flex flex-col bg-panel border-r border-border overflow-y-auto select-none">
      {SECTIONS.map(section => (
        <div key={section.id}>
          <button
            onClick={() => toggle(section.id)}
            className="flex items-center gap-1.5 w-full px-3 py-1.5 hover:bg-border/30 transition-colors"
          >
            {collapsed.has(section.id)
              ? <ChevronRight size={10} className="text-muted shrink-0" />
              : <ChevronDown  size={10} className="text-muted shrink-0" />}
            <span className="text-[10px] font-bold text-muted uppercase tracking-widest">{section.label}</span>
          </button>

          {!collapsed.has(section.id) && section.items.map(item => (
            // Pointer-event drag (not HTML5 draggable). onPointerDown starts
            // tracking; if the user moves > 3px the gesture promotes to a
            // drag with a ghost element following the cursor. Release over
            // the canvas → drop; release with no movement → click.
            <div
              key={item.id}
              role="button"
              tabIndex={0}
              className={row}
              onPointerDown={(e) => beginDrag(e, item)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setCreationItem({ id: item.id, label: item.label })
                }
              }}
              title="Click to add, or drag onto the canvas to place at a specific point"
            >
              <span className="text-muted group-hover:text-accent transition-colors shrink-0"
                style={{ width: 20, height: 20, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {item.icon}
              </span>
              <div className="min-w-0">
                <div className="text-[11px] font-medium text-text truncate">{item.label}</div>
                <div className="text-[9px] text-muted truncate leading-tight">{item.subtitle}</div>
              </div>
            </div>
          ))}
        </div>
      ))}

      <div className="flex-1" />

      {/* Mode controls */}
      <div className="border-t border-border p-2 shrink-0">
        <p className="text-[9px] font-bold text-muted uppercase tracking-widest mb-1.5 px-1">MODE</p>
        {([
          { id: 'select',  label: 'Select / Pan',    Icon: MousePointer },
          { id: 'connect', label: 'Connect Buses',   Icon: ConnectIcon  },
        ] as const).map(({ id, label, Icon }) => (
          <button
            key={id}
            onClick={() => setCanvasMode(id)}
            className={`flex items-center gap-2 w-full px-2 py-1.5 rounded text-[11px] mb-0.5 transition-colors
              ${canvasMode === id
                ? 'bg-accent/10 text-accent font-medium'
                : 'text-muted hover:text-text hover:bg-border/30'}`}
          >
            <Icon size={13} />
            {label}
          </button>
        ))}

        <div className="border-t border-border mt-2 pt-2 flex gap-1">
          <button
            className="flex-1 flex items-center justify-center gap-1 py-1 text-[10px] text-muted border border-border rounded hover:text-text hover:border-text/40 transition-colors"
            onClick={() => (window as unknown as { rfInstance?: { zoomIn: () => void } }).rfInstance?.zoomIn?.()}
          >
            <ZoomIn size={11} /> In
          </button>
          <button
            className="flex-1 flex items-center justify-center gap-1 py-1 text-[10px] text-muted border border-border rounded hover:text-text hover:border-text/40 transition-colors"
            onClick={() => (window as unknown as { rfInstance?: { zoomOut: () => void } }).rfInstance?.zoomOut?.()}
          >
            <ZoomOut size={11} /> Out
          </button>
        </div>
      </div>

      {/* Ghost element — follows the cursor while a drag is in flight so the
          user can see what they're dragging. Rendered into the body via
          fixed positioning so it doesn't get clipped by the aside's overflow.
          pointer-events-none lets pointerup pass through to the actual drop
          target underneath. */}
      {ghost && (
        <div
          className="fixed z-[9999] pointer-events-none flex items-center gap-1.5 px-2 py-1 bg-accent text-white text-[11px] font-medium rounded shadow-lg"
          style={{ left: ghost.x + 12, top: ghost.y + 12 }}
        >
          {ghost.label}
        </div>
      )}
    </aside>
  )
}
