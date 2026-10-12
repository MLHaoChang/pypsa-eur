import { useEffect, useState } from 'react'
import { MapContainer, Rectangle, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'

// The map's minimap (plan M5): a second, small Leaflet map on the same
// imagery, four zoom levels out, with a rectangle for the main map's view.
// It follows the main map (move/zoom), and a click on it recentres the
// main map there. The caller applies the schematic's fold rule (hidden
// under MINIMAP_MIN_CANVAS_PX); this component only draws.

export const MINIMAP_MIN_CANVAS_PX = 640
const ZOOM_OUT = 4

interface Props {
  /** The main map, once mounted. */
  map: L.Map
  tileUrl: string
}

function Follow({ map, onBounds }: { map: L.Map; onBounds: (b: L.LatLngBounds) => void }) {
  const mini = useMap()
  useEffect(() => {
    const sync = () => {
      mini.setView(map.getCenter(), Math.max(0, map.getZoom() - ZOOM_OUT), { animate: false })
      onBounds(map.getBounds())
    }
    sync()
    map.on('move zoom', sync)
    return () => { map.off('move zoom', sync) }
  }, [map, mini, onBounds])
  useEffect(() => {
    const recentre = (e: L.LeafletMouseEvent) => map.setView(e.latlng, map.getZoom())
    mini.on('click', recentre)
    return () => { mini.off('click', recentre) }
  }, [map, mini])
  return null
}

export default function MapMiniMap({ map, tileUrl }: Props) {
  const [bounds, setBounds] = useState<L.LatLngBounds>(() => map.getBounds())
  return (
    <div
      className="w-[160px] h-[110px] rounded-[10px] overflow-hidden border border-border shadow-md"
      data-testid="map-minimap"
      title="Overview — click to recentre"
    >
      <MapContainer
        center={map.getCenter()}
        zoom={Math.max(0, map.getZoom() - ZOOM_OUT)}
        zoomControl={false}
        attributionControl={false}
        dragging={false}
        scrollWheelZoom={false}
        doubleClickZoom={false}
        touchZoom={false}
        boxZoom={false}
        keyboard={false}
        style={{ height: '100%', width: '100%' }}
      >
        <TileLayer url={tileUrl} maxZoom={19} />
        <Rectangle bounds={bounds} pathOptions={{ color: '#2f81f7', weight: 1.5, fillOpacity: 0.08 }} interactive={false} />
        <Follow map={map} onBounds={setBounds} />
      </MapContainer>
    </div>
  )
}
