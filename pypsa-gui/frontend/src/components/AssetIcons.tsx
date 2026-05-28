// Shared pictograms for assets that don't have a clean lucide-react match.
// Co-locating them here keeps the left sidebar, the Connected-Assets buttons
// in the right panel, and the carrier badges on the diagram from drifting.

import type { CSSProperties } from 'react'

interface IconProps { size?: number; style?: CSSProperties }

// Hydrogen — text-based "H₂" glyph. Used wherever an H₂ asset is represented.
export const H2Icon = ({ size = 15, style }: IconProps) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill="none" style={style}>
    <text x="1" y="15" fontSize="11" fontWeight="700" fill="currentColor" fontFamily="monospace">H₂</text>
  </svg>
)
