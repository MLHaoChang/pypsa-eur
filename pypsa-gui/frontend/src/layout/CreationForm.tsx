import { useState, useMemo } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { X, AlertTriangle } from 'lucide-react'
import { networkApi } from '../api/network'
import { useUIStore, type CreationRequest } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import BusAutocomplete from '../components/BusAutocomplete'
import toast from 'react-hot-toast'
import type { Bus, Line, Link, Generator, Load, StorageUnit, Store, Transformer } from '../api/types'

// Carrier classification used by the bus-picker filters. Lower-case match.
const H2_CARRIERS   = new Set(['h2', 'hydrogen', 'h2 pipeline', 'h2_pipeline'])
const HEAT_CARRIERS = new Set(['heat', 'heat-low', 'heat-high', 'urban heat', 'rural heat',
  'urban central heat', 'urban decentral heat', 'rural heat'])
const GAS_CARRIERS  = new Set(['gas', 'natural gas', 'biomass', 'biogas', 'oil', 'fuel'])
const ELEC_CARRIERS = new Set(['ac', 'dc', 'electricity'])

type BusCarrier = 'h2' | 'non-h2' | 'electricity' | 'heat' | 'gas'

function carrierMatches(busCarrier: string, want: BusCarrier): boolean {
  const c = (busCarrier ?? '').toLowerCase()
  switch (want) {
    case 'h2':           return H2_CARRIERS.has(c)
    case 'non-h2':       return !H2_CARRIERS.has(c)
    case 'electricity':  return ELEC_CARRIERS.has(c) || c === '' // unset carrier defaults to AC
    case 'heat':         return HEAT_CARRIERS.has(c) || c.includes('heat')
    case 'gas':          return GAS_CARRIERS.has(c) || c.includes('gas') || c.includes('biomass')
  }
}

// ── Field definitions ─────────────────────────────────────────────────────────

interface FieldSpec {
  key: string
  label: string
  type: 'text' | 'number' | 'checkbox' | 'bus' | 'select'
  defaultValue?: string
  options?: string[]
  unit?: string
  required?: boolean
  half?: boolean
}

const genFields = (carrier: string): FieldSpec[] => [
  { key: 'name',             label: 'Name',          type: 'text',     required: true },
  { key: 'bus',              label: 'Attach to Bus',  type: 'bus',      required: true },
  { key: 'carrier',          label: 'Carrier',        type: 'text',     defaultValue: carrier },
  { key: 'p_nom',            label: 'P nom',          type: 'number',   defaultValue: '0',    unit: 'MW',     half: true },
  { key: 'p_nom_extendable', label: 'Extendable',     type: 'checkbox', defaultValue: 'false',               half: true },
  { key: 'marginal_cost',    label: 'Marginal cost',  type: 'number',   defaultValue: '0',    unit: '$/MWh',  half: true },
  { key: 'capital_cost',     label: 'Capital cost',   type: 'number',   defaultValue: '0',    unit: '$/MW',   half: true },
]

const storFields = (carrier: string, hours = '4'): FieldSpec[] => [
  { key: 'name',                label: 'Name',           type: 'text',   required: true },
  { key: 'bus',                 label: 'Attach to Bus',   type: 'bus',    required: true },
  { key: 'carrier',             label: 'Carrier',         type: 'text',   defaultValue: carrier },
  { key: 'p_nom',               label: 'P nom',           type: 'number', defaultValue: '0',    unit: 'MW',    half: true },
  { key: 'max_hours',           label: 'Max hours',       type: 'number', defaultValue: hours,  unit: 'h',     half: true },
  { key: 'efficiency_store',    label: 'η store',         type: 'number', defaultValue: '0.95',                half: true },
  { key: 'efficiency_dispatch', label: 'η dispatch',      type: 'number', defaultValue: '0.95',                half: true },
]

// Bus-picker fields carry metadata so the renderer can restrict by carrier.
// Used by Power-to-Heat / CHP / Demand variants to ensure (e.g.) a heating
// load only attaches to a heat-carrier bus.
interface BusFieldSpec extends FieldSpec { busCarrierFilter?: BusCarrier }

