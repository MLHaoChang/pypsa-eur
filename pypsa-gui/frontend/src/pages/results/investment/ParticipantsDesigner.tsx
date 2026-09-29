// Results → Investment → Participants → Designer (IC P3 WP3.6): who takes part
// in the site's money and who pays whom. A template builds a starting config
// (its unpriced draft contracts are shown for pricing in a dialog, never saved
// silently); the tables edit participants, externals, asset owners, tariff
// payees and an energy hub; Save writes through the value-flows route with the
// digest it read (412 → "changed elsewhere, reload"). A server problem (422)
// is shown beside the section it names.
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  commercialApi, NoCommercialConfigError, SolverInFlightError, StaleEditError,
  type DesignerContext, type TemplateResult,
} from '../../../api/commercial'
import type { ParticipantRole, ValueFlowConfig } from '../../../api/types'
import { Dialog } from '../../../components/Dialog'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import {
  addExternal, clearHub, draftNeeds, fillDraft, initialConfig, ownerOf, payeeOf, problemSection,
  removeHubMember, removeKindRule, resolvedPayee, ROLES, setOwner, setPayee, TEMPLATES, update,
  type Section, type TemplateName,
} from './designerModel'

const input = 'border border-border rounded px-1 py-0.5 text-[11px] bg-bg'

function Problems({ list }: { list: string[] }) {
  if (!list.length) return null
  return (
    <ul role="alert" className="text-[11px] text-danger pl-3 list-disc">
      {list.map(p => <li key={p}>{p}</li>)}
    </ul>
  )
}

function problemsOf(e: unknown): string[] | null {
  const d = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  if (d?.status !== 422) return null
  const detail = d.data?.detail as { problems?: unknown; message?: unknown } | undefined
  if (Array.isArray(detail?.problems)) return detail!.problems.map(String)
  return [String(detail?.message ?? 'the config was refused')]
}

function TemplateDialog({ built, onClose, onUse }: {
  built: { name: TemplateName; result: TemplateResult } | null
  onClose: () => void
  onUse: (cfg: ValueFlowConfig, drafts: Array<Record<string, unknown>>) => Promise<void>
}) {
  const [values, setValues] = useState<Record<string, Record<string, string>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { setValues({}); setError(null) }, [built])
  if (!built) return null
  const drafts = built.result.draft_contracts
  const filled = drafts.map(d => fillDraft(d, values[String(d.id)] ?? {}))
  const ready = filled.every(Boolean)
  const cfg = built.result.config
  return (
    <Dialog open onClose={onClose} title={`Template: ${built.name.replace(/_/g, ' ')}`}
            panelClassName="bg-bg rounded-xl shadow-2xl w-[520px] max-w-[95vw] p-4 space-y-3">
      <div className="text-[11px] space-y-1">
        <p>Participants: {(cfg.participants ?? []).map(p => `${p.name} (${p.role})`).join(', ')}</p>
        <p>Asset owners: {(cfg.asset_owners ?? []).length}; externals:{' '}
          {(cfg.externals ?? []).join(', ')}</p>
        {built.result.notes.length > 0 && (
          <ul className="text-muted pl-3 list-disc" data-testid="vf-template-notes">
            {built.result.notes.map(n => <li key={n}>{n}</li>)}
          </ul>
        )}
      </div>
      {drafts.length > 0 && (
        <fieldset className="space-y-2" data-testid="vf-drafts">
          <legend className="text-[11px] font-semibold">
            Contracts this template needs (saved to the project when you use it)
          </legend>
          {drafts.map(d => (
            <div key={String(d.id)} className="text-[11px] space-y-1">
              <div>{String(d.type)} <code>{String(d.id)}</code></div>
              {draftNeeds(d).map(field => (
                <label key={field} className="flex items-center gap-2">
                  <span className="w-48">{field.replace(/_/g, ' ')}</span>
                  <input type="number" min={0} step="any" className={input}
                         value={values[String(d.id)]?.[field] ?? ''}
                         onChange={e => setValues(v => ({ ...v, [String(d.id)]: {
                           ...(v[String(d.id)] ?? {}), [field]: e.target.value } }))} />
                </label>
              ))}
            </div>
          ))}
        </fieldset>
      )}
      {error && <p role="alert" className="text-[11px] text-danger">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" className="text-[11px] px-2 py-1" onClick={onClose}>Cancel</button>
        <button type="button" disabled={!ready || busy}
                className="text-[11px] px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                onClick={async () => {
                  setBusy(true); setError(null)
                  try { await onUse(cfg, filled as Array<Record<string, unknown>>) }
                  catch (e) { setError(e instanceof Error ? e.message : 'the contracts could not be saved') }
                  finally { setBusy(false) }
                }}>
          {drafts.length ? 'Save contracts and use' : 'Use this template'}
        </button>
      </div>
    </Dialog>
  )
}

