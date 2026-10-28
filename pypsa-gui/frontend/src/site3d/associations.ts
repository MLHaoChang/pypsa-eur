// SC-02b: the Model part's pure rules. Which model items a building can be
// attached to, the bind_targets inputs built from the current attachments
// plus the user's one change, shares as % with their total, each row's copy
// and fix, and the words findings use. No React: the panel stays thin.
//
// Wording (UX SC-02 plan, conditions 1, 5, 6): names, never `Class:name`
// refs; "·" between data; a colon between clauses; "›" only on links; one
// verb, Attach.
import type { Generator, Link, Load, StorageUnit, Store } from '../api/types'
import {
  plainMessage, type BindInputs, type BindRow, type SceneAssociation, type SceneBuilding, type SceneFinding,
  type SceneGroup, type SceneQuantity, type SceneTarget, type TargetClass,
} from '../api/siteScene'
import { carrierDisplayName } from '../pages/results/carrierAliases'
import type { NativeObstacle } from './placementCheck'
import { unitLabel } from '../utils/assetForm'

/**
 * The kind of a target in words: the backend's own `KIND_WORDS`
 * (`services/site_scene/associations.py`, `_label`), which its refusal and
 * finding messages use, with a process Link as "process unit". Parity test
 * in associations.test.ts.
 */
export const KIND_WORDS: Record<TargetClass, string> = {
  Load: 'load', Link: 'process unit', Store: 'store', Generator: 'generator', StorageUnit: 'storage unit',
}

export type UiMode = 'guided' | 'expert'

/** "cooling_1 · load" in Guided; "cooling_1 · Load" in Expert. */
export const targetLabel = (cls: TargetClass, name: string, mode: UiMode): string =>
  `${name} · ${mode === 'guided' ? KIND_WORDS[cls] : cls}`

export const carrierWords = (carrier: string | null | undefined): string | null =>
  carrier ? carrierDisplayName(carrier.trim().toLowerCase()) : null

// ── candidates ──────────────────────────────────────────────────────────────

/** A model item the user can pick: a Load, a process Link, a Store, a Generator or a StorageUnit. */
export interface Candidate { class: TargetClass; name: string; carrier: string | null }

export const candidateKey = (c: Pick<Candidate, 'class' | 'name'>): string => `${c.class}:${c.name}`

export interface NetworkRows {
  loads?: readonly Load[]
  links?: readonly Link[]
  stores?: readonly Store[]
  generators?: readonly Generator[]
  storageUnits?: readonly StorageUnit[]
}

/** `eh_process` is a lazy Link column: absent, null or false is an ordinary Link (backend `_is_process`). */
export const isProcessLink = (l: Link): boolean => {
  const v = (l as unknown as { eh_process?: unknown }).eh_process
  return v === true || v === 1 || v === 'true' || v === 'True'
}

const ORDER: TargetClass[] = ['Load', 'Link', 'Store', 'Generator', 'StorageUnit']

/** Everything a building can be attached to, by kind, then name. */
export function attachCandidates(rows: NetworkRows): Candidate[] {
  const out: Candidate[] = []
  const add = (cls: TargetClass, list: readonly { name: string; carrier?: string }[] | undefined) => {
    for (const r of list ?? []) out.push({ class: cls, name: r.name, carrier: r.carrier ?? null })
  }
  add('Load', rows.loads)
  add('Link', (rows.links ?? []).filter(isProcessLink))
  add('Store', rows.stores)
  add('Generator', rows.generators)
  add('StorageUnit', rows.storageUnits)
  return out.sort((a, b) => ORDER.indexOf(a.class) - ORDER.indexOf(b.class) || a.name.localeCompare(b.name))
}