const FIELD_MAP: Record<string, (FieldSpec | BusFieldSpec)[]> = {
  bus: [
    { key: 'name',    label: 'Name',       type: 'text',   required: true },
    { key: 'v_nom',   label: 'Voltage',    type: 'number', defaultValue: '1',  unit: 'kV',  half: true },
    { key: 'carrier', label: 'Carrier',    type: 'select', defaultValue: 'AC',              half: true, options: ['AC','DC','H2','heat','gas'] },
    { key: 'control', label: 'Control',    type: 'select', defaultValue: 'PQ',              half: true, options: ['PQ','PV','Slack'] },
    // x/y are the bus's geographic coordinates (longitude / latitude).
    // A canvas drop does NOT seed them: React Flow flow-space pixels are not
    // a geographic position, and an in-range pair (e.g. 45, 30) would be
    // accepted as one — a bus silently placed off the coast of Egypt, with
    // the backend then measuring line lengths to it. A dropped bus keeps
    // these '0' defaults, lands as unplaced (utils/geo.ts:44), and is picked
    // up by UnplacedBusesPanel. The drop point still reaches the canvas via
    // setPendingNodePosition below, which is a layout cache, not PyPSA data.
    { key: 'x',       label: 'Longitude',  type: 'number', defaultValue: '0',               half: true },
    { key: 'y',       label: 'Latitude',   type: 'number', defaultValue: '0',               half: true },
  ],
  // Transformer voltage-step presets used by the Type dropdown. Picking a
  // preset snaps v_nom_0 / v_nom_1 (and a sensible s_nom / x default) via
  // the DEPENDENT_DEFAULTS rule below. "Custom" leaves the voltage fields
  // free so the user can enter any pair.
  transformer: [
    { key: 'name',         label: 'Name',           type: 'text',   required: true },
    { key: 'bus0',         label: 'High-voltage bus',type: 'bus',   required: true,
      busCarrierFilter: 'electricity' } as BusFieldSpec,
    { key: 'bus1',         label: 'Low-voltage bus', type: 'bus',   required: true,
      busCarrierFilter: 'electricity' } as BusFieldSpec,
    { key: 'type', label: 'Voltage step',          type: 'select',
      defaultValue: 'Custom',
      options: ['380/220 kV','380/110 kV','380/132 kV','220/110 kV','220/132 kV',
        '132/33 kV','132/20 kV','110/33 kV','110/20 kV','33/11 kV','20/0.4 kV','Custom'] },
    { key: 'v_nom_0',      label: 'v_nom₀ (HV)',    type: 'number', defaultValue: '0', unit: 'kV',  half: true },
    { key: 'v_nom_1',      label: 'v_nom₁ (LV)',    type: 'number', defaultValue: '0', unit: 'kV',  half: true },
    { key: 's_nom',        label: 'S nom',          type: 'number', defaultValue: '100',  unit: 'MVA', half: true },
    { key: 'r',            label: 'Resistance r',   type: 'number', defaultValue: '0.0', unit: 'pu', half: true },
    { key: 'x',            label: 'Reactance x',    type: 'number', defaultValue: '0.1', unit: 'pu', half: true },
    { key: 'tap_ratio',    label: 'Tap ratio',      type: 'number', defaultValue: '1.0',              half: true },
    { key: 'phase_shift',  label: 'Phase shift',    type: 'number', defaultValue: '0.0', unit: '°',   half: true },
    { key: 's_nom_extendable', label: 'Extendable', type: 'checkbox', defaultValue: 'false' },
    { key: 'capital_cost', label: 'Capital cost',   type: 'number', defaultValue: '0', unit: '€/MVA' },
  ],
  line: [
    { key: 'name',             label: 'Name',         type: 'text',     required: true },
    { key: 'bus0',             label: 'From Bus',      type: 'bus',      required: false },
    { key: 'bus1',             label: 'To Bus',        type: 'bus',      required: false },
    { key: 'carrier',          label: 'Carrier',       type: 'select',   defaultValue: 'AC', options: ['AC','DC'], half: true },
    { key: 'length',           label: 'Length',        type: 'number',   defaultValue: '10', unit: 'km',    half: true },
    // r/x/b entered per km; SUBMIT_TRANSFORM multiplies by length before
    // sending PyPSA-native absolute Ohm / Siemens values. Defaults reflect
    // typical 380 kV overhead-line per-km values (r ~ 0.03, x ~ 0.3, b ~ 0).
    { key: 'r_per_km',         label: 'r',             type: 'number',   defaultValue: '0.03',unit: 'Ω/km', half: true },
    { key: 'x_per_km',         label: 'x',             type: 'number',   defaultValue: '0.3', unit: 'Ω/km', half: true },
    { key: 'b_per_km',         label: 'b',             type: 'number',   defaultValue: '0',   unit: 'S/km', half: true },
    { key: 's_nom',            label: 'S nom',         type: 'number',   defaultValue: '100', unit: 'MVA',  half: true },
    { key: 's_nom_extendable', label: 'S nom extendable', type: 'checkbox', defaultValue: 'false' },
  ],
  electrolyzer: [
    { key: 'name',         label: 'Name',                    type: 'text',   required: true },
    { key: 'bus0',         label: 'Electricity bus (input)', type: 'bus',    required: true,  busCarrierFilter: 'non-h2' } as BusFieldSpec,
    { key: 'bus1',         label: 'H₂ bus (output)',         type: 'bus',    required: true,  busCarrierFilter: 'h2' } as BusFieldSpec,
    { key: 'carrier',      label: 'Carrier',                 type: 'text',   defaultValue: 'H2' },
    { key: 'p_nom',        label: 'P nom (elec in)',         type: 'number', defaultValue: '100', unit: 'MW',    half: true },
    { key: 'efficiency',   label: 'Efficiency (η)',          type: 'number', defaultValue: '0.70',               half: true },
    { key: 'p_min_pu',     label: 'p_min_pu',               type: 'number', defaultValue: '0',                  half: true },
    { key: 'p_nom_extendable', label: 'Extendable',         type: 'checkbox', defaultValue: 'false' },
    { key: 'capital_cost', label: 'Capital cost',           type: 'number', defaultValue: '0', unit: '€/MW',   half: true },
    { key: 'marginal_cost',label: 'Marginal cost',          type: 'number', defaultValue: '0', unit: '€/MWh',  half: true },
  ],
  fuel_cell: [
    { key: 'name',         label: 'Name',                    type: 'text',   required: true },
    { key: 'bus0',         label: 'H₂ bus (input)',          type: 'bus',    required: true,  busCarrierFilter: 'h2' } as BusFieldSpec,
    { key: 'bus1',         label: 'Electricity bus (output)',type: 'bus',    required: true,  busCarrierFilter: 'non-h2' } as BusFieldSpec,
    { key: 'carrier',      label: 'Carrier',                 type: 'text',   defaultValue: 'H2' },
    { key: 'p_nom',        label: 'P nom (H₂ in)',          type: 'number', defaultValue: '50',  unit: 'MW',   half: true },
    { key: 'efficiency',   label: 'Efficiency (η)',          type: 'number', defaultValue: '0.55',              half: true },
    { key: 'p_nom_extendable', label: 'Extendable',         type: 'checkbox', defaultValue: 'false' },
    { key: 'capital_cost', label: 'Capital cost',           type: 'number', defaultValue: '0', unit: '€/MW',  half: true },
    { key: 'marginal_cost',label: 'Marginal cost',          type: 'number', defaultValue: '0', unit: '€/MWh', half: true },
  ],
  thermal:   genFields('gas'),
  renewable: genFields('wind'),
  battery:   storFields('battery', '4'),
  psh:       storFields('hydro',   '6'),
  hydrogen:  storFields('H2',      '24'),
  caes:      storFields('air',     '12'),
  flywheel:  storFields('flywheel','0.25'),
  // Power-to-Heat: Link from electricity → heat. The `carrier` field doubles
  // as the device-type discriminator (resistive vs heat-pump-air vs ground);
  // selecting a value also updates the efficiency default via a side-effect
  // in the form's `set` callback below.
  power_to_heat: [
    { key: 'name',         label: 'Name',                    type: 'text',   required: true },
    { key: 'carrier',      label: 'Type',                    type: 'select',
      defaultValue: 'heat-pump-air',
      options: ['resistive-heater', 'heat-pump-air', 'heat-pump-ground'] },
    { key: 'bus0',         label: 'Electricity bus (input)', type: 'bus', required: true,
      busCarrierFilter: 'electricity' } as BusFieldSpec,
    { key: 'bus1',         label: 'Heat bus (output)',       type: 'bus', required: true,
      busCarrierFilter: 'heat' } as BusFieldSpec,
    { key: 'p_nom',        label: 'P nom (elec in)',         type: 'number', defaultValue: '50',  unit: 'MW',    half: true },
    { key: 'efficiency',   label: 'η / COP',                 type: 'number', defaultValue: '3.5',                half: true },
    { key: 'p_nom_extendable', label: 'Extendable',          type: 'checkbox', defaultValue: 'false' },
    { key: 'capital_cost', label: 'Capital cost',            type: 'number', defaultValue: '0',   unit: '€/MW',  half: true },
    { key: 'marginal_cost',label: 'Marginal cost',           type: 'number', defaultValue: '0',   unit: '€/MWh', half: true },
  ],
  // CHP: Link from a fuel bus → electricity (efficiency) + heat (efficiency2).
  // Uses bus2 + the new efficiency2 field on LinkCreate. p_nom is fuel input
  // rate; outputs are fuel × efficiency / efficiency2 respectively.
  chp: [
    { key: 'name',         label: 'Name',                    type: 'text',   required: true },
    { key: 'bus0',         label: 'Fuel bus (input)',        type: 'bus', required: true,
      busCarrierFilter: 'gas' } as BusFieldSpec,
    { key: 'bus1',         label: 'Electricity bus (output)',type: 'bus', required: true,
      busCarrierFilter: 'electricity' } as BusFieldSpec,
    { key: 'bus2',         label: 'Heat bus (output)',       type: 'bus', required: true,
      busCarrierFilter: 'heat' } as BusFieldSpec,
    { key: 'carrier',      label: 'Carrier',                 type: 'text',   defaultValue: 'gas' },
    { key: 'p_nom',        label: 'P nom (fuel in)',         type: 'number', defaultValue: '100', unit: 'MW',    half: true },
    { key: 'efficiency',   label: 'η electrical',            type: 'number', defaultValue: '0.4',                half: true },
    { key: 'efficiency2',  label: 'η thermal',               type: 'number', defaultValue: '0.4',                half: true },
    { key: 'p_nom_extendable', label: 'Extendable',          type: 'checkbox', defaultValue: 'false' },
    { key: 'capital_cost', label: 'Capital cost',            type: 'number', defaultValue: '0',   unit: '€/MW',  half: true },
    { key: 'marginal_cost',label: 'Marginal cost',           type: 'number', defaultValue: '0',   unit: '€/MWh', half: true },
  ],
  // Thermal storage: PyPSA Store on a heat bus (e_nom only — power capacity
  // is set by whatever charging Link feeds it, not the Store itself).
  thermal_storage: [
    { key: 'name',             label: 'Name',           type: 'text', required: true },
    { key: 'bus',              label: 'Heat bus',       type: 'bus',  required: true,
      busCarrierFilter: 'heat' } as BusFieldSpec,
    { key: 'carrier',          label: 'Carrier',        type: 'text', defaultValue: 'heat' },
    { key: 'e_nom',            label: 'Energy capacity',type: 'number', defaultValue: '100', unit: 'MWh', half: true },
    { key: 'e_nom_extendable', label: 'Extendable',     type: 'checkbox', defaultValue: 'false', half: true },
    { key: 'e_cyclic',         label: 'Cyclic state-of-charge', type: 'checkbox', defaultValue: 'true' },
    { key: 'capital_cost',     label: 'Capital cost',   type: 'number', defaultValue: '0',   unit: '€/MWh' },
  ],
  // Carrier-specific demand entries. The bus picker is filtered so the user
  // can't accidentally attach (e.g.) a hydrogen load to an AC bus.
  load_elec: [
    { key: 'name',    label: 'Name',           type: 'text',   required: true },
    { key: 'bus',     label: 'Electricity bus',type: 'bus',    required: true,
      busCarrierFilter: 'electricity' } as BusFieldSpec,
    { key: 'carrier', label: 'Carrier',        type: 'text',   defaultValue: 'AC' },
    { key: 'p_set',   label: 'P set',          type: 'number', defaultValue: '0', unit: 'MW' },
  ],
  load_h2: [
    { key: 'name',    label: 'Name',          type: 'text',   required: true },
    { key: 'bus',     label: 'H₂ bus',        type: 'bus',    required: true,
      busCarrierFilter: 'h2' } as BusFieldSpec,
    { key: 'carrier', label: 'Carrier',       type: 'text',   defaultValue: 'H2' },
    { key: 'p_set',   label: 'P set',         type: 'number', defaultValue: '0', unit: 'MW' },
  ],
  load_heat: [
    { key: 'name',    label: 'Name',          type: 'text',   required: true },
    { key: 'bus',     label: 'Heat bus',      type: 'bus',    required: true,
      busCarrierFilter: 'heat' } as BusFieldSpec,
    { key: 'carrier', label: 'Carrier',       type: 'text',   defaultValue: 'heat' },
    { key: 'p_set',   label: 'P set',         type: 'number', defaultValue: '0', unit: 'MW' },
  ],
}

