// The Controls block of a converter-coupled asset's form (control-strategy 02;
// design.md DC-04 and B3: General › Sizing › Controls › Reliability). It edits
// the unit's island control settings, which live in the island sidecar, not
// in the network: so it has its own "Save controls" and sends only this unit's
// partial PATCH with the revision it read (GK B3 K8, K19). A mode switch is
// made on the server (the unit leaves its preset, the old mode's settings go,
// the new mode's come from a preset that runs it, tagged assumed); the block
// previews that before Save, from the same GET (K18).
//
// Guided shows the preset line and the mode; the settings sit under "More
// control settings", which opens on its own when a setting is required and
// empty or has an error (UX 29). Expert shows them open.
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import { isIslandConfigChanged } from '../../api/campusElectrical'
import { islandControlsApi, type IslandControlsState, type UnitControl } from '../../api/islandControls'
import { AssumedChip, AssumedKey } from '../../components/AssumedChip'
import { Btn } from '../../components/PageKit'
import { ISLAND_KEY } from '../../pages/CampusIslandSection'
import { CONTROL_FIELD_WORDS, LIMIT_WORDS, UPS_CONTROL_FIELDS, UPS_TRANSFER_LABEL } from '../../pages/islandWords'
import { controlModeLabel, presetLabel } from '../../plainNames'
import { useUIStore } from '../../store/uiStore'
import { Section } from './cardKit'

const INPUT = 'px-2 py-1 text-[12px] border border-border rounded bg-bg focus:outline-none focus:border-accent'
export const STALE_WORDS = 'The island settings were changed elsewhere. Reload to see them before saving.'

/** A draft: the mode the user picked (if they changed it) and the settings they typed, as typed. */
export interface ControlDraft {
  control?: string; values: Record<string, string>; transfers?: boolean
  /** The island revision the draft was made on: a draft restored after the settings changed is never saved. */
  base?: string | null
}
const EMPTY: ControlDraft = { values: {} }

// Unsaved edits outlive the drawer for the session (UX 29 Q1, option 1): closing and reopening
// the asset restores them, with "Unsaved control edits · Discard" above Save.
const drafts = new Map<string, ControlDraft>()
export const _resetDrafts = () => drafts.clear()

const isPairs = (f: string) => CONTROL_FIELD_WORDS[f]?.pairs !== undefined

/** The settings a mode shows: the rated power factor, the mode's own (gridspine's
 *  `control_fields`), and for a fixed power factor the one of its two the unit has. */
export function modeFields(state: IslandControlsState, uc: UnitControl, mode: string): string[] {
  if (uc.kind === 'ups') return [...UPS_CONTROL_FIELDS]
  const own = state.control_fields?.[mode] ?? []
  const choice = mode === 'gfl_pf'
    ? [(state.pf_choice ?? []).find(f => f in uc.values) ?? (state.pf_choice ?? [])[0]].filter(Boolean) as string[]
    : []
  return ['pf_rated', ...own, ...choice]
}

/** What the server fills a new mode's missing settings from: the first preset, in the
 *  schema's order, that runs this mode on this kind (`preset_block`, GK B3 K16). */
export function presetDefaults(state: IslandControlsState, kind: string, mode: string): Record<string, unknown> {
  for (const p of Object.values(state.presets ?? {})) {
    if (p.kinds.includes(kind) && p.converter.control === mode) return p.converter
  }
  return {}
}

export interface ShownField {
  field: string
  /** What the input shows: the draft, else the stored or default value as text. */
  text: string
  /** Where the shown value comes from. */
  origin: 'typed' | 'stored' | 'default' | 'empty'
  source?: string
}

const asText = (v: unknown): string =>
  Array.isArray(v) ? v.map(p => (Array.isArray(p) ? p.join(', ') : String(p))).join('\n')
    : v == null ? '' : String(v)

/** The settings the block shows for `mode`, each with its value's origin. */
export function shownFields(state: IslandControlsState, uc: UnitControl, mode: string, draft: ControlDraft): ShownField[] {
  const switching = uc.kind !== 'ups' && mode !== uc.control
  const defaults = switching ? presetDefaults(state, uc.kind, mode) : {}
  return modeFields(state, uc, mode).map(field => {
    if (field in draft.values) return { field, text: draft.values[field], origin: 'typed' }
    if (field in uc.values) return { field, text: asText(uc.values[field]), origin: 'stored', source: uc.sources[field] }
    if (field in defaults) return { field, text: asText(defaults[field]), origin: 'default', source: 'assumed' }
    return { field, text: '', origin: 'empty' }
  })
}

