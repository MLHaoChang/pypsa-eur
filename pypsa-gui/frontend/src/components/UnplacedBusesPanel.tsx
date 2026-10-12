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
  /**
   * "Place all buses around this point" (plan M4): one click lays the whole
   * campus out — the first unplaced bus (a template's grid connection) at
   * the click, the rest 200 m apart around it. Absent = not offered.
   */
  onStartPlacingAll?: () => void
}

export default function UnplacedBusesPanel({
  unplacedCount, totalCount, placing, onStartPlacing, onStartPlacingAll,
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
        className="absolute z-[900] flex items-center gap-1.5 px-2.5 py-1.5 bg-bg border border-border
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
  // z-[900] ensures we sit above Leaflet's control/tooltip panes (z-800).
  return (
    <div
      role="status"
      className="absolute z-[900] left-1/2 -translate-x-1/2 max-w-md w-[min(28rem,calc(100%-2rem))]
                 bg-bg border border-border rounded-lg shadow-lg p-4 text-center"
      style={{ top: 96 }}
    >
      <div className="flex items-center justify-center gap-2 text-sm font-semibold text-text">
        <MapPin size={14} className="text-accent" />
        No bus has a location yet
      </div>
      <p className="mt-2 text-xs text-muted leading-relaxed">
        Satellite and Hybrid plot buses by longitude (x) and latitude (y).
        All {totalCount} buses have no usable location yet — most commonly
        PyPSA&rsquo;s default 0, 0, but also missing or out-of-range values.
      </p>
      {onStartPlacingAll && (
        <p className="mt-2 text-xs font-medium text-text">
          Place your campus: pick the grid connection on the map
        </p>
      )}
      <div className="mt-3 flex items-center justify-center gap-2 flex-wrap">
        {onStartPlacingAll && (
          <button
            type="button"
            onClick={onStartPlacingAll}
            title="One click: the first bus lands there, the others 200 m apart around it. Drag them afterwards."
            className="px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 transition-opacity"
          >
            Place all buses around this point
          </button>
        )}
        <button
          type="button"
          onClick={onStartPlacing}
          className={`px-3 py-1.5 rounded-md text-xs font-medium transition-opacity ${
            onStartPlacingAll ? 'border border-border text-text hover:bg-border/30' : 'bg-accent text-white hover:opacity-90'}`}
        >
          {onStartPlacingAll ? 'Place buses one by one' : 'Place buses on the map'}
        </button>
      </div>
      <p className="mt-2 text-[10px] text-muted">
        or open a bus and paste a <span className="font-mono">lat, lng</span> pair into its properties
      </p>
    </div>
  )
}