/**
 * Palette id → the field a drop-on-a-bus prefills (spec D27).
 *
 * 17 of the 18 palette items have a terminal. `bus` is deliberately absent:
 * it IS the terminal, so dropping a bus onto a bus behaves as a plain canvas
 * drop. The six branch types take `bus0`; the eleven single-terminal types
 * take `bus`.
 *
 * The orphan FIELD_MAP keys `link` / `generator` / `load` are not listed —
 * they are unreachable from the live UI and are deleted in the same change as
 * AssetPalette.tsx (spec D29).
 */
export const TERMINAL_FIELD: Record<string, 'bus' | 'bus0'> = {
  line: 'bus0', transformer: 'bus0',
  electrolyzer: 'bus0', fuel_cell: 'bus0', power_to_heat: 'bus0', chp: 'bus0',
  thermal: 'bus', renewable: 'bus',
  battery: 'bus', psh: 'bus', caes: 'bus', flywheel: 'bus', hydrogen: 'bus',
  thermal_storage: 'bus',
  load_elec: 'bus', load_h2: 'bus', load_heat: 'bus',
}

const AUTO_PREFIX: Record<string, string> = {
  bus: 'Bus', line: 'Line', transformer: 'Tx',
  thermal: 'Gas', renewable: 'Wind',
  battery: 'Battery', psh: 'PSH', hydrogen: 'H2', caes: 'CAES', flywheel: 'FW',
  electrolyzer: 'Electrolyzer', fuel_cell: 'FuelCell',
  power_to_heat: 'P2H', chp: 'CHP', thermal_storage: 'TES',
  load_elec: 'Load', load_h2: 'H2Load', load_heat: 'HeatLoad',
}

