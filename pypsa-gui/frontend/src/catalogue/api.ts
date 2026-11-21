/**
 * The Parameter catalogue client (spec expert-catalogue §3.1): `GET /api/catalogue`,
 * project-free, revalidated with its ETag so an unchanged catalogue costs a 304.
 */
import { client } from '../api/client'

export type CatalogueDepth = 'essentials' | 'basic' | 'advanced' | 'detail'
/** How a function relates to an entry: it needs the value, or reads it when set. */
export type FunctionUse = 'needs' | 'uses' | string

export interface CatalogueEntry {
  id: string
  /** The model-file name. Shown only on the guide's "In model files" line (RAW-NAMES-1). */
  model_name: string
  /** The component class the entry belongs to (`Generator`, `Link`, …). */
  scope: string
  plain_name: string
  short_help: string
  group: string
  order: number
  depth: CatalogueDepth
  unit_display: string
  /** display = stored × scale */
  scale: number
  kind: string
  options: string[]
  options_plain: string[]
  functions: Record<string, FunctionUse>
  aliases: string[]
  related: string[]
  former_ids: string[]
  /** Guide markdown (rendered with raw HTML off), or null. */
  guide: string | null
  /** The value used when none is set: shown, never written. `none`: there is none. */
  default?: CatalogueDefault | null
  /** Allowed and typical values; null until the catalogue carries them. */
  range?: CatalogueRange | null
}

export interface CatalogueDefault {
  value: number | string | boolean | null
  /** `engine`: the model's built-in default; `library`: a Generic default from a Library pack. */
  source: 'none' | 'engine' | 'library' | string
}

export interface CatalogueRange {
  min?: number | null
  max?: number | null
  /** The usual span, low to high. */
  typical?: [number, number] | null
}

export interface CatalogueGroup { id: string; plain_name: string; short_help: string; order: number; guide: string | null }
export interface CatalogueFunction { id: string; plain_name: string; short_help: string; guide: string | null }
export interface CatalogueAssetType {
  id: string
  plain_name: string
  plural: string
  category: string
  components: string[]
  groups: string[]
  functions: string[]
  internal: boolean
  guide: string | null
}

export interface Catalogue {
  version: string
  groups: CatalogueGroup[]
  functions: CatalogueFunction[]
  asset_types: CatalogueAssetType[]
  entries: CatalogueEntry[]
}

// The last full body and its tag: a 304 answers with no body, so it reuses this one.
let cached: { etag: string; body: Catalogue } | null = null

/**
 * The whole catalogue. Sends `If-None-Match` only while a body is held; a 304 returns
 * that body. A 304 with no body held (after a reset) refetches without the header.
 */
export async function fetchCatalogue(): Promise<Catalogue> {
  const held = cached
  const res = await client.get<Catalogue>('/catalogue', {
    headers: held ? { 'If-None-Match': held.etag } : {},
    validateStatus: s => (s >= 200 && s < 300) || s === 304,
  })
  if (res.status === 304) {
    if (held && cached === held) return held.body
    cached = null
    const fresh = await client.get<Catalogue>('/catalogue', { headers: {} })
    return keep(fresh.data, fresh.headers?.etag)
  }
  return keep(res.data, res.headers?.etag)
}

function keep(body: Catalogue, etag: unknown): Catalogue {
  cached = typeof etag === 'string' && etag ? { etag, body } : null
  return body
}

/** Tests only: forget the held body. */
export function resetCatalogueCache(): void { cached = null }
