// The section-5 Controls block's view of GET /island (control-strategy 02):
// gridspine's control objects and each converter and UPS unit's checked block,
// which `get_island` adds beside the sidecar. Kept apart from
// `campusElectrical.ts` so the shared island types stay as they are.
import { campusApi, type IslandPatch, type IslandSource, type IslandState } from './campusElectrical'

/** One unit's checked block (gridspine `validate_unit_island`), its preset expanded. */
export interface UnitControl {
  kind: string
  /** Converters only. */
  control?: string
  values: Record<string, unknown>
  sources: Record<string, IslandSource>
  /** The preset that set the unit, absent when none applied. */
  preset?: string
  /** "project": the project's preset; "unit": the unit names its own; null: none. */
  preset_scope: 'project' | 'unit' | null
  /** UPS only. */
  transfers_on_islanding?: boolean
}

export interface IslandControlsState extends IslandState {
  /** gridspine `CONTROLS`, in the schema's order. */
  controls?: string[]
  /** gridspine `CONTROL_FIELDS`: the parameters each mode needs beyond `pf_rated`. */
  control_fields?: Record<string, string[]>
  /** `gfl_pf` needs exactly one of these. */
  pf_choice?: string[]
  presets?: Record<string, { kinds: string[]; converter: Record<string, unknown>; ups: Record<string, unknown> }>
  /** Null when the sidecar does not validate. */
  unit_controls?: Record<string, UnitControl> | null
}

export const islandControlsApi = {
  read: (project: string) => campusApi.island(project) as Promise<IslandControlsState>,
  patch: (project: string, patch: IslandPatch, revision?: string | null) =>
    campusApi.patchIsland(project, patch, revision) as Promise<IslandControlsState>,
}