const QUERY_KEY: Record<string, string> = {
  bus: 'buses', line: 'lines', transformer: 'transformers',
  thermal: 'generators', renewable: 'generators',
  battery: 'storage_units', psh: 'storage_units', hydrogen: 'storage_units',
  caes: 'storage_units', flywheel: 'storage_units',
  electrolyzer: 'links', fuel_cell: 'links',
  power_to_heat: 'links', chp: 'links',
  thermal_storage: 'stores',
  load_elec: 'loads', load_h2: 'loads', load_heat: 'loads',
}

const COMPONENT_TYPE: Record<string, string> = {
  bus: 'Bus', line: 'Line', transformer: 'Transformer',
  thermal: 'Generator', renewable: 'Generator',
  battery: 'StorageUnit', psh: 'StorageUnit', hydrogen: 'StorageUnit',
  caes: 'StorageUnit', flywheel: 'StorageUnit',
  electrolyzer: 'Link', fuel_cell: 'Link',
  power_to_heat: 'Link', chp: 'Link',
  thermal_storage: 'Store',
  load_elec: 'Load', load_h2: 'Load', load_heat: 'Load',
}

type CreateFn = (p: Record<string, unknown>) => Promise<unknown>

const CREATE_FN: Record<string, CreateFn> = {
  bus:           p => networkApi.createBus(p as Partial<Bus>),
  line:          p => networkApi.createLine(p as Partial<Line>),
  transformer:   p => networkApi.createTransformer(p as Partial<Transformer>),
  electrolyzer:  p => networkApi.createLink(p as Partial<Link>),
  fuel_cell:     p => networkApi.createLink(p as Partial<Link>),
  power_to_heat: p => networkApi.createLink(p as Partial<Link>),
  chp:           p => networkApi.createLink(p as Partial<Link>),
  thermal:       p => networkApi.createGenerator(p as Partial<Generator>),
  renewable:     p => networkApi.createGenerator(p as Partial<Generator>),
  battery:       p => networkApi.createStorageUnit(p as Partial<StorageUnit>),
  psh:           p => networkApi.createStorageUnit(p as Partial<StorageUnit>),
  hydrogen:      p => networkApi.createStorageUnit(p as Partial<StorageUnit>),
  caes:          p => networkApi.createStorageUnit(p as Partial<StorageUnit>),
  flywheel:      p => networkApi.createStorageUnit(p as Partial<StorageUnit>),
  thermal_storage: p => networkApi.createStore(p as Partial<Store>),
  load_elec:     p => networkApi.createLoad(p as Partial<Load>),
  load_h2:       p => networkApi.createLoad(p as Partial<Load>),
  load_heat:     p => networkApi.createLoad(p as Partial<Load>),
}