/** The stored settings a switch to `mode` drops (shown before Save, GK B3 K18). */
export function droppedFields(state: IslandControlsState, uc: UnitControl, mode: string): string[] {
  if (uc.kind === 'ups' || mode === uc.control) return []
  const keep = new Set([...modeFields(state, uc, mode), 'neg_seq'])
  return Object.keys(uc.values).filter(f => !keep.has(f))
}

/** A typed value as the PATCH sends it (a number, or a list of pairs), or the error in words. */
export function parseValue(field: string, text: string): { value?: number | number[][]; error?: string } {
  const t = text.trim()
  if (t === '') return { error: 'Enter a value.' }
  if (isPairs(field)) {
    const pairs = t.split('\n').map(l => l.trim()).filter(Boolean)
      .map(l => l.split(/[\s,;]+/).filter(Boolean).map(Number))
    if (pairs.length < 2 || pairs.some(p => p.length !== 2 || p.some(x => !Number.isFinite(x)))) {
      return { error: 'Enter at least two lines, each with two numbers.' }
    }
    return { value: pairs }
  }
  const n = Number(t)
  return Number.isFinite(n) ? { value: n } : { error: 'Enter a number.' }
}

/** The partial PATCH for this unit: the mode when it changed, and only the settings typed,
 *  as plain values (the server sets every source: GK B3 K17, K19). */
export function unitPatch(uc: UnitControl, draft: ControlDraft): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (draft.control !== undefined && draft.control !== uc.control) out.control = draft.control
  for (const [f, text] of Object.entries(draft.values)) {
    const p = parseValue(f, text)
    if (p.value !== undefined) out[f] = p.value
  }
  if (draft.transfers !== undefined && draft.transfers !== uc.transfers_on_islanding) {
    out.transfers_on_islanding = draft.transfers
  }
  return out
}

/** The 422's sentence on the field it names, in words (never the raw backend text: UX 29 4). */
export function refusalByField(detail: string, fields: string[]): { field: string | null; words: string } {
  const field = fields.find(f => new RegExp(`(^|[.\\['" ])${f}(\\b|$)`).test(detail)) ?? null
  if (!field) return { field: null, words: 'The controls could not be saved. Check the values and try again.' }
  const label = CONTROL_FIELD_WORDS[field]?.label ?? field
  if (/missing/.test(detail)) return { field, words: `${label} is needed for this mode.` }
  const limit = new RegExp(`${field}[^:]*?must be (.*?)(?:, got\\b|;|$)`).exec(detail)?.[1]
  const words = limit !== undefined ? LIMIT_WORDS[limit] : undefined
  return { field, words: words ? `${label} must be ${words}.` : `${label} was not accepted. Check the value.` }
}

/** The revision a Save sends: the one the edits were made on, so a draft from an older
 *  revision meets the server's 409 rather than overwriting what changed (GK B3). */
export function saveRevision(draft: ControlDraft, current: string | null | undefined): string | null | undefined {
  return draft.base ?? current
}


