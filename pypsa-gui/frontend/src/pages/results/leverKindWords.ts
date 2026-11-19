// Plain words for a levers table option's `kind` (RAW-NAMES-1): the table never
// shows an engine id such as `storage_duration`. The parity test holds this map
// to the kinds the backend's levers module builds.
import type { LeverKey } from './EhReferenceDesignPanel'

export const LEVER_KIND_WORDS: Record<LeverKey, string> = {
  storage_duration: 'Battery duration',
  import_cap: 'Import capacity',
  import_energy: 'Import energy',
  redundancy: 'Redundancy',
}

/** The words for one option's kind; an unknown kind reads with spaces, never underscores. */
export function leverKindWords(kind: string): string {
  return (LEVER_KIND_WORDS as Record<string, string>)[kind] ?? kind.replace(/_/g, ' ')
}
