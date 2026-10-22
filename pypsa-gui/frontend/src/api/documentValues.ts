import client from './client'
import type { EditCard, EditReceipt } from '../components/EditCard'
import type { MappableField, MappingPlan } from './equipmentTable'

// Values from a PDF, each cited by page and quote (data-to-model slice 2, owner
// AI-567b). The previews are reads outside /api/network
// (`POST /api/document_values/preview`, `.../commercial/preview`); the applies
// are the same edit-card writes the assistant's tools make. With `find`, a
// preview lists the candidate lines for each field (`document_values.find`).
// Backend: routers/network_document_values.py, services/document_values*.py.

export interface DocumentSource {
  upload_id: string
  filename: string
  sha256: string
  pages: number
  truncated: number[]
}

/** A line of the document that could be cited for a field, as the server reads it. */
export interface QuoteCandidate {
  page: number
  quote: string
  /** The number to read, when the line holds several. */
  value_text: string | null
  value: string
  unit: string
  /** The line names a word of the field's label. */
  label: boolean
}

export type Candidates = Record<string, { candidates: QuoteCandidate[]; more: number }>

export type DecimalMark = '.' | ','

export interface DocumentPlan extends MappingPlan {
  cells: unknown[]
}

export interface DocumentGap {
  kind: string
  message: string
  page?: number
  field?: string
  name?: string
  /** A `library_pinned` row: the library item to update instead. */
  library?: { kind: string; id: string; version: number | string }
}

interface Money {
  decimal_mark?: DecimalMark | null
  currency?: string | null
  money_year?: number | null
  confirm?: string[]
}

export interface AssetCitation {
  names: string[]
  field: string
  page: number
  quote: string
  unit: string
  value_text?: string | null
}

export interface CommercialCitation {
  target: 'tariff' | 'ppa'
  entity: string
  field: string
  page: number
  quote: string
  unit: string
  value_text?: string | null
}

export interface AssetDocumentPreview {
  document: { source: DocumentSource }
  fields?: MappableField[]
  candidates?: Candidates
  plan?: Omit<DocumentPlan, 'unmapped'> & { unmapped: DocumentGap[] }
  card?: EditCard | null
}

export interface CommercialTargets {
  tariff: { pinned: boolean; items: { id: string; unit: string; fields: string[] }[] } | null
  ppa: { id: string; pinned: boolean; fields: { field: string; label: string; unit: string }[] }[]
}

export interface CommercialDocumentPreview {
  document: { source: DocumentSource }
  targets: CommercialTargets
  candidates?: Candidates
  plan?: Omit<DocumentPlan, 'unmapped'> & { unmapped: DocumentGap[] }
  card?: EditCard | null
}

export interface DocumentApplied {
  receipt: EditReceipt
}

type Signed = { source_sha256: string; digest: string }

export const documentValuesApi = {
  previewAssets: (body: { upload_id: string; component_class: string; citations?: AssetCitation[]; find?: boolean } & Money) =>
    client.post<AssetDocumentPreview>('/document_values/preview', body).then(r => r.data),
  applyAssets: (body: { upload_id: string; component_class: string; citations: AssetCitation[] } & Money & Signed) =>
    client.post<DocumentApplied>('/network/document_values/apply', body).then(r => r.data),
  previewCommercial: (body: { upload_id: string; citations?: CommercialCitation[]; find?: boolean } & Money) =>
    client.post<CommercialDocumentPreview>('/document_values/commercial/preview', body).then(r => r.data),
  applyCommercial: (body: { upload_id: string; citations: CommercialCitation[] } & Money & Signed) =>
    client.post<DocumentApplied>('/simulation/document_values/apply', body).then(r => r.data),
}