export default function ControlsBlock({ name }: { name: string }) {
  const project = useUIStore(s => s.currentProject)
  const guided = useUIStore(s => s.uiMode) === 'guided'
  const qc = useQueryClient()
  const island = useQuery({
    queryKey: ISLAND_KEY(project ?? ''), queryFn: () => islandControlsApi.read(project as string),
    enabled: !!project, retry: false,
  })
  const key = `${project}/${name}`
  const [draft, setDraftState] = useState<ControlDraft>(() => drafts.get(key) ?? EMPTY)
  const setDraft = (d: ControlDraft) => {
    const empty = d.control === undefined && d.transfers === undefined && !Object.keys(d.values).length
    const next = empty ? EMPTY : { ...d, base: d.base ?? island.data?.revision ?? null }
    setDraftState(next)
    if (empty) drafts.delete(key)
    else drafts.set(key, next)
  }
  const [refusal, setRefusal] = useState<{ field: string | null; words: string } | null>(null)
  const [stale, setStale] = useState(false)
  const [confirmReload, setConfirmReload] = useState(false)
  const [foldOpen, setFoldOpen] = useState<boolean | null>(null)

  const state = island.data
  const uc = state?.unit_controls?.[name]
  // A draft made on an older revision (restored after the settings changed elsewhere): Reload or Discard first.
  const draftStale = draft.base != null && state?.revision != null && draft.base !== state.revision
  const mode = draft.control ?? uc?.control ?? ''
  const shown = useMemo(() => (state && uc ? shownFields(state, uc, mode, draft) : []), [state, uc, mode, draft])
  const save = useMutation({
    mutationFn: () => islandControlsApi.patch(project as string, { units: { [name]: unitPatch(uc!, draft) } },
      // The revision the edits were made on (GK B3): a draft from an older one gets the server's 409.
      saveRevision(draft, state?.revision)),
    onMutate: () => { setRefusal(null); setStale(false) },
    onSuccess: data => { qc.setQueryData(ISLAND_KEY(project as string), data); setDraft(EMPTY) },
    onError: (e: unknown) => {
      if (isIslandConfigChanged(e)) { setStale(true); return }
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setRefusal(refusalByField(typeof detail === 'string' ? detail : '', shown.map(s => s.field)))
    },
  })

  if (!state || !uc) return null

  const errors = Object.fromEntries(shown.filter(s => s.origin === 'typed')
    .map(s => [s.field, parseValue(s.field, s.text).error]).filter(([, e]) => e))
  const empty = shown.filter(s => s.origin === 'empty').map(s => s.field)
  const dropped = droppedFields(state, uc, mode)
  const edited = Object.keys(unitPatch(uc, draft)).length > 0
  const blocked = draftStale ? 'Reload or discard first: the island settings changed since these edits.'
    : empty.length ? `Enter ${empty.map(f => CONTROL_FIELD_WORDS[f]?.label ?? f).join(', ')} to save.`
    : Object.keys(errors).length ? 'Fix the settings marked below to save.' : null
  const mustOpen = empty.length > 0 || Object.keys(errors).length > 0 || refusal?.field != null
  const open = !guided || mustOpen || (foldOpen ?? false)
  const anyAssumed = shown.some(s => s.origin !== 'typed' && s.source === 'assumed')

  const setValue = (field: string, text: string) => setDraft({ ...draft, values: { ...draft.values, [field]: text } })
  const setMode = (m: string) => setDraft({ ...draft, control: m === uc.control ? undefined : m })
  const reload = () => {
    if (edited && !confirmReload) { setConfirmReload(true); return }
    setConfirmReload(false); setStale(false); setDraft(EMPTY)
    void qc.invalidateQueries({ queryKey: ISLAND_KEY(project as string) })
  }

  const settings = (
    <div className="grid gap-2" data-testid="controls-fields">
      {shown.map(s => {
        const words = CONTROL_FIELD_WORDS[s.field]
        const label = words?.label ?? s.field
        const error = errors[s.field] ?? (refusal?.field === s.field ? refusal.words : undefined)
        const id = `controls-${name}-${s.field}`
        return (
          <div key={s.field} className="grid gap-0.5 text-[12px]" data-testid={`controls-field-${s.field}`}>
            <label htmlFor={id} className="text-text">
              {label}{words?.unit ? ` (${words.unit})` : ''}{s.origin === 'empty' && <span className="text-danger"> · required</span>}
            </label>
            {words?.pairs && <p className="text-[11px] text-muted" id={`${id}-hint`}>{words.pairs}</p>}
            {words?.pairs
              ? <textarea id={id} rows={4} className={`${INPUT} font-mono`} value={s.text} aria-describedby={`${id}-hint`}
                  aria-invalid={!!error} onChange={e => setValue(s.field, e.target.value)} />
              : <input id={id} type="text" inputMode="decimal" className={`${INPUT} w-32`} value={s.text}
                  aria-invalid={!!error} onChange={e => setValue(s.field, e.target.value)} />}
            {s.origin === 'typed' && <AssumedChip typed testId={`controls-stated-${s.field}`} />}
            {s.origin !== 'typed' && s.source === 'assumed' && (
              <AssumedChip testId={`controls-assumed-${s.field}`}
                source="a preset or template default" />
            )}
            {error && <p role="alert" className="text-[11px] text-danger" data-testid={`controls-error-${s.field}`}>{error}</p>}
          </div>
        )
      })}
      {uc.kind === 'ups' && (
        <label className="flex gap-2 items-center text-[12px]">
          <input type="checkbox" checked={draft.transfers ?? !!uc.transfers_on_islanding}
            onChange={e => setDraft({ ...draft, transfers: e.target.checked })} />
          {UPS_TRANSFER_LABEL}
        </label>
      )}
      {anyAssumed && <div className="text-[11px]"><AssumedKey testId="controls-assumed-key" /></div>}
    </div>
  )

  return (
    <div data-testid="controls-block">
      <Section title="Controls">
        {/* Plain words: no field in the app edits the project preset yet (UX 29, rule #1252). */}
        {uc.preset && uc.kind !== 'ups' && uc.preset_scope === 'project' && (
          <p className="text-[12px] text-muted mb-1" data-testid="controls-preset">
            From the project preset: {presetLabel(uc.preset)}.
          </p>
        )}
        {uc.preset && uc.kind !== 'ups' && uc.preset_scope === 'unit' && (
          <p className="text-[12px] text-muted mb-1" data-testid="controls-preset">
            From this unit's preset: {presetLabel(uc.preset)}.
          </p>
        )}
        {uc.kind !== 'ups' && (
          <div className="grid gap-0.5 text-[12px] mb-2">
            <label htmlFor={`controls-${name}-mode`} className="text-text">Control mode</label>
            <select id={`controls-${name}-mode`} className={`${INPUT} w-full min-w-0 max-w-full`} value={mode} onChange={e => setMode(e.target.value)}>
              {(state.controls ?? []).map(c => <option key={c} value={c}>{controlModeLabel(c)}</option>)}
            </select>
          </div>
        )}
        {mode !== uc.control && uc.kind !== 'ups' && (
          <p className="text-[12px] text-text mb-2" data-testid="controls-switch-note">
            {uc.preset ? 'This unit leaves the preset. ' : ''}
            {dropped.length
              ? `Saving drops ${dropped.map(f => CONTROL_FIELD_WORDS[f]?.label ?? f).join(', ')}.`
              : 'Saving keeps every current setting.'}
          </p>
        )}
        {guided
          ? (
            <details open={open} onToggle={e => setFoldOpen((e.target as HTMLDetailsElement).open)}
              data-testid="controls-fold">
              <summary className="cursor-pointer text-[12px] text-link mb-1">More control settings</summary>
              {settings}
            </details>
          )
          : settings}
        {(stale || draftStale) && (
          <div role="status" className="text-[12px] text-danger mt-2 flex flex-wrap gap-2 items-center"
            data-testid="controls-stale">
            {STALE_WORDS}
            <Btn onClick={reload}>Reload</Btn>
          </div>
        )}
        {confirmReload && (
          <div role="status" className="text-[12px] text-text mt-2 flex flex-wrap gap-2 items-center"
            data-testid="controls-reload-confirm">
            Reloading discards your unsaved control edits.
            <Btn onClick={reload}>Reload and discard</Btn>
            <Btn onClick={() => setConfirmReload(false)}>Keep editing</Btn>
          </div>
        )}
        {refusal && refusal.field === null && (
          <p role="alert" className="text-[12px] text-danger mt-2" data-testid="controls-refusal">{refusal.words}</p>
        )}
        <div className="flex flex-wrap gap-2 items-center justify-end mt-2">
          {edited && (
            <span role="status" className="text-[11px] text-muted mr-auto" data-testid="controls-unsaved">
              Unsaved control edits ·{' '}
              <button type="button" className="text-link underline" onClick={() => { setDraft(EMPTY); setRefusal(null); setStale(false) }}>
                Discard
              </button>
            </span>
          )}
          {edited && blocked && <span className="text-[11px] text-danger" data-testid="controls-blocked">{blocked}</span>}
          <Btn onClick={() => save.mutate()} disabled={!edited || !!blocked || stale || save.isPending}>
            <Save size={13} /> Save controls
          </Btn>
        </div>
      </Section>
    </div>
  )
}
