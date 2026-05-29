import { Square, Globe, MapPin } from 'lucide-react'
import { useUIStore, type CanvasView } from '../store/uiStore'

// Three-segment toggle pinned over the canvas top-right. Persisted to
// localStorage via the uiStore setter.
const MODES: Array<{ id: CanvasView; label: string; Icon: typeof Square; tip: string }> = [
  { id: 'blank',     label: 'Blank',     Icon: Square, tip: 'Schematic canvas (full edit)' },
  { id: 'satellite', label: 'Satellite', Icon: Globe,  tip: 'Esri satellite imagery (no labels)' },
  { id: 'hybrid',    label: 'Hybrid',    Icon: MapPin, tip: 'Satellite imagery with street & place labels' },
]

export default function MapModeSwitcher() {
  const { canvasView, setCanvasView, activeSlidePanel, paletteMode } = useUIStore()
  // Hide when any slide-in panel is open — those are z-300 fixed-position
  // overlays and would otherwise visually clip the switcher. Re-shows the
  // moment the panel closes. Leaflet's own panes go up to z-800 (markers,
  // tooltips, controls) so the switcher must sit above that band; z-[900]
  // wins against every leaflet UI element.
  if (activeSlidePanel) return null
  // Same for the command palette (z-[500]). Without this guard the z-[900]
  // switcher would float ON TOP of the palette overlay. Hiding for the
  // duration of the modal is cleaner than reshuffling z-indexes.
  if (paletteMode !== null) return null
  return (
    <div
      className="absolute z-[900] flex gap-0 bg-bg border border-border rounded-md shadow overflow-hidden"
      style={{ top: 12, right: 12 }}
    >
      {MODES.map(({ id, label, Icon, tip }) => {
        const active = canvasView === id
        return (
          <button
            key={id}
            type="button"
            title={tip}
            onClick={() => setCanvasView(id)}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium border-r last:border-r-0 border-border transition-colors
              ${active ? 'bg-accent text-white' : 'bg-bg text-muted hover:text-text hover:bg-accent/5'}`}
          >
            <Icon size={11} />
            {label}
          </button>
        )
      })}
    </div>
  )
}
