import { MapPin } from 'lucide-react'

// The map's answer to "why is this blank?". Presentational on purpose: it
// imports no Leaflet and no store, so it can be mounted in jsdom and the copy
// the user reads is actually under test. The caller derives the counts with
// `unplacedBusNames` from utils/geo.
//
// It replaces a `toast()` that fired once per mount and vanished after 4.5
// seconds (D5). A network whose buses have no coordinates is a persistent
// condition and deserves persistent UI.
interface UnplacedBusesPanelProps {
  unplacedCount: number
  totalCount: number
  /** True while click-to-place is running; the panel hides so it can't cover the map. */
  placing: boolean
  onStartPlacing: () => void
}

export default function UnplacedBusesPanel({
  unplacedCount, totalCount, placing, onStartPlacing,
}: UnplacedBusesPanelProps) {
  if (unplacedCount === 0 || placing) return null

  // Some buses are placed — the map already shows a network, so a full-size
  // panel over it would be in the way. A count in the toolbar band is enough.
  if (unplacedCount < totalCount) {
    return (
      <button
        type="button"
        onClick={onStartPlacing}
        title="Place the remaining buses by clicking the map"
        className="absolute z-[500] flex items-center gap-1.5 px-2.5 py-1.5 bg-bg border border-border
                   rounded-md shadow text-[11px] font-medium text-text hover:text-accent
                   hover:border-accent transition-colors"
        style={{ top: 128, left: 10 }}
      >
        <MapPin size={12} />
        {unplacedCount} of {totalCount} buses unplaced
      </button>
    )
  }

  // Nothing is placed: the map is showing its default view and no network at
  // all. State the cause plainly — the coordinates, not the basemap.
  return (
    <div
      role="status"
      className="absolute z-[500] left-1/2 -translate-x-1/2 max-w-md w-[min(28rem,calc(100%-2rem))]
                 bg-bg border border-border rounded-lg shadow-lg p-4 text-center"
      style={{ top: 96 }}
    >
      <div className="flex items-center justify-center gap-2 text-sm font-semibold text-text">
        <MapPin size={14} className="text-accent" />
        No bus has a location yet
      </div>
      <p className="mt-2 text-xs text-muted leading-relaxed">
        Satellite and Hybrid plot buses by longitude (x) and latitude (y).
        All {totalCount} buses are still at PyPSA&rsquo;s default 0, 0.
      </p>
      <button
        type="button"
        onClick={onStartPlacing}
        className="mt-3 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium
                   hover:opacity-90 transition-opacity"
      >
        Place buses on the map
      </button>
      <p className="mt-2 text-[10px] text-muted">
        or open a bus and paste a <span className="font-mono">lat, lng</span> pair into its properties
      </p>
    </div>
  )
}
