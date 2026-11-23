import type { Catalogue } from './api'
import { entry } from './testEntries'

/** A small catalogue for the guide's tests. */
export const CAP = entry({
  id: 'generator.capacity', plain_name: 'Capacity', unit_display: 'MW', model_name: 'p_nom',
  short_help: 'The largest output the asset can deliver.',
  functions: { dispatch: 'needs', load_flow: 'uses', finance: 'uses' }, related: ['generator.max', 'gone.entry'],
  former_ids: ['generator.p_nom'], default: { value: 0, source: 'engine' },
  guide: '## What it means\n\nThe largest output, in MW AC.\n\n## Example\n\nA 3 MWp roof: 2.5 MW.\n',
})
export const MAX = entry({
  id: 'generator.max', plain_name: 'Largest size', unit_display: 'MW', group: 'investment', order: 1, model_name: 'p_nom_max',
  default: { value: 'inf', source: 'engine' }, functions: { expansion: 'needs' },
})
export const LINE = entry({ id: 'line.length', plain_name: 'Length', scope: 'Line', model_name: 'length', default: { value: null, source: 'none' } })
export const LIMIT = entry({ id: 'globalconstraint.constant', plain_name: 'Limit', scope: 'GlobalConstraint', model_name: 'constant' })

export const CATALOGUE: Catalogue = {
  version: 'v',
  groups: [{ id: 'investment', plain_name: 'Investment', short_help: '', order: 5, guide: null },
    { id: 'size_limits', plain_name: 'Size and limits', short_help: 'How big.', order: 1, guide: null }],
  functions: [{ id: 'dispatch', plain_name: 'Dispatch', short_help: '', guide: null },
    { id: 'expansion', plain_name: 'Capacity expansion', short_help: '', guide: null },
    { id: 'load_flow', plain_name: 'Load flow', short_help: 'Flows and voltages.', guide: null },
    { id: 'finance', plain_name: 'Finance', short_help: '', guide: null }],
  asset_types: [
    { id: 'pvRoof', plain_name: 'Rooftop PV', plural: 'Rooftop PV', category: 'supply', components: ['Generator'], groups: [], functions: [], internal: false, guide: null },
    { id: 'lost', plain_name: 'Shortfall', plural: '', category: '', components: ['Generator'], groups: [], functions: [], internal: true, guide: null },
  ],
  entries: [CAP, MAX, LINE, LIMIT],
}