// Per-asset payload transformer. Runs AFTER form → payload coercion in
// `createMut` and BEFORE the API call. Keep narrowly scoped: only for fields
// the UI displays differently from PyPSA's storage format. Today: Line r/x/b
// are entered as Ω/km · S/km but PyPSA stores absolute Ω · S.
const SUBMIT_TRANSFORM: Record<string, (p: Record<string, unknown>) => Record<string, unknown>> = {
  line: (p) => {
    const L = Number(p.length ?? 0) || 0
    const out = { ...p }
    out.r = (Number(p.r_per_km ?? 0) || 0) * L
    out.x = (Number(p.x_per_km ?? 0) || 0) * L
    out.b = (Number(p.b_per_km ?? 0) || 0) * L
    delete out.r_per_km
    delete out.x_per_km
    delete out.b_per_km
    return out
  },
}

// Power-to-Heat: when the user picks a device type via the carrier dropdown,
// snap efficiency to a sensible default (resistive ≈ 1.0, air-source HP ≈ 3.5,
// ground-source HP ≈ 4.5). Kept as a per-id table so future asset types can
// plug in their own dependent-default rules without growing the `set` body.
const DEPENDENT_DEFAULTS: Record<string, (key: string, value: string) => Partial<Record<string, string>> | null> = {
  power_to_heat: (key, value) => {
    if (key !== 'carrier') return null
    const efficiency = ({
      'resistive-heater':  '1.0',
      'heat-pump-air':     '3.5',
      'heat-pump-ground':  '4.5',
    } as Record<string, string>)[value]
    return efficiency ? { efficiency } : null
  },
  // Transformer: picking a voltage-step preset snaps v_nom_0 / v_nom_1 plus
  // typical s_nom + reactance. Mirrors the backend's _TRANSFORMER_PRESETS
  // table; keep the two in sync if you add or change presets.
  transformer: (key, value) => {
    if (key !== 'type') return null
    const presets: Record<string, { v_nom_0: string; v_nom_1: string; s_nom: string; x: string }> = {
      '380/220 kV': { v_nom_0: '380', v_nom_1: '220', s_nom: '600', x: '0.08' },
      '380/110 kV': { v_nom_0: '380', v_nom_1: '110', s_nom: '600', x: '0.10' },
      '380/132 kV': { v_nom_0: '380', v_nom_1: '132', s_nom: '600', x: '0.10' },
      '220/110 kV': { v_nom_0: '220', v_nom_1: '110', s_nom: '300', x: '0.10' },
      '220/132 kV': { v_nom_0: '220', v_nom_1: '132', s_nom: '300', x: '0.10' },
      '132/33 kV':  { v_nom_0: '132', v_nom_1: '33',  s_nom: '100', x: '0.12' },
      '132/20 kV':  { v_nom_0: '132', v_nom_1: '20',  s_nom: '60',  x: '0.12' },
      '110/33 kV':  { v_nom_0: '110', v_nom_1: '33',  s_nom: '80',  x: '0.12' },
      '110/20 kV':  { v_nom_0: '110', v_nom_1: '20',  s_nom: '60',  x: '0.12' },
      '33/11 kV':   { v_nom_0: '33',  v_nom_1: '11',  s_nom: '25',  x: '0.10' },
      '20/0.4 kV':  { v_nom_0: '20',  v_nom_1: '0.4', s_nom: '1',   x: '0.06' },
    }
    return presets[value] ?? null
  },
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function CreationForm({ item }: { item: CreationRequest }) {
  const { setCreationItem, setSelectedComponent, setPendingNodePosition } = useUIStore()
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()

  const fields = FIELD_MAP[item.id] ?? []
  const qKey = QUERY_KEY[item.id] ?? ''
  const prefix = AUTO_PREFIX[item.id] ?? 'Asset'

  const allBuses = useMemo(
    () => qc.getQueryData<Array<{ name: string; carrier?: string }>>(nk(currentProject, 'buses')) ?? [],
    [qc, currentProject],
  )
  const busNames = useMemo(() => allBuses.map(b => b.name).sort(), [allBuses])

  const filteredBusNames = (filter: BusCarrier | undefined): string[] => {
    if (!filter) return busNames
    return allBuses
      .filter(b => carrierMatches(b.carrier ?? '', filter))
      .map(b => b.name)
      .sort()
  }

  const [form, setForm] = useState<Record<string, string>>(() => {
    const existingCount = (qc.getQueryData<unknown[]>(nk(useUIStore.getState().currentProject, qKey)) ?? []).length
    const init: Record<string, string> = {}
    fields.forEach(f => { init[f.key] = f.defaultValue ?? '' })
    init.name = `${prefix} ${existingCount + 1}`
    // Terminal prefill from a drop-on-a-bus (spec D27). Applied only when the
    // target bus passes the field's own carrier filter — otherwise the form
    // opens unprefilled and the existing mismatch line ("No H₂ bus in
    // network…") does the explaining, rather than a hydrogen bus silently
    // landing in an electricity-only terminal.
    //
    // A schematic drop deliberately does NOT seed init.x / init.y (spec D28):
    // React Flow flow-space pixels are not a geographic position. See the
    // comment on FIELD_MAP.bus's x/y fields.
    const terminal = TERMINAL_FIELD[item.id]
    if (item.dropBusName && terminal) {
      const spec = fields.find(f => f.key === terminal) as BusFieldSpec | undefined
      if (spec && filteredBusNames(spec.busCarrierFilter).includes(item.dropBusName)) {
        init[terminal] = item.dropBusName
      }
    }
    return init
  })
  const [errors, setErrors] = useState<Record<string, string>>({})

  const set = (k: string, v: string) => {
    setForm(f => {
      const next = { ...f, [k]: v }
      // Apply dependent-default rules for this asset type. Used by Power-to-Heat
      // to bump efficiency when the device type changes (carrier dropdown).
      const updates = DEPENDENT_DEFAULTS[item.id]?.(k, v)
      if (updates) Object.assign(next, updates)
      return next
    })
    if (errors[k]) setErrors(e => { const n = { ...e }; delete n[k]; return n })
  }

  const validate = () => {
    const errs: Record<string, string> = {}
    if (!form.name?.trim()) errs.name = 'Name is required'
    fields.forEach(f => {
      if (f.required && f.type === 'bus' && !form[f.key]?.trim()) {
        // bus field not strictly required for line/link (can connect later)
      }
      if (f.type === 'number' && form[f.key] !== '' && isNaN(parseFloat(form[f.key] ?? ''))) {
        errs[f.key] = 'Must be a number'
      }
    })
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const createMut = useMutation({
    mutationFn: async () => {
      if (!validate()) throw new Error('validation')
      const payload: Record<string, unknown> = {}
      fields.forEach(f => {
        const v = form[f.key] ?? ''
        if (f.type === 'number') payload[f.key] = v !== '' ? parseFloat(v) : 0
        else if (f.type === 'checkbox') payload[f.key] = v === 'true'
        else payload[f.key] = v
      })
      const fn = CREATE_FN[item.id]
      if (!fn) throw new Error(`Unknown asset type: ${item.id}`)
      const transform = SUBMIT_TRANSFORM[item.id]
      return fn(transform ? transform(payload) : payload)
    },
    onSuccess: () => {
      const proj = useUIStore.getState().currentProject
      const invalidateKeys = [qKey, 'buses']
      invalidateKeys.forEach(k => qc.invalidateQueries({ queryKey: nk(proj, k) }))
      const name = form.name.trim()
      // Drop-position handoff. Only Bus drops carry a meaningful position —
      // generators / loads / etc. are positioned on the canvas relative to
      // their parent bus, not as standalone nodes, so a drop coord doesn't
      // map to anything for them. Send it to the canvas via pendingNodePosition
      // which TopologyCanvas reads in a useEffect once the new bus appears.
      if (item.dropPosition && item.id === 'bus') {
        setPendingNodePosition({ name, position: item.dropPosition })
      }
      setCreationItem(null)
      setSelectedComponent({ type: COMPONENT_TYPE[item.id] ?? item.id, name })
      toast.success(`${item.label} "${name}" added`)
    },
    onError: (e: Error) => {
      if (e.message !== 'validation') toast.error(e.message)
    },
  })

  const inp = 'bg-bg border border-border rounded px-2 py-1.5 text-xs focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 w-full'
  const lbl = 'block text-[10px] font-medium text-text mb-0.5'
  const errCls = 'text-[9px] text-danger mt-0.5'

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
        <div>
          <p className="text-[9px] font-bold text-accent uppercase tracking-widest">ADD</p>
          <p className="text-sm font-semibold text-text">{item.label}</p>
        </div>
        <button
          onClick={() => setCreationItem(null)}
          className="p-1 text-muted hover:text-text transition-colors"
          title="Close"
        >
          <X size={14} />
        </button>
      </div>

      {/* Form body */}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        <div className="grid grid-cols-2 gap-x-2 gap-y-2.5">
          {fields.map(f => {
            const colSpan = f.half ? '' : 'col-span-2'
            const error = errors[f.key]

            if (f.type === 'checkbox') {
              return (
                <label key={f.key} className="col-span-2 flex items-center gap-1.5 cursor-pointer py-0.5">
                  <input
                    type="checkbox"
                    checked={form[f.key] === 'true'}
                    onChange={e => set(f.key, e.target.checked ? 'true' : 'false')}
                    className="accent-accent"
                  />
                  <span className="text-xs text-text">{f.label}</span>
                </label>
              )
            }

            if (f.type === 'bus') {
              const bf = f as BusFieldSpec
              const availBuses = filteredBusNames(bf.busCarrierFilter)
              const noMatch = bf.busCarrierFilter !== undefined && availBuses.length === 0
              const warnLabel = ({
                'h2': 'H₂', 'non-h2': 'non-H₂', 'electricity': 'electricity',
                'heat': 'heat', 'gas': 'gas/biomass',
              } as Record<string, string>)[bf.busCarrierFilter ?? ''] ?? bf.busCarrierFilter
              return (
                <div key={f.key} className="col-span-2">
                  <label className={lbl}>{f.label}{f.required ? ' *' : ''}</label>
                  <BusAutocomplete
                    value={form[f.key] ?? ''}
                    onChange={v => set(f.key, v)}
                    buses={availBuses}
                    placeholder="Select bus…"
                  />
                  {noMatch && (
                    <p className="flex items-center gap-1 text-[9px] text-warn mt-0.5">
                      <AlertTriangle size={10} />
                      No {warnLabel} bus in network — add one first
                    </p>
                  )}
                  {error && <p className={errCls}>{error}</p>}
                </div>
              )
            }

            if (f.type === 'select') {
              return (
                <div key={f.key} className={colSpan}>
                  <label className={lbl}>{f.label}</label>
                  <select className={inp} value={form[f.key] ?? ''} onChange={e => set(f.key, e.target.value)}>
                    {(f.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                  {error && <p className={errCls}>{error}</p>}
                </div>
              )
            }

            if (f.type === 'number') {
              return (
                <div key={f.key} className={colSpan}>
                  <label className={lbl}>
                    {f.label}{f.unit ? <span className="text-muted font-normal"> ({f.unit})</span> : null}
                  </label>
                  <input
                    type="number" step="any"
                    className={inp}
                    value={form[f.key] ?? ''}
                    onChange={e => set(f.key, e.target.value)}
                  />
                  {error && <p className={errCls}>{error}</p>}
                </div>
              )
            }

            // text
            return (
              <div key={f.key} className={colSpan}>
                <label className={lbl}>{f.label}{f.required ? ' *' : ''}</label>
                <input
                  type="text"
                  className={inp}
                  value={form[f.key] ?? ''}
                  onChange={e => set(f.key, e.target.value)}
                />
                {error && <p className={errCls}>{error}</p>}
              </div>
            )
          })}
        </div>
      </div>

      {/* Footer */}
      <div className="flex gap-2 px-3 py-2 border-t border-border shrink-0">
        <button
          onClick={() => setCreationItem(null)}
          className="flex-1 py-1.5 border border-border rounded text-xs text-muted hover:text-text transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={() => createMut.mutate()}
          disabled={createMut.isPending}
          className="flex-1 py-1.5 bg-accent text-white rounded text-xs font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          {createMut.isPending ? 'Adding…' : 'Add to Network'}
        </button>
      </div>
    </div>
  )
}