/** Every model item of one class (any Link, not only process units): the picker for re-attaching a placed key. */
export function sameClassItems(rows: NetworkRows, cls: string): Candidate[] {
  const list: Record<TargetClass, readonly { name: string; carrier?: string }[] | undefined> = {
    Load: rows.loads, Link: rows.links, Store: rows.stores, Generator: rows.generators, StorageUnit: rows.storageUnits,
  }
  if (!(cls in list)) return []
  return (list[cls as TargetClass] ?? []).map(r => ({ class: cls as TargetClass, name: r.name, carrier: r.carrier ?? null }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

/** What can be placed as equipment: generators, storage units, stores and process units. */
export const placeableItems = (rows: NetworkRows): Candidate[] =>
  attachCandidates({ ...rows, loads: [] })

/** The picker's list for one building: every candidate not already attached to it. */
export function candidatesFor(feature: string, all: readonly Candidate[], associations: readonly SceneAssociation[]): Candidate[] {
  const taken = new Set(associations.filter(a => a.feature === feature && a.status === 'bound' && a.name)
    .map(a => candidateKey({ class: a.class, name: a.name! })))
  return all.filter(c => !taken.has(candidateKey(c)))
}

// ── names ───────────────────────────────────────────────────────────────────

/** How the user knows a building: "OSM building 7", else "Building n" in scene order. */
export function buildingName(id: string, buildings: readonly SceneBuilding[]): string {
  const i = buildings.findIndex(b => b.id === id)
  const b = buildings[i]
  if (b?.source.namespace === 'osm') return `OSM building ${b.source.source_id}`
  return i >= 0 ? `Building ${i + 1}` : 'A building no longer in the scene'
}

/** A feature (building, group or placement key) in words. */
export function featureName(feature: string, buildings: readonly SceneBuilding[], groups: readonly SceneGroup[] = []): string {
  if (buildings.some(b => b.id === feature)) return buildingName(feature, buildings)
  const g = groups.find(x => x.id === feature)
  if (g) return g.label
  const i = feature.indexOf(':')
  return i > 0 ? feature.slice(i + 1) : feature
}

/** A server reason after a colon: a leading sentence word in lower case, a name kept as written. */
export function clauseTail(reason: string): string {
  const text = plainMessage(reason.trim())
  return /^(No|Only|This|That|The|A|An|Several|Each|Give|Not)\b/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text
}

// ── rows ────────────────────────────────────────────────────────────────────

export type RowFix = 'attach_again' | 'loads' | null
export interface RowCopy { text: string; fix: RowFix; tone: 'ok' | 'warn' | 'muted' }

export const NOT_CHECKED_COPY = 'Not checked: open the project’s model to check attachments'

/**
 * One attachment's line. Bound: its label, carrier and unit. Unbound: "Not
 * attached: …" with "› Attach again" only when a same-kind item exists to
 * re-attach to. Unavailable: "Unavailable: …" with "› Loads". Not checked:
 * no link (nothing to fix from here).
 */
export function rowCopy(a: SceneAssociation, mode: UiMode, canRebind: boolean): RowCopy {
  if (a.status === 'bound' && a.name) {
    const data = [targetLabel(a.class, a.name, mode), carrierWords(a.carrier), a.unit ? unitLabel(a.unit) : null]
    return { text: data.filter(Boolean).join(' · '), fix: null, tone: 'ok' }
  }
  if (a.status === 'unbound') {
    const why = a.reason ? clauseTail(a.reason) : `${a.name_at_bind ?? 'the item'} was deleted or replaced`
    return { text: `Not attached: ${why}`, fix: canRebind ? 'attach_again' : null, tone: 'warn' }
  }
  if (a.status === 'unavailable') {
    const why = a.reason ? clauseTail(a.reason) : `no ${carrierWords(a.carrier) ?? ''} load in the model`.replace('  ', ' ')
    return { text: `Unavailable: ${why}`, fix: 'loads', tone: 'warn' }
  }
  return { text: NOT_CHECKED_COPY, fix: null, tone: 'muted' }
}

// ── shares ──────────────────────────────────────────────────────────────────

/** The buildings a target would be split over once `feature` is attached to it, in their order. */
export function shareFeatures(associations: readonly SceneAssociation[], target: Pick<Candidate, 'class' | 'name'>, feature: string): string[] {
  const out = associations.filter(a => a.status === 'bound' && a.class === target.class && a.name === target.name).map(a => a.feature)
  if (!out.includes(feature)) out.push(feature)
  return [...new Set(out)]
}

/** Existing shares as % (null where none is set) for the features of a target. */
export function startingShares(associations: readonly SceneAssociation[], target: Pick<Candidate, 'class' | 'name'>, features: readonly string[]): Record<string, number | null> {
  const out: Record<string, number | null> = {}
  for (const f of features) {
    const row = associations.find(a => a.status === 'bound' && a.class === target.class && a.name === target.name && a.feature === f)
    // Unrounded: 1/3 each must still total 100 % (round only where it is shown).
    out[f] = row?.weight != null ? row.weight * 100 : null
  }
  return out
}

const round1 = (v: number) => Math.round(v * 10) / 10
export const pct = (v: number): string => `${round1(v).toLocaleString('en-US')} %`

export type ShareState = 'none' | 'partial' | 'ok' | 'off' | 'invalid'
export interface ShareCheck { state: ShareState; total: number | null; copy: string | null }

/**
 * Shares are optional: none (the target is not split), or one per building
 * above 0 totalling 100 % (the backend's tolerance). Partial, invalid or a
 * total other than 100 % is a visible finding that blocks Preview.
 */
export function checkShares(shares: Record<string, number | null>, features: readonly string[]): ShareCheck {
  const vals = features.map(f => shares[f] ?? null)
  const given = vals.filter((v): v is number => v !== null)
  if (given.length === 0) return { state: 'none', total: null, copy: features.length > 1 ? 'Not split: every building counts the whole item' : null }
  if (given.some(v => !Number.isFinite(v) || v <= 0)) return { state: 'invalid', total: null, copy: 'Each share must be a number above 0 %' }
  const total = given.reduce((s, v) => s + v, 0)
  if (given.length < vals.length) return { state: 'partial', total, copy: 'Give every building a share, or none' }
  // The backend's own test on exactly the fractions sent: abs(sum - 1) <= 1e-9.
  const fractionTotal = given.reduce((s, v) => s + v / 100, 0)
  if (Math.abs(fractionTotal - 1) > 1e-9) {
    // A total that rounds to 100 % but is not shows its digits, so the copy never reads "Total 100 %: … add up to 100 %".
    const shown = round1(total) === 100 ? `${total.toLocaleString('en-US', { maximumFractionDigits: 6 })} %` : pct(total)
    return { state: 'off', total, copy: `Total ${shown}: shares should add up to 100 %` }
  }
  return { state: 'ok', total, copy: 'Total 100 %' }
}

// ── bind_targets inputs ─────────────────────────────────────────────────────

export interface AttachChange {
  feature: string
  target: Candidate
  /** % per feature of the target; all null means not split. */
  shares: Record<string, number | null>
}

/**
 * The full `bind_targets` list: every current association kept by `{id}`,
 * except the target's own rows when its split changes (restated with their
 * new weight, or none), plus the new row. Groups are sent back unchanged.
 */
export function buildBindInputs(associations: readonly SceneAssociation[], change: AttachChange, groups: readonly SceneGroup[] = []): BindInputs {
  const { feature, target, shares } = change
  const features = shareFeatures(associations, target, feature)
  const split = checkShares(shares, features).state === 'ok'
  const weightOf = (f: string): { weight?: number } => (split ? { weight: (shares[f] as number) / 100 } : {})
  const ofTarget = (a: SceneAssociation) => a.status === 'bound' && a.class === target.class && a.name === target.name
  const touched = associations.some(a => ofTarget(a) && (split || a.weight != null))
  const rows: BindRow[] = associations.map(a => (touched && ofTarget(a)
    ? { feature: a.feature, target: { class: a.class, name: target.name }, ...weightOf(a.feature) }
    : { id: a.id }))
  rows.push({ feature, target: { class: target.class, name: target.name }, ...weightOf(feature) })
  return { associations: rows, groups: groups.map(g => ({ ...g, members: [...g.members] })) }
}

// ── the next task (Guided) ──────────────────────────────────────────────────

export const TASK_ATTACH = 'Attach buildings to loads and processes'
export const TASK_PLACE = 'Place equipment'

/** "Attach…" until the user has attached some building (or every one); then "Place equipment". */
export function nextTask(associations: readonly SceneAssociation[]): string {
  // "Every building attached" implies "some attached", so one test covers both.
  return associations.some(a => a.status === 'bound') ? TASK_PLACE : TASK_ATTACH
}

// ── Expert detail: capacity once, enclosures, allocation ────────────────────

const REASON_WORDS: Record<string, string> = { time_varying_demand: 'varies over time' }

export function quantityCopy(q: SceneQuantity): string {
  if (q.available && q.value !== null) return `${q.value.toLocaleString('en-US', { maximumFractionDigits: 3 })} ${unitLabel(q.unit)}`
  return q.reason ? (REASON_WORDS[q.reason] ?? q.reason.replace(/_/g, ' ')) : 'not known'
}

export interface TargetDetail { enclosures: string; capacity: string; shares: string[] }

/** "Enclosures 2", "Capacity 3 MW", and each share as "Building 1: 40 % · 1.2 MW allocated" (or "Not split"). */
export function targetDetail(t: SceneTarget, buildings: readonly SceneBuilding[], groups: readonly SceneGroup[] = []): TargetDetail {
  const shares = t.allocation.basis === 'explicit'
    ? t.allocation.shares.map(s => `${featureName(s.feature, buildings, groups)}: ${pct(s.weight * 100)} · ${quantityCopy(s.allocated)} allocated`)
    : t.enclosures > 1 ? ['Not split: each building counts the whole item'] : []
  return { enclosures: `Enclosures ${t.enclosures}`, capacity: `Capacity ${quantityCopy(t.capacity)}`, shares }
}

// ── findings and refusals in the user's words (UX SC-02b note 3) ────────────

export interface Names { item: (key: string) => string; building: (id: string) => string }

/** A preview finding: "Overlaps OSM building 7"; others as the server words them. */
export function findingCopy(f: SceneFinding, names: Names): string {
  if (f.code === 'overlap' && f.objects && f.objects.length >= 2) return `Overlaps ${names.building(f.objects[1])}`
  return plainMessage(f.message)
}

/** The scene objects a finding names: placement keys and building ids, so a click selects both. */
export function findingObjects(f: SceneFinding, buildings: readonly SceneBuilding[]): { items: string[]; buildings: string[] } {
  const ids = new Set(buildings.map(b => b.id))
  const all = f.objects ?? []
  return { items: all.filter(o => !ids.has(o)), buildings: all.filter(o => ids.has(o)) }
}

/** A refused placement: off_roof and not_roof_eligible in plain words; null for other codes. */
export function placeRefusalCopy(code: string | undefined, item: string, building: string | null): string | null {
  if (code === 'off_roof') return `Off the roof of ${building ?? 'the building'}`
  if (code === 'not_roof_eligible') return `Can’t go on a roof: ${item}`
  return null
}

// ── the 3D scene's inputs (SiteCanvas) ──────────────────────────────────────

/** The accepted buildings as obstacles and roofs for the layout and the check, named as the user knows them. */
export const sceneObstacles = (buildings: readonly SceneBuilding[]): NativeObstacle[] =>
  buildings.map(b => ({ id: b.id, label: buildingName(b.id, buildings), footprint: b.footprint, height_m: b.height_m }))

type Spot = { x: number; y: number; heading: number; on?: string }

/**
 * The Site's placements with each scene placement's `on` (ground or a roof)
 * carried over by key; positions stay the Site's own. Unchanged without a scene.
 */
export function withSceneRoofs<P extends Spot>(placements: Record<string, P>, scenePlacements: Record<string, Spot> | null | undefined): Record<string, P> {
  if (!scenePlacements) return placements
  let out: Record<string, P> | null = null
  for (const [key, p] of Object.entries(placements)) {
    const on = scenePlacements[key]?.on
    if (on && on !== p.on) (out ??= { ...placements })[key] = { ...p, on }
  }
  return out ?? placements
}
