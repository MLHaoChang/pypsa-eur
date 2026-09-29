// The participants designer's pure model (IC P3 WP3.6): the config it starts
// from, edits as plain functions, the drafts a template needs priced, and the
// section a server problem belongs to. Tested without a DOM.
import type { DesignerContext } from '../../../api/commercial'
import type {
  AssetOwnership, ParticipantRole, TariffPayeeRule, ValueFlowConfig, ValueFlowsState,
} from '../../../api/types'

export const ROLES: ParticipantRole[] = [
  'site_owner', 'developer', 'investor', 'lender', 'tax_equity', 'dso', 'tso', 'retailer',
  'tenant', 'landlord', 'hub_member', 'offtaker', 'other',
]
export const TEMPLATES = ['single_owner', 'btm_ppa', 'landlord_tenant', 'dso_developer',
                          'energy_hub'] as const
export type TemplateName = typeof TEMPLATES[number]

/** The stored config when it validates, else a one-participant start. */
export function initialConfig(state: ValueFlowsState | undefined,
                              ctx: DesignerContext | undefined): ValueFlowConfig {
  if (state?.status === 'ok' && state.value_flows) return structuredClone(state.value_flows)
  const site = ctx?.site_party ?? 'site'
  return { template: 'custom',
           participants: [{ id: site, name: site, role: 'site_owner' }],
           externals: [...(ctx?.default_externals ?? [])] }
}

/** A hand edit keeps the template's stamp: the server then discloses
 *  `template_edited` (its digest no longer matches), which is the point. */
const edited = (cfg: ValueFlowConfig): ValueFlowConfig => cfg

export function setOwner(cfg: ValueFlowConfig, component: AssetOwnership['component'],
                         asset: string, owner: string): ValueFlowConfig {
  const rest = (cfg.asset_owners ?? []).filter(o => !(o.component === component && o.asset_id === asset))
  return edited({ ...cfg, asset_owners: owner ? [...rest, { component, asset_id: asset, owner }] : rest })
}

export function ownerOf(cfg: ValueFlowConfig, component: string, asset: string): string {
  return (cfg.asset_owners ?? []).find(o => o.component === component && o.asset_id === asset)?.owner ?? ''
}

export function setPayee(cfg: ValueFlowConfig, itemId: string, payee: string): ValueFlowConfig {
  const rest: TariffPayeeRule[] = (cfg.tariff_payees ?? []).filter(r => r.item_id !== itemId)
  return edited({ ...cfg, tariff_payees: payee ? [...rest, { item_id: itemId, payee }] : rest })
}

export function payeeOf(cfg: ValueFlowConfig, itemId: string): string {
  return (cfg.tariff_payees ?? []).find(r => r.item_id === itemId)?.payee ?? ''
}

export function update(cfg: ValueFlowConfig, patch: Partial<ValueFlowConfig>): ValueFlowConfig {
  return edited({ ...cfg, ...patch })
}

/** The money fields a template draft leaves null for the user to price. */
export function draftNeeds(draft: Record<string, unknown>): string[] {
  return Object.entries(draft).filter(([, v]) => v === null).map(([k]) => k)
}

export function fillDraft(draft: Record<string, unknown>,
                          values: Record<string, string>): Record<string, unknown> | null {
  const out = { ...draft }
  for (const k of draftNeeds(draft)) {
    const v = Number(values[k])
    if (values[k] === undefined || values[k] === '' || !Number.isFinite(v) || v < 0) return null
    out[k] = v
  }
  return out
}

export type Section = 'participants' | 'assets' | 'payees' | 'hub'

/** Where a server problem (PUT value_flows 422) is shown. */
export function problemSection(problem: string): Section {
  const p = problem.toLowerCase()
  if (/tariff payee|tariff item|item /.test(p)) return 'payees'
  if (/hub member|allocation|fixed_shares|contracted_mw|group/.test(p)) return 'hub'
  if (/^asset |owner|owned/.test(p)) return 'assets'
  return 'participants'
}
