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
  /** What an emptied input saves: null, 0, or the current value. */
  blank: 'clear' | 'zero' | 'keep'
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

export const assetSchemaApi = {
  get: (component: string) =>
    client.get<AssetSchema>(`/network/asset_schema/${component}`).then(r => r.data),
  provenance: (component: string, name: string) =>
    client.get<{ component: string; name: string; fields: Record<string, ProvenanceEntry> }>(
      `/network/asset_schema/${component}/provenance/${encodeURIComponent(name)}`).then(r => r.data),
}
