import type { CatalogueEntry } from './api'

/** A catalogue entry for tests: the fields a test names, plausible values for the rest. */
export function entry(over: Partial<CatalogueEntry> & Pick<CatalogueEntry, 'id' | 'plain_name'>): CatalogueEntry {
  return {
    model_name: over.id.split('.')[1] ?? over.id,
    scope: 'Generator', short_help: '', group: 'size_limits', order: 0, depth: 'essentials',
    unit_display: '', scale: 1, kind: 'number', options: [], options_plain: [], functions: {},
    aliases: [], related: [], former_ids: [], guide: null,
    ...over,
  }
}
