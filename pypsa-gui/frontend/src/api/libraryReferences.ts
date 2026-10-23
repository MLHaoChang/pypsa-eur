import client from './client'
import type { EditCard, EditReceipt } from '../components/EditCard'

// The shipped (common) cost references: `GET /api/library/references[/{technology}/{year}]`
// (read-only), preview `POST /api/library_reference/preview` (a read), and apply
// to one asset, `POST /api/network/library_reference/apply`
// (backend: services/library/reference_catalogue.py, reference_apply.py).

export type CapacityBasisCode =
  | 'output_electric' | 'output_electric_ac' | 'input_electric' | 'output_electric_dc'
  | 'output_product' | 'storage_energy' | 'not_applicable'
export type GridConnection = boolean | 'unknown'
export type Confirmation = 'dc_ac' | 'grid_connection' | 'money_year'

export interface ReferenceSummary {
  scope: 'common'
  reference_id: string
  technology: string
  projection_year: number
  currency: string
  currency_year: number
  capacity_basis: string
  capacity_basis_code: CapacityBasisCode
  includes_grid_connection: GridConnection
  applicability: string
  publisher: string
  licence: string
  attribution: string
  source_url: string
}

/** One value as the pack or the reference states it (the backend's `PackValue`, the fields shown). */
export interface ReferenceValue {
  key: string
  label: string
  parameter: string
  value: number
  unit: string
  currency: string | null
  currency_year: number | null
  projection_year: number | null
  source: string
  source_url?: string | null
}

export interface ReferenceComparisonRow {
  parameter: string
  reference: ReferenceValue
  pack_stamp: string
  pack: ReferenceValue | null
  differs: boolean
}

export interface ReferenceDetail {
  scope: 'common'
  reference: ReferenceSummary & {
    included_costs: string
    unmapped_terms: string[]
    source_sheet: string
    source_cells: Record<string, { cell: string; label: string; value: number }>
  }
  pack: { stamp: string; version: string; chosen_by: 'asset_provenance' | 'latest_generic'; note: string | null }
  comparison: ReferenceComparisonRow[]
}

export interface ReferenceNeed {
  confirmation: Confirmation
  text: string
  disclosure?: string
  read_as_source?: string
}

/** One field the apply writes, with the reference and the default beside it. */
export interface ReferencePlanRow {
  field: string
  new: number
  reference: { parameter: string; value: number; unit: string; currency_year: number | null;
               projection_year: number; source: string; cell: string; label: string }
  pack: { key: string; value: number; unit: string; currency_year: number | null;
          projection_year: number | null; source: string } | null
  differs: boolean
}

export interface ReferencePreview {
  /** null while a confirmation is missing: tick it and preview again. */
  card: EditCard | null
  needs: ReferenceNeed[]
  missing: Confirmation[]
  notes: string[]
  rows: ReferencePlanRow[]
  pack: ReferenceDetail['pack']
  licence: string
  attribution: string
}

export interface ReferenceApplyArgs {
  component_class: 'Generator' | 'Link'
  name: string
  technology: string
  projection_year: number
  confirm: Confirmation[]
}

export interface ReferenceApplied {
  component_class: string
  name: string
  written: Record<string, number>
  source: string
  currency: string
  currency_year: number
  licence: string
  attribution: string
  notes: string[]
  receipt: EditReceipt
}

const QUIET = { skipErrorToast: true }

export const libraryReferencesApi = {
  list: () => client.get<ReferenceSummary[]>('/library/references').then(r => r.data),
  detail: (technology: string, year: number, packSource?: string | null) =>
    client.get<ReferenceDetail>(`/library/references/${encodeURIComponent(technology)}/${year}`,
      { params: packSource ? { pack_source: packSource } : undefined }).then(r => r.data),
  preview: (args: ReferenceApplyArgs) =>
    client.post<ReferencePreview>('/library_reference/preview', args, QUIET).then(r => r.data),
  apply: (args: ReferenceApplyArgs & { digest: string }) =>
    client.post<ReferenceApplied>('/network/library_reference/apply', args, QUIET).then(r => r.data),
}
