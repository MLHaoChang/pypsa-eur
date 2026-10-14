import client from './client'

// S1 (plan 2026-10-13-s1-expert-asset-cards.md): one descriptor per field,
// served by `GET /api/network/asset_schema/{component}`
// (services/asset_schema/fields.py). `display = stored × scale`.

/**
 * The classes `GET /network/asset_schema/{component}` describes (the backend's
 * `fields.known_components()`; pinned against the fixture by a test). A view
 * that shows several classes asks only for these, so a Bus or Carrier tab
 * makes no request the route would refuse.
 */
export const SCHEMA_CLASSES: ReadonlySet<string> = new Set([
  'Generator', 'Line', 'Link', 'Load', 'Store', 'StorageUnit', 'Transformer',
  // M5: the non-asset components.
  'Bus', 'Carrier', 'GlobalConstraint', 'ShuntImpedance',
])

export type AssetFieldKind = 'number' | 'int' | 'bool' | 'enum' | 'text' | 'bus' | 'carrier'
export type AssetGroupId =
  | 'identity' | 'size' | 'investment' | 'fom' | 'variable' | 'performance' | 'replacement' | 'provenance'

export interface AssetFieldSpec {
  key: string
  technical_name: string
  group: AssetGroupId
  label: string
  help: string
  unit_display: string
  unit_storage: string
  scale: number
  kind: AssetFieldKind
  options: string[]
  required: boolean
  key_param: boolean
  derived: boolean
  reveal_when: Record<string, unknown> | null
  part: string | null
  /** What an emptied input saves: null, 0, "" (a text column typed `str`), or the current value. */
  blank: 'clear' | 'zero' | 'empty' | 'keep'
}

export interface AssetSchemaGroup {
  id: AssetGroupId
  label: string
  fields: AssetFieldSpec[]
}

export interface AssetSchema {
  component: string
  groups: AssetSchemaGroup[]
}

export interface ProvenanceEntry {
  status: 'default' | 'customised' | 'measured'
  source: string | null
  source_year: number | null
  currency_year: number | null
  range: [number, number] | null
  illustrative: boolean
}

/** One investment part of an asset (`services/asset_schema/access.py` `upfront_parts`): EUR per unit of the
 *  asset's sizing variable (`AssetCostRow.basis`). */
export interface AssetCostPart {
  name: string
  upfront_per_unit: number | null
  lifetime: number | null           // null: never retires
  fom_share: number | null
  derived_from_capital_cost: boolean
}

/** One investable asset in the Economics panel's Asset costs tab (`GET /network/asset_costs`). */
export interface AssetCostRow {
  component: string
  name: string
  carrier: string
  basis: 'EUR/MW' | 'EUR/MWh' | 'EUR/MVA'
  size_column: string
  size: number | null
  extendable: boolean
  parts: AssetCostPart[]            // empty: not priced
  upfront_per_unit: number | null
  upfront_total: number | null
  capital_cost: number | null       // annual, as stored (derived where priced by parts)
  fom_cost: number | null
  provenance: Record<string, ProvenanceEntry>
}

export interface AssetCostsPayload {
  discount_rate: number
  assets: AssetCostRow[]
}

export const assetSchemaApi = {
  costs: () => client.get<AssetCostsPayload>('/network/asset_costs').then(r => r.data),
  get: (component: string) =>
    client.get<AssetSchema>(`/network/asset_schema/${component}`).then(r => r.data),
  provenance: (component: string, name: string) =>
    client.get<{ component: string; name: string; fields: Record<string, ProvenanceEntry> }>(
      `/network/asset_schema/${component}/provenance/${encodeURIComponent(name)}`).then(r => r.data),
}
