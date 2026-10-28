// SC-02b: the Model part of Site 3D. A selected building lists what it is
// attached to; "Attach to…" picks a model item (never preselected) and its
// shares, Preview makes a `bind_targets` proposal reviewed like Generate's
// (Apply or Discard). An attachment whose item was deleted is attached again
// through `rebind`, the user picking the item. "Place equipment" puts an
// existing model item on the ground or a solar item on a roof
// (`place_existing`). Every reason is visible text; every "›" link leaves
// through withReturnFromSite3D so "Back to Site 3D" returns.
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import {
  sceneErrorCopy, sceneErrorDetail, type BindPreview, type ModelProposal, type PlaceEntry, type PlacePreview, type RebindPreview,
  type SceneAssociation, type SceneBuilding, type SceneFinding, type SiteScene, type TargetClass,
} from '../api/siteScene'
import { openSectionInExpert, openSectionInGuided, withReturnFromSite3D } from '../layout/flowNavigation'
import type { FlowSectionId } from '../api/flow'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import {
  attachCandidates, buildBindInputs, buildingName, candidateKey, candidatesFor, checkShares, clauseTail, featureName, findingCopy,
  findingObjects, KIND_WORDS, nextTask, pct, placeableItems, placeRefusalCopy, rowCopy, shareFeatures, startingShares, targetDetail,
  targetLabel, type Candidate, type UiMode,
} from './associations'
import { APPROXIMATE_SIZE, roofEligible, roofItem, sizeBasis } from './placementCheck'
import { componentOfKey } from './placementFindingsStore'
import NumberField from './NumberField'
import { applyProposal, discardProposal, propose, SitesNotSaved } from './sceneActions'
import { useModelProposal } from './useSiteScene'
import type { AssetFootprint, Site } from './types'

type Enabled = Extract<SiteScene, { state: 'enabled' }>

const CHOOSE_ATTACH = 'Choose what to attach first'
const CHOOSE_PLACE = 'Choose what to place first'
const FIX_FIELDS = 'Correct the field marked in red to continue'
const NO_CANDIDATES = 'Nothing to attach: the model has no loads, process units, stores, generators or storage units'
const NO_PLACEABLE = 'Nothing to place: the model has no generators, storage units, stores or process units'

/** A "›" home outside Site 3D, in the user's mode (Guided never flips to Expert), with Back to Site 3D. */
function openHome(section: FlowSectionId): void {
  withReturnFromSite3D(() => {
    if (useUIStore.getState().uiMode === 'guided') openSectionInGuided(section)
    else openSectionInExpert(section)
  })
}

const link = 'whitespace-nowrap text-accent hover:underline'
const primary = 'px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50'
const secondary = 'px-3 py-1 rounded border border-border text-text hover:border-accent disabled:opacity-50'

function useNetworkRows(project: string | null) {
  const opts = { enabled: !!project, staleTime: 30_000 }
  const loads = useQuery({ queryKey: nk(project, 'loads'), queryFn: networkApi.getLoads, ...opts })
  const links = useQuery({ queryKey: nk(project, 'links'), queryFn: networkApi.getLinks, ...opts })
  const stores = useQuery({ queryKey: nk(project, 'stores'), queryFn: networkApi.getStores, ...opts })
  const generators = useQuery({ queryKey: nk(project, 'generators'), queryFn: networkApi.getGenerators, ...opts })
  const storageUnits = useQuery({ queryKey: nk(project, 'storage_units'), queryFn: networkApi.getStorageUnits, ...opts })
  return useMemo(() => ({
    loads: loads.data ?? [], links: links.data ?? [], stores: stores.data ?? [],
    generators: generators.data ?? [], storageUnits: storageUnits.data ?? [],
  }), [loads.data, links.data, stores.data, generators.data, storageUnits.data])
}

/** Select both objects a finding names: the item in the scene and the building. */
function selectFinding(f: SceneFinding, buildings: readonly SceneBuilding[]): void {
  const { items, buildings: ids } = findingObjects(f, buildings)
  const ui = useUIStore.getState()
  if (ids[0]) ui.setSitePartSelection({ kind: 'building', id: ids[0] })
  if (items[0]) {
    ui.setSelectedComponent(componentOfKey(items[0]))
    ui.setHighlightedComponent(componentOfKey(items[1] ?? items[0]) as never)
  }
}

