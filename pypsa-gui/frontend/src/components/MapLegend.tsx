import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { VOLTAGE_TIERS } from '../utils/voltageTiers'
import { getCarrierBadge } from '../utils/carrierBadges'
import { getLinkColor } from '../pages/topologyEdges'
import { assetIcon } from '../utils/assetTypeIcon'
import type { LegendEntry } from '../utils/assetTypes'

// The map's legend (plan M5): the voltage tiers the lines and bus rings
// use, the link carriers present, and one row per asset type present —
// the SAME rows the schematic (topologyAssets.assetLegend) and the 3D
// legend draw, from utils/assetTypes' shared table, so the three layers
// agree. Collapsible, remembered per browser like the schematic's.
// Presentational: the map passes what is on it.

const LEGEND_KEY = 'pypsa.mapLegendOpen'
function readOpen(): boolean { try { return localStorage.getItem(LEGEND_KEY) !== 'false' } catch { return true } }
function writeOpen(open: boolean) { try { localStorage.setItem(LEGEND_KEY, String(open)) } catch { /* private mode */ } }

interface Props {
  /** Link carriers present on the map, in first-seen order. */
  linkCarriers: readonly string[]
  /** `assetLegend(buildAssetNodes(...))`: the asset types present. */
  assetRows: readonly LegendEntry[]
}

export default function MapLegend({ linkCarriers, assetRows }: Props) {
  const [open, setOpenState] = useState<boolean>(() => readOpen())
  const setOpen = (v: boolean) => { setOpenState(v); writeOpen(v) }
  const frosted = { background: 'color-mix(in srgb, var(--color-bg) 92%, transparent)', backdropFilter: 'blur(8px)' } as const
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        data-testid="map-legend-toggle"
        className="flex items-center gap-1 border border-border rounded-[10px] shadow-md px-2.5 py-1.5 text-[10px] font-bold text-muted uppercase tracking-[0.14em] hover:text-text"
        style={frosted}
      ><ChevronRight size={10} className="shrink-0" />Legend</button>
    )
  }
  return (
    <div className="border border-border rounded-[10px] shadow-md w-[164px] p-3 max-h-[50vh] overflow-auto" style={frosted} data-testid="map-legend">
      <button
        type="button"
        onClick={() => setOpen(false)}
        aria-label="Hide legend"
        aria-expanded={true}
        data-testid="map-legend-toggle"
        className="flex items-center gap-1 mb-2 -ml-0.5 text-left text-muted hover:text-text"
      >
        <ChevronDown size={10} className="shrink-0" />
        <span className="text-[9px] font-bold uppercase tracking-[0.14em]">Buses &amp; Lines · voltage</span>
      </button>
      {VOLTAGE_TIERS.map(t => (
        <div key={t.label} className="flex items-center gap-2 mb-1" data-testid="legend-voltage">
          <svg width="20" height="3" style={{ flexShrink: 0 }}><line x1="0" y1="1.5" x2="20" y2="1.5" stroke={t.color} strokeWidth="2.5" strokeLinecap="round" /></svg>
          <span className="text-[10.5px] text-ink-700">{t.label}</span>
        </div>
      ))}
      {linkCarriers.length > 0 && (
        <>
          <p className="text-[9px] font-bold text-muted uppercase tracking-[0.14em] mb-2 mt-3">Links · carrier</p>
          {linkCarriers.map((carrier, i) => {
            const { Icon, label } = getCarrierBadge(carrier)
            return (
              <div key={carrier} className="flex items-center gap-1.5 mb-1.5" data-testid="legend-carrier">
                <div style={{ display: 'flex', alignItems: 'center', gap: 2, backgroundColor: getLinkColor(carrier, i), color: '#fff', padding: '1px 5px', borderRadius: 6, fontSize: 8, fontWeight: 700, fontFamily: 'var(--font-family-mono)', border: '1px solid #fff', flexShrink: 0 }}>
                  <Icon size={8} strokeWidth={2.5} />
                  <span style={{ marginLeft: 1 }}>{label}</span>
                </div>
                <span className="text-[9.5px] text-muted truncate max-w-[72px]">{carrier}</span>
              </div>
            )
          })}
        </>
      )}
      <p className="text-[9px] font-bold text-muted uppercase tracking-[0.14em] mb-2 mt-3">Assets · type</p>
      {assetRows.map(row => (
        <div key={row.id} className="flex items-center gap-2 mb-1" data-testid="legend-asset-type">
          <span style={{ color: row.color, display: 'inline-flex', flexShrink: 0 }}>{assetIcon(row.icon)}</span>
          <span className="text-[10.5px] text-ink-700 truncate" title={row.label}>{row.label}</span>
        </div>
      ))}
      {assetRows.length === 0 && <p className="text-[9.5px] text-muted">No assets</p>}
    </div>
  )
}
