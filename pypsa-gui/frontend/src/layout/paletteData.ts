// The asset palette's data: ids, labels, sections, and what each item
// creates (PyPSA class, default carrier, which bus carriers each port
// accepts). The sidebar renders it (adding icons by id), the creation form
// takes its class map from it, and the 3D asset library's matching test
// reads it, so a palette change is seen by all three (Phase 2 plan Task 0.3).
// Pure data: no React, no icons.

export interface PaletteItemData { id: string; label: string; subtitle: string }
export interface PaletteSectionData { id: string; label: string; items: PaletteItemData[] }

export const PALETTE_SECTIONS_DATA: PaletteSectionData[] = [
  {
    id: 'network', label: 'NETWORK',
    items: [
      { id: 'bus',         label: 'Bus (Node)',        subtitle: 'Voltage / carrier node' },
      { id: 'line',        label: 'Transmission Line', subtitle: 'AC line / branch' },
      { id: 'transformer', label: 'Transformer',       subtitle: 'Voltage step (e.g. 380/220)' },
    ],
  },
  {
    id: 'electricity', label: 'ELECTRICITY',
    items: [
      { id: 'thermal',   label: 'Conventional', subtitle: 'Coal, CCGT, oil, gas…' },
      { id: 'renewable', label: 'Renewable',    subtitle: 'Wind, solar, hydro' },
    ],
  },
  {
    id: 'hydrogen', label: 'HYDROGEN',
    items: [
      { id: 'electrolyzer', label: 'Electrolyzer', subtitle: 'Electricity → H₂' },
      { id: 'fuel_cell',    label: 'Fuel Cell',    subtitle: 'H₂ → Electricity' },
    ],
  },
  {
    id: 'heat', label: 'HEAT',
    items: [
      { id: 'power_to_heat', label: 'Power-to-Heat', subtitle: 'Heat pump / resistive heater' },
      { id: 'chp',           label: 'CHP Plant',     subtitle: 'Co-generation (elec + heat)' },
    ],
  },
  {
    id: 'storage', label: 'STORAGE',
    items: [
      { id: 'battery',         label: 'Battery',          subtitle: 'Li-Ion / BESS' },
      { id: 'psh',             label: 'Pumped Hydro',     subtitle: 'Large-scale PSH' },
      { id: 'caes',            label: 'Compressed Air',   subtitle: 'CAES' },
      { id: 'flywheel',        label: 'Flywheel',         subtitle: 'Short-duration' },
      { id: 'hydrogen',        label: 'Hydrogen Storage', subtitle: 'H₂ tank / cavern' },
      { id: 'thermal_storage', label: 'Thermal Storage',  subtitle: 'Hot-water tank / TES' },
    ],
  },
  {
    id: 'demand', label: 'DEMAND',
    items: [
      { id: 'load_elec', label: 'Electrical Demand', subtitle: 'Load on AC/DC bus' },
      { id: 'load_h2',   label: 'Hydrogen Demand',   subtitle: 'Load on H₂ bus' },
      { id: 'load_heat', label: 'Heating Demand',    subtitle: 'Load on heat bus' },
    ],
  },
]

export const PALETTE_ITEM_IDS: string[] = PALETTE_SECTIONS_DATA.flatMap(s => s.items.map(i => i.id))

export type PaletteClass = 'Bus' | 'Line' | 'Transformer' | 'Generator' | 'StorageUnit' | 'Store' | 'Link' | 'Load'

export const PALETTE_COMPONENT_TYPE: Record<string, PaletteClass> = {
  bus: 'Bus', line: 'Line', transformer: 'Transformer',
  thermal: 'Generator', renewable: 'Generator',
  battery: 'StorageUnit', psh: 'StorageUnit', hydrogen: 'StorageUnit',
  caes: 'StorageUnit', flywheel: 'StorageUnit',
  electrolyzer: 'Link', fuel_cell: 'Link',
  power_to_heat: 'Link', chp: 'Link',
  thermal_storage: 'Store',
  load_elec: 'Load', load_h2: 'Load', load_heat: 'Load',
}

/** The bus-picker filters the creation form applies (`BusFieldSpec.busCarrierFilter`). */
export type PortFilter = 'electricity' | 'non-h2' | 'h2' | 'heat' | 'gas'

export interface PaletteDefaults {
  cls: PaletteClass
  /** The creation form's default carrier (undefined when the form has no carrier field). */
  carrier?: string
  /** Per port, the bus carriers the form lets the user pick; undefined = unrestricted. */
  ports?: Partial<Record<'bus' | 'bus0' | 'bus1' | 'bus2', PortFilter>>
}

const DEFAULTS: Record<string, Omit<PaletteDefaults, 'cls'>> = {
  bus:             { carrier: 'AC' },
  line:            { carrier: 'AC' },
  transformer:     { ports: { bus0: 'electricity', bus1: 'electricity' } },
  thermal:         { carrier: 'gas' },
  renewable:       { carrier: 'wind' },
  electrolyzer:    { carrier: 'H2', ports: { bus0: 'non-h2', bus1: 'h2' } },
  fuel_cell:       { carrier: 'H2', ports: { bus0: 'h2', bus1: 'non-h2' } },
  power_to_heat:   { carrier: 'heat-pump-air', ports: { bus0: 'electricity', bus1: 'heat' } },
  chp:             { carrier: 'gas', ports: { bus0: 'gas', bus1: 'electricity', bus2: 'heat' } },
  battery:         { carrier: 'battery' },
  psh:             { carrier: 'hydro' },
  caes:            { carrier: 'air' },
  flywheel:        { carrier: 'flywheel' },
  hydrogen:        { carrier: 'H2' },
  thermal_storage: { carrier: 'heat', ports: { bus: 'heat' } },
  load_elec:       { carrier: 'AC', ports: { bus: 'electricity' } },
  load_h2:         { carrier: 'H2', ports: { bus: 'h2' } },
  load_heat:       { carrier: 'heat', ports: { bus: 'heat' } },
}

export function paletteDefaults(id: string): PaletteDefaults {
  return { cls: PALETTE_COMPONENT_TYPE[id], ...DEFAULTS[id] }
}