interface ErrorState { copy: string; code?: string; candidates?: string[] }

interface PlaceDraft { key: string; on: 'ground' | 'roof'; roofOf: string | null; x: number; y: number; heading: number }
const emptyPlace: PlaceDraft = { key: '', on: 'ground', roofOf: null, x: 0, y: 0, heading: 0 }

const centroid = (ring: readonly [number, number][]): [number, number] => {
  const n = ring.length || 1
  return [ring.reduce((s, p) => s + p[0], 0) / n, ring.reduce((s, p) => s + p[1], 0) / n]
}

export default function AssociationPanel({ site, scene, canWrite, blockedReason }: {
  site: Site
  scene: Enabled
  canWrite: boolean
  /** Why writing is off (read-only, busy elsewhere), shown where a button is disabled. */
  blockedReason: string | null
}) {
  const project = useUIStore(s => s.currentProject)
  const mode = useUIStore(s => s.uiMode) as UiMode
  const selection = useUIStore(s => s.sitePartSelection)
  const qc = useQueryClient()
  const proposal = useModelProposal(s => s.proposal)
  const setProposal = useModelProposal(s => s.setProposal)
  const addAssetFrom = useModelProposal(s => s.addAssetFrom)
  const setAddAssetFrom = useModelProposal(s => s.setAddAssetFrom)
  const rows = useNetworkRows(project)
  const [target, setTarget] = useState<Candidate | null>(null)
  const [shares, setShares] = useState<Record<string, number | null>>({})
  const [rebinding, setRebinding] = useState<SceneAssociation | null>(null)
  const [place, setPlace] = useState<PlaceDraft>(emptyPlace)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ErrorState | null>(null)

  const buildings = scene.buildings
  const associations = scene.associations ?? []
  const targets = scene.targets ?? []
  const groups = scene.groups ?? []
  const placements = ((scene.site as { placements?: Record<string, PlaceEntry> }).placements) ?? {}
  const footprints = scene.asset_footprints as Record<string, AssetFootprint | undefined>
  const selected = selection?.kind === 'building' ? buildings.find(b => b.id === selection.id) ?? null : null
  const mine = proposal && proposal.site_id === site.id ? proposal : null
  const all = useMemo(() => attachCandidates(rows), [rows])
  const placeable = useMemo(() => placeableItems(rows), [rows])
  const writable = canWrite && !busy
  const bName = (id: string) => buildingName(id, buildings)
  const itemName = (key: string) => {
    const c = componentOfKey(key)
    return c.type in KIND_WORDS ? targetLabel(c.type as TargetClass, c.name, mode) : c.name
  }

  const run = async (fn: () => Promise<void>, refusal?: (code: string | undefined) => string | null) => {
    if (!project) return
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      const d = sceneErrorDetail(e)
      if (['proposal_expired', 'proposal_cancelled'].includes(d?.error_kind ?? '')) setProposal(null)
      const copy = e instanceof SitesNotSaved ? e.message : (refusal?.(d?.code) ?? sceneErrorCopy(e))
      setError({ copy, code: d?.code, candidates: d?.code === 'ambiguous_target' ? d.candidates : undefined })
    } finally { setBusy(false) }
  }

  // ── attach ────────────────────────────────────────────────────────────────
  const feature = selected?.id ?? null
  const mineRows = feature ? associations.filter(a => a.feature === feature) : []
  const pickList = rebinding
    ? all.filter(c => c.class === rebinding.class)
    : feature ? candidatesFor(feature, all, associations) : []
  const features = target && feature && !rebinding ? shareFeatures(associations, target, feature) : []
  const shareCheck = checkShares(shares, features)
  const shareBlocks = features.length > 1 && ['partial', 'off', 'invalid'].includes(shareCheck.state)
  const attachReason = !target ? CHOOSE_ATTACH : shareBlocks ? shareCheck.copy : !canWrite ? blockedReason : null

  const choose = (key: string) => {
    const c = pickList.find(x => candidateKey(x) === key) ?? null
    setTarget(c)
    setShares(c && feature && !rebinding ? startingShares(associations, c, shareFeatures(associations, c, feature)) : {})
  }
  const resetAttach = () => { setTarget(null); setShares({}); setRebinding(null) }
  const previewAttach = () => run(async () => {
    if (!target || !feature) return
    const p = rebinding
      ? { ...(await propose<RebindPreview>(qc, project!, site.id, 'rebind',
          { association: rebinding.id, target: { class: target.class, name: target.name }, confirmed: true })), action: 'rebind' as const }
      : { ...(await propose<BindPreview>(qc, project!, site.id, 'bind_targets',
          buildBindInputs(associations, { feature, target, shares }, groups))), action: 'bind_targets' as const }
    setProposal(p as ModelProposal)
  })
  const startRebind = (a: SceneAssociation) => { setError(null); setTarget(null); setShares({}); setRebinding(a) }

  // ── place ─────────────────────────────────────────────────────────────────
  const placeItem = placeable.find(c => candidateKey(c) === place.key) ?? null
  const roofOk = !!selected && roofEligible(selected)
  const solar = !!placeItem && roofItem(placeItem.class, placeItem.carrier)
  const roofReason = !selected ? 'Roof: select a building first'
    : !roofOk ? `Roof: ${bName(selected.id)} has no height to stand on`
      : placeItem && !solar ? 'Roof: only solar panels go on a roof' : null
  const numbersOk = [place.x, place.y, place.heading].every(Number.isFinite)
  const placeReason = !placeItem ? CHOOSE_PLACE : !numbersOk ? FIX_FIELDS : !canWrite ? blockedReason : null
  const roofBuilding = place.on === 'roof' ? place.roofOf : null
  const setOn = (on: string) => {
    if (on === 'ground') { setPlace(p => ({ ...p, on: 'ground', roofOf: null })); return }
    if (!selected) return
    const [x, y] = centroid(selected.footprint)
    setPlace(p => ({ ...p, on: 'roof', roofOf: selected.id, x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 }))
  }
  const previewPlace = () => run(async () => {
    if (!placeItem) return
    const entry: PlaceEntry = { x: place.x, y: place.y, heading: place.heading, on: roofBuilding ? `roof:${roofBuilding}` : 'ground' }
    const p = await propose<PlacePreview>(qc, project!, site.id, 'place_existing', { placements: { [place.key]: entry } })
    setProposal({ ...p, action: 'place_existing' })
  }, code => placeRefusalCopy(code, placeItem ? targetLabel(placeItem.class, placeItem.name, mode) : 'this item',
    roofBuilding ? bName(roofBuilding) : null))

  // ── review ────────────────────────────────────────────────────────────────
  const apply = () => run(async () => {
    await applyProposal(qc, project!, mine!)
    setProposal(null); resetAttach(); setPlace(emptyPlace)
  })
  const discard = () => run(async () => {
    try { await discardProposal(project!, mine!) } catch (e) {
      if (sceneErrorDetail(e)?.error_kind !== 'proposal_expired') throw e
    } finally { setProposal(null) }
  })

  // ── add model asset → place it ────────────────────────────────────────────
  const addAsset = () => {
    setAddAssetFrom(placeable.map(candidateKey))
    openHome('assets')
  }
  const fresh = addAssetFrom
    ? placeable.find(c => !addAssetFrom.includes(candidateKey(c)) && !(candidateKey(c) in placements)) ?? null
    : null
  const placeIt = (c: Candidate) => { setAddAssetFrom(null); setPlace({ ...emptyPlace, key: candidateKey(c) }) }

  const sizeOf = (key: string) => footprints[key]
  const approx = (key: string) => sizeBasis(sizeOf(key)) === 'approximate'

  return (
    <div className="flex flex-col gap-2" data-testid="association-panel">
      {mode === 'guided' && (
        <span className="text-text font-medium" data-testid="scene-next-task">Next: {nextTask(associations)}</span>
      )}

      {mine ? (
        <Review proposal={mine} mode={mode} buildings={buildings} groups={groups} itemName={itemName} approx={approx}
          sized={key => !!sizeOf(key)} onApply={() => void apply()} onDiscard={() => void discard()}
          canApply={writable} applyReason={!canWrite ? blockedReason : null} busy={busy} />
      ) : (
        <>
          {selected ? (
            <div className="flex flex-col gap-1.5" data-testid="assoc-building">
              <span className="text-text font-medium">{bName(selected.id)}</span>
              {mineRows.length === 0 && <span className="text-[11px] text-muted" data-testid="assoc-none">Not attached to anything yet</span>}
              <ul className="flex flex-col gap-1">
                {mineRows.map(a => {
                  const canRebind = all.some(c => c.class === a.class)
                  const copy = rowCopy(a, mode, canRebind)
                  const t = a.status === 'bound' ? targets.find(x => x.class === a.class && x.name === a.name) : undefined
                  const detail = t && mode === 'expert' ? targetDetail(t, buildings, groups) : null
                  return (
                    <li key={a.id} data-testid={`assoc-row-${a.id}`} data-status={a.status}
                      className={`text-[11px] ${copy.tone === 'warn' ? 'text-warn' : copy.tone === 'ok' ? 'text-text' : 'text-muted'}`}>
                      {copy.text}
                      {copy.fix === 'attach_again' && (
                        <> <button type="button" data-testid={`assoc-fix-${a.id}`} className={link} disabled={!writable}
                          onClick={() => startRebind(a)}>› Attach again</button></>
                      )}
                      {copy.fix === 'loads' && (
                        <> <button type="button" data-testid={`assoc-fix-${a.id}`} className={link} onClick={() => openHome('loads')}>› Loads</button></>
                      )}
                      {detail && (
                        <span className="block text-muted" data-testid={`assoc-target-detail-${a.id}`}>
                          {[detail.enclosures, detail.capacity].join(' · ')}
                          {detail.shares.map(s => <span key={s} className="block">{s}</span>)}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>

              {rebinding && (
                <span className="text-[11px] text-text" data-testid="assoc-rebind">
                  Attach again: choose the {mode === 'guided' ? KIND_WORDS[rebinding.class] : rebinding.class} to use instead of {rebinding.name_at_bind ?? 'the old item'}
                </span>
              )}
              <label className="flex flex-col gap-0.5 text-[11px]">
                <span className="text-muted">Attach to…</span>
                <select data-testid="assoc-attach-picker" value={target ? candidateKey(target) : ''} disabled={!writable || pickList.length === 0}
                  onChange={e => choose(e.target.value)}
                  className="rounded border border-border bg-bg px-2 py-1 text-[12px] text-text disabled:opacity-50">
                  <option value="" disabled>Choose an item</option>
                  {pickList.map(c => (
                    <option key={candidateKey(c)} value={candidateKey(c)}>{targetLabel(c.class, c.name, mode)}</option>
                  ))}
                </select>
              </label>
              {pickList.length === 0 && (
                <span className="text-[11px] text-muted" data-testid="assoc-attach-reason">
                  {all.length === 0 ? NO_CANDIDATES : 'Nothing left to attach: this building is attached to every item in the model'}
                </span>
              )}

              {features.length > 1 && (
                <div className="flex flex-col gap-1" data-testid="assoc-shares">
                  <span className="text-[11px] text-muted">Shares of {target ? targetLabel(target.class, target.name, mode) : ''} (optional)</span>
                  <div className="flex flex-wrap gap-2">
                    {features.map(f => (
                      <NumberField key={f} label={featureName(f, buildings, groups)} unit="%" width="w-16" testId={`assoc-share-${f}`}
                        value={shares[f] ?? NaN} disabled={!writable}
                        onChange={v => setShares(s => ({ ...s, [f]: Number.isFinite(v) ? v : null }))} />
                    ))}
                  </div>
                  {shareCheck.copy && (
                    <span data-testid="assoc-share-total" role={shareBlocks ? 'alert' : undefined}
                      className={`text-[11px] ${shareBlocks ? 'text-warn' : 'text-muted'}`}>{shareCheck.copy}</span>
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <button type="button" data-testid="assoc-preview" className={primary} disabled={!writable || !!attachReason}
                  onClick={() => void previewAttach()}>Preview</button>
                {(target || rebinding) && (
                  <button type="button" data-testid="assoc-cancel" className={secondary} onClick={resetAttach}>Cancel</button>
                )}
              </div>
              {attachReason && <span className="text-[11px] text-muted" data-testid="assoc-preview-reason">{attachReason}</span>}
            </div>
          ) : (
            <span className="text-[11px] text-muted" data-testid="assoc-select-hint">
              Select a building in the scene to attach it · {associations.filter(a => a.status === 'bound').length} attached
            </span>
          )}

          <div className="flex flex-col gap-1.5 border-t border-border pt-2" data-testid="place-form">
            <span className="text-text font-medium">Place equipment</span>
            {fresh && (
              <span className="text-[11px] text-text" data-testid="place-it">
                {targetLabel(fresh.class, fresh.name, mode)} is in the model and not placed{' '}
                <button type="button" data-testid="place-it-link" className={link} onClick={() => placeIt(fresh)}>› Place it</button>
              </span>
            )}
            <label className="flex flex-col gap-0.5 text-[11px]">
              <span className="text-muted">Item</span>
              <select data-testid="place-item" value={place.key} disabled={!writable || placeable.length === 0}
                onChange={e => setPlace(p => ({ ...p, key: e.target.value, on: 'ground', roofOf: null }))}
                className="rounded border border-border bg-bg px-2 py-1 text-[12px] text-text disabled:opacity-50">
                <option value="" disabled>Choose an item</option>
                {placeable.map(c => (
                  <option key={candidateKey(c)} value={candidateKey(c)}>
                    {targetLabel(c.class, c.name, mode)}{candidateKey(c) in placements ? ' · placed' : ''}
                  </option>
                ))}
              </select>
            </label>
            {placeable.length === 0 && <span className="text-[11px] text-muted" data-testid="place-item-reason">{NO_PLACEABLE}</span>}
            {placeItem && (
              <>
                {approx(place.key) && <span className="text-[11px] text-muted" data-testid="place-size">{APPROXIMATE_SIZE}</span>}
                {!sizeOf(place.key) && (
                  <span className="text-[11px] text-muted" data-testid="place-no-rule">
                    No rule checked: overlap with buildings uses the centre point only (no size entered for {placeItem.name})
                  </span>
                )}
                <label className="flex flex-col gap-0.5 text-[11px]">
                  <span className="text-muted">Stands on</span>
                  <select data-testid="place-on" value={place.on} disabled={!writable} onChange={e => setOn(e.target.value)}
                    className="rounded border border-border bg-bg px-2 py-1 text-[12px] text-text disabled:opacity-50">
                    <option value="ground">Ground</option>
                    {selected && roofOk && solar && <option value="roof">Roof of {bName(selected.id)}</option>}
                  </select>
                </label>
                {roofReason && place.on === 'ground' && <span className="text-[11px] text-muted" data-testid="place-on-reason">{roofReason}</span>}
                <div className="flex flex-wrap items-end gap-2">
                  <NumberField label="East" unit="m" width="w-16" value={place.x} testId="place-x" disabled={!writable}
                    error={Number.isFinite(place.x) ? null : 'Enter a number'} onChange={v => setPlace(p => ({ ...p, x: v }))} />
                  <NumberField label="North" unit="m" width="w-16" value={place.y} testId="place-y" disabled={!writable}
                    error={Number.isFinite(place.y) ? null : 'Enter a number'} onChange={v => setPlace(p => ({ ...p, y: v }))} />
                  <NumberField label="Heading" unit="°" width="w-16" value={place.heading} testId="place-heading" disabled={!writable}
                    error={Number.isFinite(place.heading) ? null : 'Enter a number'} onChange={v => setPlace(p => ({ ...p, heading: v }))} />
                </div>
              </>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" data-testid="place-preview" className={primary} disabled={!writable || !!placeReason}
                onClick={() => void previewPlace()}>Preview</button>
              <button type="button" data-testid="add-asset-link" className={link} onClick={addAsset}>› Add model asset</button>
            </div>
            {placeReason && <span className="text-[11px] text-muted" data-testid="place-preview-reason">{placeReason}</span>}
          </div>
        </>
      )}

      {error && (
        <div role="alert" data-testid="assoc-error" className="text-[11px] text-danger">
          {error.copy}
          {error.candidates && error.candidates.length > 0 && (
            <span className="flex flex-wrap gap-1 mt-1" data-testid="assoc-candidates">
              {error.candidates.map(name => (
                <button key={name} type="button" data-testid={`assoc-candidate-${name}`} className={secondary}
                  onClick={() => { setError(null); setTarget({ class: 'Load', name, carrier: null }) }}>
                  {targetLabel('Load', name, mode)}
                </button>
              ))}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function Review({ proposal, mode, buildings, groups, itemName, approx, sized, onApply, onDiscard, canApply, applyReason, busy }: {
  proposal: ModelProposal
  mode: UiMode
  buildings: readonly SceneBuilding[]
  groups: readonly { id: string; label: string; members: string[] }[]
  itemName: (key: string) => string
  approx: (key: string) => boolean
  sized: (key: string) => boolean
  onApply: () => void
  onDiscard: () => void
  canApply: boolean
  applyReason: string | null
  busy: boolean
}) {
  const fName = (f: string) => featureName(f, buildings, groups)
  const bName = (id: string) => buildingName(id, buildings)
  const lines: { key: string; text: string; note?: string[] }[] = []
  if (proposal.action === 'bind_targets') {
    const changed = proposal.preview.associations.filter(r => r.status !== 'kept')
    for (const [i, r] of changed.entries()) {
      const what = r.name ? targetLabel(r.class, r.name, mode) : `a ${r.carrier ?? ''} load`.replace('  ', ' ')
      const share = r.weight != null ? ` · ${pct(r.weight * 100)}` : ''
      lines.push({ key: `b${i}`, text: r.status === 'unavailable'
        ? `Unavailable for ${fName(r.feature)}: ${r.reason ? clauseTail(r.reason) : 'no such load in the model'}`
        : `Attach ${fName(r.feature)} to ${what}${share}` })
    }
    const kept = proposal.preview.associations.length - changed.length
    if (kept > 0) lines.push({ key: 'kept', text: `${kept} other ${kept === 1 ? 'attachment stays' : 'attachments stay'} as they are` })
  } else if (proposal.action === 'rebind') {
    const p = proposal.preview
    const what = targetLabel(p.class as TargetClass, p.name, mode)
    lines.push({ key: 'r', text: `Attach ${p.feature ? fName(p.feature) : 'it'} to ${what} again${p.was ? ` · was ${p.was}` : ''}` })
  } else {
    for (const [key, e] of Object.entries(proposal.preview.placements)) {
      const where = e.on && e.on.startsWith('roof:') ? `on the roof of ${bName(e.on.slice(5))}` : 'on the ground'
      const note = [approx(key) ? APPROXIMATE_SIZE : null,
        sized(key) ? null : 'No rule checked: overlap with buildings uses the centre point only'].filter((x): x is string => !!x)
      lines.push({ key, text: `${itemName(key)} · ${where} · ${e.x.toLocaleString('en-US')} m east, ${e.y.toLocaleString('en-US')} m north · heading ${e.heading}°`, note })
    }
  }
  const findings = proposal.preview.findings ?? []
  const names = { item: itemName, building: bName }
  return (
    <div className="flex flex-col gap-1.5" data-testid="assoc-proposal" data-action={proposal.action}>
      <span className="text-text">Preview: nothing is saved until Apply</span>
      <ul className="flex flex-col gap-0.5 text-[11px] text-text">
        {lines.map(l => (
          <li key={l.key} data-testid={`assoc-proposal-line-${l.key}`}>
            {l.text}
            {l.note?.map(n => <span key={n} className="block text-muted" data-testid={n === APPROXIMATE_SIZE ? 'proposal-size' : 'proposal-no-rule'}>{n}</span>)}
          </li>
        ))}
      </ul>
      {findings.length > 0 && (
        <ul className="flex flex-col gap-0.5" aria-label="Findings">
          {findings.map((f, i) => (
            <li key={`${f.code}:${i}`}>
              <button type="button" data-testid={`assoc-finding-${i}`} data-code={f.code}
                className={`text-left text-[11px] hover:underline ${f.severity === 'warn' ? 'text-warn' : 'text-muted'}`}
                onClick={() => selectFinding(f, buildings)}>
                {findingCopy(f, names)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {findings.length === 0 && proposal.action === 'place_existing' && (
        <span className="text-[11px] text-muted" data-testid="assoc-no-findings">No overlap with buildings found</span>
      )}
      <div className="flex gap-2">
        <button type="button" data-testid="assoc-apply" className={primary} disabled={!canApply} onClick={onApply}>Apply</button>
        <button type="button" data-testid="assoc-discard" className={secondary} disabled={busy} onClick={onDiscard}>Discard</button>
      </div>
      {applyReason && <span className="text-[11px] text-muted" data-testid="assoc-apply-reason">{applyReason}</span>}
    </div>
  )
}
