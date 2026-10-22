import client from './client'
import type { EditCard, EditReceipt } from '../components/EditCard'

// The manual-mapping form for an uploaded equipment table (data-to-model,
// owner AI-567). `preview` is a read (`POST /api/equipment_table/preview`,
// outside /api/network: no undo snapshot, results kept); `apply` is the same
// edit-card write the assistant uses (`POST /api/network/equipment_table/apply`).
// Backend: routers/network_equipment_table.py, services/equipment_table.py.

export type Raw = string | number | boolean | null

export interface TableSource {
  upload_id: string
  filename: string
  sha256: string
  sheet: string
  sheets: string[]
}

export interface MappableField {
  field: string
  label: string
  /** The only units the server accepts for it: stored first, then display. */
  units: string[]
  /** A cost's currency (needs the table's currency and money year), else null. */
  currency: string | null
}

export interface ColumnMap { field: string; unit: string }

export interface MappingGap {
  kind: string
  message: string
  name?: string
  field?: string
  column?: string
  row?: number
  cell?: string
}

export interface MappingNeed {
  confirmation: 'money_year'
  text: string
}

export interface MappingPlan {
  changed_cells: number
  unchanged_cells: number
  unmapped: MappingGap[]
  unmapped_total: number
  unmapped_by_kind: Record<string, number>
  needs: MappingNeed[]
  missing: string[]
  notes: string[]
}

export interface EquipmentPreview {
  table: {
    source: TableSource
    headers: string[]
    row_count: number
    rows: { row: number; cells: Record<string, Raw> }[]
  }
  fields?: MappableField[]
  plan?: MappingPlan
  card?: EditCard | null
}

export interface MappingRequest {
  upload_id: string
  sheet?: string | null
  component_class?: string
  name_column?: string
  columns?: Record<string, ColumnMap>
  currency?: string | null
  money_year?: number | null
  confirm?: string[]
}

export interface MappingApplied {
  written: Record<string, Record<string, number>>
  partial?: boolean
  failed?: { name: string; error_kind: string; message: string }
  not_attempted?: string[]
  receipt: EditReceipt
}

export const equipmentTableApi = {
  preview: (body: MappingRequest) =>
    client.post<EquipmentPreview>('/equipment_table/preview', body).then(r => r.data),
  apply: (body: MappingRequest & { source_sha256: string; digest: string }) =>
    client.post<MappingApplied>('/network/equipment_table/apply', body).then(r => r.data),
}
