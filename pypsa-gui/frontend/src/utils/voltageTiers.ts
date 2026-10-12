// The map's voltage palette, one table for the lines, the bus rings and the
// legend (plan M5). Mirrors the schematic's tiers so the two canvases read
// as one network; the schematic keeps its own (slightly lighter) swatches in
// TopologyCanvas and is not changed here.

export interface VoltageTier { label: string; min: number; color: string }

/** Highest tier first; a line takes the first tier whose `min` it exceeds. */
export const VOLTAGE_TIERS: readonly VoltageTier[] = [
  { label: '> 300 kV', min: 300, color: '#dc2626' },
  { label: '200–300 kV', min: 200, color: '#16a34a' },
  { label: '100–200 kV', min: 100, color: '#2563eb' },
  { label: '< 100 kV', min: -Infinity, color: '#374151' },
]

export function lineColor(vNom: number): string {
  for (const t of VOLTAGE_TIERS) if (vNom > t.min) return t.color
  return VOLTAGE_TIERS[VOLTAGE_TIERS.length - 1].color
}
