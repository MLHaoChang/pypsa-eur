// The asset palette's icons, keyed by palette item id (the data is in
// paletteData.ts). Split from Sidebar.tsx so that module exports only its
// component (React fast refresh).
import type React from 'react'
import { Flame, Wind, BatteryCharging, Droplets, Thermometer, Zap } from 'lucide-react'
import { H2Icon } from '../components/AssetIcons'

// ── Inline SVG icons ───────────────────────────────────────────────────────────
const BusIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none">
    <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.8" />
    <circle cx="10" cy="10" r="2.5" fill="currentColor" />
  </svg>
)
const LineIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <line x1="2" y1="10" x2="18" y2="10" />
    <line x1="6" y1="7" x2="6" y2="13" />
    <line x1="10" y1="7" x2="10" y2="13" />
    <line x1="14" y1="7" x2="14" y2="13" />
  </svg>
)
const FlywheelIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="10" cy="10" r="7" />
    <circle cx="10" cy="10" r="2.5" />
    <path d="M10 3 Q14 6 17 10" strokeLinecap="round" />
  </svg>
)
const CAESIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M4 12 Q7 8 10 12 Q13 16 16 12" />
    <path d="M4 8 Q7 4 10 8 Q13 12 16 8" />
  </svg>
)
const ElectrolyzerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M2 10 L7 10" />
    <rect x="7" y="5" width="6" height="10" rx="1.5" />
    <path d="M13 10 L18 10" strokeDasharray="2 1.5" />
    <text x="8.5" y="14" fontSize="5" fontWeight="700" fill="currentColor" stroke="none" fontFamily="monospace">H₂</text>
  </svg>
)
const FuelCellIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
    <path d="M2 10 L7 10" strokeDasharray="2 1.5" />
    <rect x="7" y="5" width="6" height="10" rx="1.5" />
    <path d="M13 10 L18 10" />
    <path d="M10 7.5 L10 8.5 M9 8 L11 8" strokeWidth="1.2" />
    <path d="M9 11 L11 13 M11 11 L9 13" strokeWidth="1.2" />
  </svg>
)
// Heat pump / resistive heater: electricity in (left) → heat out (squiggle right).
const PowerToHeatIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <path d="M2 10 L7 10" />
    <rect x="7" y="5" width="6" height="10" rx="1.5" />
    <path d="M14 6 q2 2 0 4 q-2 2 0 4" />
    <path d="M16.5 6 q2 2 0 4 q-2 2 0 4" />
  </svg>
)
// CHP: fuel in → both electricity + heat out.
const CHPIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <path d="M2 10 L6 10" />
    <rect x="6" y="5" width="6" height="10" rx="1.5" />
    <path d="M12 7.5 L17 5.5" />
    <path d="M12 12.5 q2 0.5 3.5 2 q-1 -0.5 -1.5 1.5 q1.5 -1 3 0.5" />
  </svg>
)
// Thermal storage: tank with wave inside.
const ThermalStorageIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
    <rect x="4" y="3" width="12" height="14" rx="2" />
    <path d="M5.5 11 q2 -1.5 4 0 t 4 0 t 1.5 0" />
    <path d="M5.5 8 q2 -1.5 4 0 t 4 0 t 1.5 0" strokeOpacity="0.6" />
  </svg>
)
// Transformer: classic IEC two-interlocking-circles symbol.
const TransformerIcon = () => (
  <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6">
    <circle cx="7" cy="10" r="4" />
    <circle cx="13" cy="10" r="4" />
  </svg>
)

export const PALETTE_ICONS: Record<string, React.ReactNode> = {
  bus: <BusIcon />, line: <LineIcon />, transformer: <TransformerIcon />,
  thermal: <Flame size={14} />, renewable: <Wind size={14} />,
  electrolyzer: <ElectrolyzerIcon />, fuel_cell: <FuelCellIcon />,
  power_to_heat: <PowerToHeatIcon />, chp: <CHPIcon />,
  battery: <BatteryCharging size={14} />, psh: <Droplets size={14} />, caes: <CAESIcon />,
  flywheel: <FlywheelIcon />, hydrogen: <H2Icon />, thermal_storage: <ThermalStorageIcon />,
  load_elec: <Zap size={14} />, load_h2: <H2Icon />, load_heat: <Thermometer size={14} />,
}
