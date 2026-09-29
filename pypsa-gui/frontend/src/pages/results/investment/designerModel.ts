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

/** Where a server problem (PUT value_flows 422) is shown — anchored on the
 *  server's own phrasing (`participants.value_flows_problems`), so a party or
 *  contract NAME cannot route it (WP3.6 review #7). */
export function problemSection(problem: string): Section {
  const p = problem.trim()
  if (/^tariff payee|^tariff_payees/i.test(p)) return 'payees'
  if (/^(hub member|hub_members|an allocation key|the contracted_capacity key|fixed_shares|the energy_hub template)/i.test(p)) {
    return 'hub'
  }
  if (/^asset '|^(Generator|StorageUnit|Store|Link|Line|Transformer) '.*' is owned twice|^asset_owners/.test(p)) {
    return 'assets'
  }
  return 'participants'
}

const norm = (x: string) => x.trim().toLowerCase()

/** The payee an item resolves to, as the server resolves it: a rule for the
 *  item, then a rule for its kind, then the designer's default (WP3.6 review #5). */
export function resolvedPayee(cfg: ValueFlowConfig, item: { id: string; kind: string;
                                                           default_payee: string }): {
  payee: string; by: 'item' | 'kind' | 'default'
} {
  const byItem = (cfg.tariff_payees ?? []).find(r => r.item_id === item.id)
  if (byItem) return { payee: byItem.payee, by: 'item' }
  const byKind = (cfg.tariff_payees ?? []).find(r => !r.item_id && r.kind === item.kind)
  if (byKind) return { payee: byKind.payee, by: 'kind' }
  return { payee: item.default_payee, by: 'default' }
}

export function removeKindRule(cfg: ValueFlowConfig, kind: string): ValueFlowConfig {
  return update(cfg, { tariff_payees: (cfg.tariff_payees ?? []).filter(r => r.item_id || r.kind !== kind) })
}

/** Add an external unless it is blank or already there (case and spacing
 *  aside, as the server matches parties). */
export function addExternal(cfg: ValueFlowConfig, name: string): ValueFlowConfig {
  const x = name.trim()
  if (!x || (cfg.externals ?? []).some(e => norm(e) === norm(x))) return cfg
  return update(cfg, { externals: [...(cfg.externals ?? []), x] })
}

/** Drop a hub member (and its fixed share). */
export function removeHubMember(cfg: ValueFlowConfig, link: string): ValueFlowConfig {
  const gone = (cfg.hub_members ?? []).find(m => m.link === link)
  const shares = { ...(cfg.allocation?.shares ?? {}) }
  if (gone) delete shares[gone.participant]
  return update(cfg, {
    hub_members: (cfg.hub_members ?? []).filter(m => m.link !== link),
    allocation: cfg.allocation ? { ...cfg.allocation,
      shares: cfg.allocation.basis === 'fixed_shares' ? shares : cfg.allocation.shares } : null,
  })
}

export function clearHub(cfg: ValueFlowConfig): ValueFlowConfig {
  return update(cfg, { hub_members: [], allocation: null })
}
