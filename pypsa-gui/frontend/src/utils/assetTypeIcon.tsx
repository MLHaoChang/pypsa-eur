// The icon for an asset type (utils/assetTypes.ts names them; this module
// draws them). Palette names come from layout/paletteIcons.tsx so the palette,
// the schematic, the map and the 3D legend show the same glyph for the same
// type; the four names the palette lacks are lucide icons at the palette's
// size. Main-bundle (React, lucide), no three. The record is typed
// exhaustively: a name added to AssetIconName without an entry here fails
// to compile.
import type React from 'react'
import { Box, Factory, Sun, Waypoints } from 'lucide-react'
import { PALETTE_ICONS } from '../layout/paletteIcons'
import { iconNameOf, type AssetIconName } from './assetTypes'

const palette = (id: AssetIconName): React.ReactNode => PALETTE_ICONS[id]

export const ASSET_TYPE_ICONS: Record<AssetIconName, React.ReactNode> = {
  bus: palette('bus'), line: palette('line'), transformer: palette('transformer'),
  thermal: palette('thermal'), renewable: palette('renewable'),
  electrolyzer: palette('electrolyzer'), fuel_cell: palette('fuel_cell'), power_to_heat: palette('power_to_heat'), chp: palette('chp'),
  battery: palette('battery'), psh: palette('psh'), caes: palette('caes'), flywheel: palette('flywheel'),
  hydrogen: palette('hydrogen'), thermal_storage: palette('thermal_storage'),
  load_elec: palette('load_elec'), load_h2: palette('load_h2'), load_heat: palette('load_heat'),
  sun: <Sun size={14} />, box: <Box size={14} />, factory: <Factory size={14} />, waypoints: <Waypoints size={14} />,
}

/** The icon node for an icon name. */
export const assetIcon = (name: AssetIconName): React.ReactNode => ASSET_TYPE_ICONS[name]

/** The icon node for an asset type id; null for an id the taxonomy does not know. */
export function iconFor(typeId: string): React.ReactNode {
  const name = iconNameOf(typeId)
  return name ? ASSET_TYPE_ICONS[name] : null
}