export default function ParticipantsDesigner() {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const state = useQuery({ queryKey: nk(project, 'value_flows', 'state'),
                           queryFn: () => commercialApi.getValueFlows() })
  const ctx = useQuery({ queryKey: nk(project, 'value_flows', 'designer'),
                         queryFn: () => commercialApi.getDesigner(), retry: false })
  const [cfg, setCfg] = useState<ValueFlowConfig | null>(null)
  const [digest, setDigest] = useState<string | null>(null)
  const [pick, setPick] = useState<TemplateName>('single_owner')
  const [built, setBuilt] = useState<{ name: TemplateName; result: TemplateResult } | null>(null)
  const [problems, setProblems] = useState<string[]>([])
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [stale, setStale] = useState(false)
  const [saving, setSaving] = useState(false)
  const [newExternal, setNewExternal] = useState('')

  // Start from what the server holds — never from a cache that is being
  // refetched (review #1): once per load; a reload re-seeds explicitly.
  useEffect(() => {
    if (state.data && ctx.data && cfg === null && !state.isFetching) {
      setCfg(initialConfig(state.data, ctx.data)); setDigest(state.data.digest)
    }
  }, [state.data, state.isFetching, ctx.data, cfg])

  const bySection = useMemo(() => {
    const out: Record<Section, string[]> = { participants: [], assets: [], payees: [], hub: [] }
    for (const p of problems) out[problemSection(p)].push(p)
    return out
  }, [problems])

  if (ctx.isError || state.isError) {
    const e = ctx.isError ? ctx.error : state.error
    return <p className="text-[11px] text-muted py-2" data-testid="vf-designer-unavailable">
      {e instanceof NoCommercialConfigError
        ? 'Set up the commercial config (the point of connection) before defining participants.'
        : e instanceof SolverInFlightError ? 'A solve is running; edit the participants after it.'
          : 'The designer could not be loaded.'}</p>
  }
  if (!cfg || !ctx.data) return <p className="text-[11px] text-muted py-2">Loading the designer…</p>
  const c: DesignerContext = ctx.data
  const participants = cfg.participants ?? []
  const siteIndex = participants.findIndex(p => p.id.trim().toLowerCase()
                                                === c.site_party.trim().toLowerCase())
  const hubShown = c.group_members.length > 0 || (cfg.hub_members?.length ?? 0) > 0 || !!cfg.allocation
  const parties = [...participants.map(p => p.id), ...(cfg.externals ?? [])]
  const byBus = new Map<string, DesignerContext['assets']>()
  for (const a of c.assets) byBus.set(a.bus, [...(byBus.get(a.bus) ?? []), a])

  const reload = async () => {
    setStale(false); setProblems([]); setMessage(null)
    // Fetch the CURRENT state (not the cache) and re-seed from it (review #1).
    const fresh = await qc.fetchQuery({ queryKey: nk(project, 'value_flows', 'state'),
                                        queryFn: () => commercialApi.getValueFlows(), staleTime: 0 })
    await qc.invalidateQueries({ queryKey: nk(project, 'value_flows', 'designer') })
    setCfg(initialConfig(fresh, c)); setDigest(fresh.digest)
  }
  const save = async (value: ValueFlowConfig) => {
    if (saving) return
    setSaving(true); setProblems([]); setMessage(null)
    try {
      const out = await commercialApi.putValueFlows(value, digest ?? '')
      setDigest(out.digest)
      setMessage({ tone: 'ok', text: 'Participants saved. Re-read the results for the new ledger.' })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
      await qc.invalidateQueries({ queryKey: nk(project, 'value_flows', 'state') })
    } catch (e) {
      if (e instanceof StaleEditError) { setStale(true); return }
      const list = problemsOf(e)
      if (list) { setProblems(list); return }
      setMessage({ tone: 'error', text: e instanceof SolverInFlightError
        ? 'A solve is running; save after it finishes.' : 'The participants could not be saved.' })
    } finally { setSaving(false) }
  }
  const applyTemplate = async (value: ValueFlowConfig, drafts: Array<Record<string, unknown>>) => {
    if (drafts.length) {
      await commercialApi.appendContracts(drafts)
      // Contracts change the bill, the settlement and the ledger without a
      // re-solve (review #4).
      await qc.invalidateQueries({ queryKey: nk(project, 'value_flows', 'designer') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
      await qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
    }
    setCfg(value); setBuilt(null)
    setMessage({ tone: 'ok', text: drafts.length
      ? `${drafts.length} contract(s) saved to the project. The template is loaded; the participants are not saved yet — review them, then save.`
      : 'Template loaded — review it, then save.' })
  }

  return (
    <div className="space-y-4 text-[11px]" data-testid="vf-designer">
      {stale && (
        <div role="alert" className="text-warn" data-testid="vf-stale">
          The participants changed elsewhere since you opened them.{' '}
          <button type="button" className="underline" onClick={reload}>Reload</button>
        </div>
      )}
      {message && (
        <p role="status" className={message.tone === 'ok' ? 'text-success' : 'text-danger'}>
          {message.text}</p>
      )}
      {state.data?.status === 'value_flows_invalid' && (
        <p role="alert" className="text-warn" data-testid="vf-stored-invalid">
          The stored participants do not validate{state.data.message ? `: ${state.data.message}` : ''}.
          The editor starts afresh; saving replaces the stored config.</p>
      )}

      <section aria-labelledby="vf-d-template" className="flex flex-wrap items-center gap-2">
        <h4 id="vf-d-template" className="font-semibold">Start from a template</h4>
        <label>Template{' '}
          <select className={input} value={pick} onChange={e => setPick(e.target.value as TemplateName)}>
            {TEMPLATES.map(t => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
          </select>
        </label>
        <button type="button" className="underline" onClick={async () => {
          setMessage(null)
          try { setBuilt({ name: pick, result: await commercialApi.buildTemplate(pick) }) }
          catch (e) {
            const d = (e as { response?: { data?: { detail?: { message?: string } } } })?.response
            setMessage({ tone: 'error', text: d?.data?.detail?.message ?? 'The template could not be built.' })
          }
        }}>Build</button>
      </section>

      <section aria-labelledby="vf-d-participants" className="space-y-1">
        <h4 id="vf-d-participants" className="font-semibold">Participants</h4>
        <table className="w-full max-w-xl">
          <thead><tr><th scope="col" className="text-left">Id</th><th scope="col" className="text-left">Name</th>
            <th scope="col" className="text-left">Role</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
          <tbody>
            {participants.map((p, i) => (
              <tr key={i}>
                <td><input aria-label={`Participant ${i + 1} id`} className={input} value={p.id}
                           readOnly={i === siteIndex}
                           title={i === siteIndex ? 'The site party keeps its id' : undefined}
                           onChange={e => setCfg(update(cfg, { participants: participants.map((x, j) =>
                             j === i ? { ...x, id: e.target.value } : x) }))} /></td>
                <td><input aria-label={`Participant ${i + 1} name`} className={input} value={p.name}
                           onChange={e => setCfg(update(cfg, { participants: participants.map((x, j) =>
                             j === i ? { ...x, name: e.target.value } : x) }))} /></td>
                <td><select aria-label={`Participant ${i + 1} role`} className={input} value={p.role}
                            onChange={e => setCfg(update(cfg, { participants: participants.map((x, j) =>
                              j === i ? { ...x, role: e.target.value as ParticipantRole } : x) }))}>
                  {ROLES.map(r => <option key={r} value={r}>{r.replace(/_/g, ' ')}</option>)}
                </select></td>
                <td><button type="button" aria-label={`Remove participant ${p.id || i + 1}`}
                            disabled={i === siteIndex}
                            onClick={() => setCfg(update(cfg, { participants: participants.filter((_, j) => j !== i) }))}>
                  ×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <button type="button" className="underline" onClick={() => setCfg(update(cfg, {
          participants: [...participants, { id: '', name: '', role: 'other' }] }))}>Add participant</button>
        <div className="space-y-1">
          <div>Externals (parties outside the investment case):</div>
          <ul className="flex flex-wrap gap-1">
            {(cfg.externals ?? []).map((x, k) => (
              <li key={`${k}:${x}`} className="border border-border rounded px-1">
                {x}{' '}
                <button type="button" aria-label={`Remove external ${x}`}
                        onClick={() => setCfg(update(cfg, { externals: (cfg.externals ?? []).filter((_, j) => j !== k) }))}>
                  ×</button>
              </li>
            ))}
          </ul>
          <input aria-label="New external" className={input} value={newExternal}
                 onChange={e => setNewExternal(e.target.value)} />
          <button type="button" className="underline ml-1" disabled={!newExternal.trim()}
                  onClick={() => { setCfg(addExternal(cfg, newExternal)); setNewExternal('') }}>
            Add external</button>
          {c.contract_parties.length > 0 && (
            <p className="text-muted">The contracts name: {c.contract_parties.join(', ')}.</p>
          )}
        </div>
        <Problems list={bySection.participants} />
      </section>

      <section aria-labelledby="vf-d-assets" className="space-y-1">
        <h4 id="vf-d-assets" className="font-semibold">Asset owners</h4>
        <p className="text-muted">Unassigned assets belong to {c.site_party}. Grid-side assets are the market's.</p>
        {[...byBus.entries()].map(([bus, list]) => (
          <fieldset key={bus} className="border border-border rounded p-1">
            <legend>Bus {bus}</legend>
            {list.map(a => (
              <label key={`${a.component}:${a.name}`} className="flex items-center gap-2">
                <span className="w-56">{a.component} {a.name}</span>
                {a.ownable ? (
                  <select aria-label={`Owner of ${a.component} ${a.name}`} className={input}
                          value={ownerOf(cfg, a.component, a.name)}
                          onChange={e => setCfg(setOwner(cfg, a.component, a.name, e.target.value))}>
                    <option value="">{c.site_party} (default)</option>
                    {participants.filter(p => p.id).map(p => <option key={p.id} value={p.id}>{p.id}</option>)}
                  </select>
                ) : <span className="text-muted">grid side (market)</span>}
              </label>
            ))}
          </fieldset>
        ))}
        <Problems list={bySection.assets} />
      </section>

      <section aria-labelledby="vf-d-payees" className="space-y-1">
        <h4 id="vf-d-payees" className="font-semibold">Who is paid each tariff item</h4>
        {c.tariff_items.length === 0 && <p className="text-muted">The project has no import tariff.</p>}
        {c.tariff_items.map(it => {
          // Without an item rule the item resolves by kind, then by default.
          const inherited = resolvedPayee({ ...cfg, tariff_payees: (cfg.tariff_payees ?? [])
            .filter(r => r.item_id !== it.id) }, it)
          return (
            <label key={it.id} className="flex items-center gap-2">
              <span className="w-56">{it.id} ({it.kind})</span>
              <select aria-label={`Payee of ${it.id}`} className={input} value={payeeOf(cfg, it.id)}
                      onChange={e => setCfg(setPayee(cfg, it.id, e.target.value))}>
                <option value="">{inherited.payee} ({inherited.by === 'kind' ? `the ${it.kind} rule` : 'default'})</option>
                {parties.filter(Boolean).map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
          )
        })}
        {(cfg.tariff_payees ?? []).filter(r => !r.item_id && r.kind).map(r => (
          <div key={`kind:${r.kind}`} className="flex items-center gap-2" data-testid={`vf-kind-rule-${r.kind}`}>
            <span>Every {r.kind} item is paid to {r.payee}</span>
            <button type="button" className="underline" aria-label={`Remove the ${r.kind} payee rule`}
                    onClick={() => setCfg(removeKindRule(cfg, r.kind!))}>Remove</button>
          </div>
        ))}
        <label className="flex items-center gap-2">
          <span className="w-56">Export revenue goes to</span>
          <select aria-label="Export revenue goes to" className={input}
                  value={cfg.export_revenue_to ?? 'site_party'}
                  onChange={e => setCfg(update(cfg, { export_revenue_to: e.target.value as 'site_party' | 'asset_owner' }))}>
            <option value="site_party">{c.site_party} (the site party)</option>
            <option value="asset_owner">each generator's owner</option>
          </select>
        </label>
        <Problems list={bySection.payees} />
      </section>

      {hubShown && (
        <section aria-labelledby="vf-d-hub" className="space-y-1">
          <h4 id="vf-d-hub" className="font-semibold">Energy hub</h4>
          {c.group_members.length === 0 && (
            <p className="text-warn">The project has no group contract, so hub settings cannot be
              saved.{' '}
              <button type="button" className="underline" onClick={() => setCfg(clearHub(cfg))}>
                Remove hub settings</button></p>
          )}
          {c.group_members.map(link => {
            const m = (cfg.hub_members ?? []).find(x => x.link === link)
            const setMember = (patch: { participant?: string; contracted_mw?: number | null }) => {
              const next = { link, participant: m?.participant ?? '', contracted_mw: m?.contracted_mw ?? null, ...patch }
              if (!next.participant) { setCfg(removeHubMember(cfg, link)); return }
              const rest = (cfg.hub_members ?? []).filter(x => x.link !== link)
              setCfg(update(cfg, { hub_members: [...rest, next] }))
            }
            return (
              <div key={link} className="flex items-center gap-2">
                <span className="w-32">Link {link}</span>
                <select aria-label={`Hub member for ${link}`} className={input} value={m?.participant ?? ''}
                        onChange={e => setMember({ participant: e.target.value })}>
                  <option value="">not a hub member</option>
                  {participants.filter(p => p.id).map(p => <option key={p.id} value={p.id}>{p.id}</option>)}
                </select>
                <label>contracted MW{' '}
                  <input type="number" min={0} step="any" className={input} disabled={!m}
                         aria-label={`Contracted MW for ${link}`} value={m?.contracted_mw ?? ''}
                         onChange={e => setMember({ contracted_mw: e.target.value === '' ? null : Number(e.target.value) })} />
                </label>
              </div>
            )
          })}
          <label className="flex items-center gap-2">
            <span className="w-32">Allocation key</span>
            <select aria-label="Allocation key" className={input} value={cfg.allocation?.basis ?? ''}
                    onChange={e => setCfg(update(cfg, { allocation: e.target.value
                      ? { basis: e.target.value as NonNullable<ValueFlowConfig['allocation']>['basis'] } : null }))}>
              <option value="">none (the hub keeps the bill)</option>
              <option value="energy">energy</option>
              <option value="contracted_capacity">contracted capacity</option>
              <option value="peak_contribution">peak contribution</option>
              <option value="fixed_shares">fixed shares</option>
            </select>
          </label>
          {cfg.allocation?.basis === 'fixed_shares' && (cfg.hub_members ?? []).map(m => (
            <label key={m.participant} className="flex items-center gap-2">
              <span className="w-32">Share of {m.participant}</span>
              <input type="number" min={0} max={1} step="any" className={input}
                     aria-label={`Share of ${m.participant}`}
                     value={cfg.allocation?.shares?.[m.participant] ?? ''}
                     onChange={e => {
                       const shares = { ...(cfg.allocation?.shares ?? {}) }
                       if (e.target.value === '') delete shares[m.participant]
                       else shares[m.participant] = Number(e.target.value)
                       setCfg(update(cfg, { allocation: { basis: 'fixed_shares', shares } }))
                     }} />
            </label>
          ))}
          <Problems list={bySection.hub} />
        </section>
      )}
      {!hubShown && <Problems list={bySection.hub} />}

      <div className="flex gap-2">
        <button type="button" className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
                disabled={saving} onClick={() => save(cfg)}>Save participants</button>
        <button type="button" className="px-2 py-1 underline" onClick={reload}>Discard changes</button>
      </div>

      <TemplateDialog built={built} onClose={() => setBuilt(null)} onUse={applyTemplate} />
    </div>
  )
}
