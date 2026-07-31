import React from 'react'
import {
  Atom, BatteryCharging, Droplets, Factory, Flame, Fuel, Leaf, Mountain,
  PlugZap, Sun, Thermometer, Waves, Wind, Zap,
} from 'lucide-react'
import { H2Icon } from '../components/AssetIcons'

// Carrier → pictogram, shared by the schematic canvas and the map.
//
// It lived inside TopologyCanvas.tsx, which meant the map could only get it by
// copying — the same copy-and-drift that utils/carriers.ts exists to prevent.
// The map was not copying it at all: it hard-coded ONE icon per category, so
// every renewable group rendered as a wind turbine and a real project's solar
// plant was drawn as wind.
//
// `solar` was also simply absent, so even the original table would have fallen
// through to a generic Zap.
//
// KEY ORDERING RULE: When one key is a substring of another AND they have
// different badge labels, the longer key MUST come first. This ensures that
// variant inputs (e.g. "biogas-chp", "hvdc-link") match the most specific key
// via the fallback substring scan, not an overly-general one. Example: if "gas"
// came before "biogas", then input "biogas-chp" would match "gas" first and
// return 'Gas' instead of the correct 'Biogas' label, contradicting the emissions
// layer's biogas CO₂ classification and misleading the user about the asset's
// properties. Always put more-specific (longer, more-constrained) keys before
// the short keys they contain.

export type BadgeIcon = React.FC<{ size?: number; style?: React.CSSProperties; strokeWidth?: number }>
export interface BadgeDef { Icon: BadgeIcon; label: string }

export const CARRIER_BADGES: Record<string, BadgeDef> = {
  H2:              { Icon: H2Icon,          label: 'H₂' },
  hydrogen:        { Icon: H2Icon,          label: 'H₂' },
  electrolysis:    { Icon: Droplets,        label: 'ELY' },
  heat_pump:       { Icon: Thermometer,     label: 'HP' },
  'heat pump':     { Icon: Thermometer,     label: 'HP' },
  heat:            { Icon: Thermometer,     label: 'Heat' },
  // Fossil. `gas` covers the fuel-level carrier; CCGT/OCGT are technologies.
  biogas:          { Icon: Leaf,            label: 'Biogas' },
  gas:             { Icon: Flame,           label: 'Gas' },
  CCGT:            { Icon: Flame,           label: 'Gas' },
  OCGT:            { Icon: Flame,           label: 'Gas' },
  coal:            { Icon: Factory,         label: 'Coal' },
  lignite:         { Icon: Factory,         label: 'Coal' },
  oil:             { Icon: Fuel,            label: 'Oil' },
  diesel:          { Icon: Fuel,            label: 'Oil' },
  nuclear:         { Icon: Atom,            label: 'Nuclear' },
  SMR:             { Icon: H2Icon,          label: 'SMR' },
  // Renewables.
  solar:           { Icon: Sun,             label: 'Solar' },
  'solar-rooftop': { Icon: Sun,             label: 'Solar' },
  onwind:          { Icon: Wind,            label: 'Wind' },
  'offwind-ac':    { Icon: Wind,            label: 'Wind' },
  'offwind-dc':    { Icon: Wind,            label: 'Wind' },
  wind:            { Icon: Wind,            label: 'Wind' },
  hydro:           { Icon: Droplets,        label: 'Hydro' },
  ror:             { Icon: Droplets,        label: 'Hydro' },
  PHS:             { Icon: Droplets,        label: 'PHS' },
  biomass:         { Icon: Leaf,            label: 'Biomass' },
  geothermal:      { Icon: Mountain,        label: 'Geo' },
  wave:            { Icon: Waves,           label: 'Wave' },
  tidal:           { Icon: Waves,           label: 'Tidal' },
  // Storage and transport.
  battery:         { Icon: BatteryCharging, label: 'Batt.' },
  BEV:             { Icon: BatteryCharging, label: 'BEV' },
  HVDC:            { Icon: PlugZap,         label: 'HVDC' },
  DC:              { Icon: PlugZap,         label: 'DC' },
  AC:              { Icon: Zap,             label: 'AC' },
  resistive:       { Icon: Zap,             label: 'Res.' },
}

export function getCarrierBadge(carrier: string): BadgeDef {
  if (CARRIER_BADGES[carrier]) return CARRIER_BADGES[carrier]
  const key = Object.keys(CARRIER_BADGES).find(k =>
    carrier.toLowerCase().includes(k.toLowerCase())
  )
  return key ? CARRIER_BADGES[key] : { Icon: Zap, label: carrier.slice(0, 5) }
}

/**
 * The badge shared by every carrier in a group, or null when the group is
 * mixed or empty.
 *
 * Compared by badge, not by carrier string, so `onwind` + `offwind-ac` still
 * resolves to a turbine — the icon is honest and specific. `solar` + `onwind`
 * has no honest single icon, so the caller falls back to the category icon.
 */
export function uniformBadge(carriers: string[]): BadgeDef | null {
  if (carriers.length === 0) return null
  const first = getCarrierBadge(carriers[0])
  return carriers.every(c => {
    const b = getCarrierBadge(c)
    return b.Icon === first.Icon && b.label === first.label
  }) ? first : null
}
