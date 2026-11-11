// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * Slice 03b: saved parameter values in the asset forms (issue
 * `.scratch/assistant-memory/issues/03-parameter-suggestions.md`).
 *
 * - Read-out view: a "Save for reuse" button beside each field Save accepts
 *   (`saveable`, Save's own predicate: GK B 47 A1).
 * - Edit view: "Used before" chips under a field; a pick opens the
 *   `update_component` edit card with the memory line, and Apply posts the
 *   preview's ids and digest untouched (GK B P4, 47 A4).
 *
 * Expert only (`memoryShown`): Guided shows neither, and the Guided field
 * drawer gets them in ticket 06 (UX P6).
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import type { AssetFieldSpec } from '../../api/assetSchema'
import {
  PROJECT_KIND_WORDS, errorKind, errorWords, memoryApi, memoryParameterApi,
  type ParameterChip, type ParameterMemory, type ParameterPreview, type ParameterTarget,
} from '../../api/memory'
import { EditCardTable, editCardOf } from '../EditCard'
import { useUIStore, type UiMode } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { money } from '../../utils/money'
import { showUndoToast } from '../../utils/toasts'
import { displayValue, visibleUnit } from '../../utils/assetForm'
import { componentLabel } from '../../utils/componentLabels'
import { useAssetSchema } from '../../hooks/useAssetSchema'

/** The flag: saved values show in Expert only (UX 18 point 5). */
export function memoryShown(mode: UiMode): boolean {
  return mode === 'expert'
}

/** The network query each class's card reads, refetched after an apply. */
const ROOT: Record<string, string> = {
  Generator: 'generators', StorageUnit: 'storage_units', Store: 'stores', Load: 'loads',
  Link: 'links', Line: 'lines', Transformer: 'transformers', Bus: 'buses',
}

/** A refusal shown in place clears after this long (#1183). */
export const REFUSAL_MS = 8000

// ── Words ────────────────────────────────────────────────────────────────────

const isMoneyUnit = (u: string | null | undefined) => !!u && u.split('/')[0] === 'EUR'

/** "1,200 MW", "7 %", or for a cost "€ 1.2 k/kW": a stored value as the form shows it. */
export function valueWords(spec: Pick<AssetFieldSpec, 'scale' | 'unit_display' | 'unit_storage'>,
  stored: number, currency?: string | null): string {
  const d = displayValue(spec as AssetFieldSpec, stored)
  if (d === null) return 'no value'  // the store keeps finite numbers only; never a bare dash
  if (isMoneyUnit(spec.unit_display) || isMoneyUnit(spec.unit_storage)) {
    const unit = spec.unit_display || spec.unit_storage
    const per = unit.includes('/') ? `/${unit.split('/').slice(1).join('/')}` : ''
    return `${money(d, currency ?? 'EUR')}${per}`
  }
  const num = d.toLocaleString('en', { maximumFractionDigits: 4 })
  const unit = visibleUnit(spec.unit_display)
  return unit ? `${num} ${unit}` : num
}

/** "2024 EUR": a saved value's money stamp, as saved (GK B 47 A5). */
export function moneyStamp(m: Pick<ParameterChip, 'currency' | 'currency_year'>): string | null {
  return m.currency ? `${m.currency_year ?? ''} ${m.currency}`.trim() : null
}

export function sourceWords(chip: Pick<ParameterChip, 'source'>): string {
  return chip.source?.access && chip.source.label ? chip.source.label : 'another project'
}

/** The preview's own units, with the form's display scale: every unit on the card is one
 *  the digest binds (GK B 47 A4). */
function shownAs(spec: AssetFieldSpec, m: ParameterMemory) {
  return { scale: spec.scale, unit_display: m.unit_display, unit_storage: m.unit }
}

/** Where the replaced value came from, in words: a provenance source key is never shown
 *  (UX 18, #1194). No recorded source is a value the user typed. */
export function replacedSourceWords(source: string | null): string {
  if (!source) return 'you typed'
  if (source.startsWith('pack:')) return 'from a defaults pack'
  if (source.startsWith('eu-dea-')) return 'from the Danish Energy Agency technology catalogue'
  if (source.startsWith('memory:')) return 'from a saved value'
  return 'from another source'
}

/** "Applies to gas, replacing 100 MW from a defaults pack.": the target value and source
 *  the apply replaces, both bound by the digest (GK B 47 A4). */
export function targetWords(m: ParameterMemory, spec: AssetFieldSpec): string {
  const t = m.target
  if (t.value == null) return `Applies to ${t.name}, which has no value yet.`
  return `Applies to ${t.name}, replacing ${valueWords(shownAs(spec, m), t.value, m.currency)} `
    + `${replacedSourceWords(t.source)}.`
}

/** UX P1: "Used before: 7 % in Project A" / "Saved 2026-11-03 · 2024 EUR". */
export function chipWords(chip: ParameterChip, spec: AssetFieldSpec): [string, string] {
  const stamp = moneyStamp(chip)
  return [
    `Used before: ${valueWords(spec, chip.value, chip.currency)} in ${sourceWords(chip)}`,
    `Saved ${chip.saved_at.slice(0, 10)}${stamp ? ` · ${stamp}` : ''}`,
  ]
}

/** UX 18 point 1: "From memory v2 · saved in Project A · 2026-11-03 · 2024 EUR · carrier solar". */
export function memoryLine(m: ParameterMemory, chip: ParameterChip): string {
  return [
    `From memory v${m.version}`,
    `saved in ${sourceWords(chip)}`,
    chip.saved_at.slice(0, 10),
    moneyStamp(m),
    m.carrier ? `carrier ${m.carrier}` : null,
  ].filter(Boolean).join(' · ')
}

// ── Data ─────────────────────────────────────────────────────────────────────

/** The asset's chips and saveable fields. The key holds the asset's whole record, so
 *  any edit to it (bus, carrier or a value) refetches (GK B 47 A6). */
export function useParameterSuggestions(componentClass: string | null, name: string | null,
  asset: Record<string, unknown>) {
  const project = useUIStore(s => s.currentProject)
  const shown = memoryShown(useUIStore(s => s.uiMode))
  const enabled = shown && !!project && !!componentClass && !!name
  return useQuery({
    queryKey: nk(project, 'memory', 'suggestions', componentClass, name, JSON.stringify(asset)),
    queryFn: () => memoryParameterApi.suggestions({
      project_id: project!, component_class: componentClass!, name: name!,
    }),
    enabled, retry: false, staleTime: 0,
  })
}

function useInvalidateMemory() {
  const qc = useQueryClient()
  const project = useUIStore(s => s.currentProject)
  return () => qc.invalidateQueries({ queryKey: nk(project, 'memory', 'suggestions') })
}

// ── Save for reuse (read-out view) ───────────────────────────────────────────

/** The "Save for reuse" button of one read-out row, and its in-place refusal. Shown on
 *  row hover and focus-within (the row is `group`); it is always in the Tab order and
 *  takes its space up front, so the row never shifts (UX 18 point 4). */
export function SaveForReuse({ componentClass, name, field, label }: {
  componentClass: string; name: string; field: string; label: string
}) {
  const project = useUIStore(s => s.currentProject)
  const invalidate = useInvalidateMemory()
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  useEffect(() => {
    if (!refusal) return
    const t = window.setTimeout(() => setRefusal(null), REFUSAL_MS)
    return () => window.clearTimeout(t)
  }, [refusal])
  // The condition ends when the asset changes: a new name or field clears it.
  useEffect(() => setRefusal(null), [name, field])

  const save = async () => {
    if (!project) return
    setBusy(true)
    setRefusal(null)
    try {
      const item = await memoryParameterApi.save({ project_id: project, component_class: componentClass, name, field })
      const kinds = item.applies_to ? `${PROJECT_KIND_WORDS[item.applies_to]} projects` : 'every project'
      if (item.created) {
        // Undo retires exactly the item this save created (GK B 47 A2).
        showUndoToast(`Saved for reuse in ${kinds}`, async () => {
          await memoryApi.act(item.id, 'retire')
          await invalidate()
        })
      } else {
        toast.success(`Saved value updated (version ${item.version}). The earlier value stays in the Memory dialog as a version.`,
          { duration: 6000 })
      }
      await invalidate()
    } catch (e) {
      setRefusal(errorWords(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" onClick={save} disabled={busy} data-testid={`memory-save-${field}`}
        aria-label={`Save ${label} for reuse`}
        className="ml-1.5 text-[9px] text-muted hover:text-accent underline-offset-2 hover:underline opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent rounded-sm disabled:opacity-50">
        Save for reuse
      </button>
      <span role="status" className="basis-full text-[9px] leading-snug text-danger empty:hidden" data-testid={`memory-save-refusal-${field}`}>
        {refusal}
      </span>
    </>
  )
}

/** The read-out rows' action slot: Save for reuse where Save accepts the field. */
export function useSaveAction(componentClass: string | null, name: string | null,
  asset: Record<string, unknown>): (field: string | undefined, label: string) => ReactNode {
  const q = useParameterSuggestions(componentClass, name, asset)
  const saveable = new Set(q.data?.saveable ?? [])
  return (field, label) => field && componentClass && name && saveable.has(field)
    ? <SaveForReuse componentClass={componentClass} name={name} field={field} label={label} />
    : null
}

// ── Chips and the pick card (edit view) ──────────────────────────────────────

type CardState =
  | { at: 'loading' }
  | { at: 'card'; preview: ParameterPreview }
  | { at: 'refused'; message: string; stale: boolean }
  | { at: 'gone' }

const STALE = new Set(['edit_preview_stale', 'memory_stale'])
const refused = (e: unknown): CardState => ({ at: 'refused', message: errorWords(e), stale: STALE.has(errorKind(e) ?? '') })

/** The chips under one field. A chip the server marks `current` is a marker, not a
 *  button: Apply never proposes a no-op (UX P2, GK B 47 A3). */
export function SuggestionChips({ chips, spec, target, onApplied }: {
  chips: ParameterChip[]; spec: AssetFieldSpec; target: Omit<ParameterTarget, 'project_id'>
  onApplied: (written: Record<string, number>) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const refs = useRef<Record<string, HTMLElement | null>>({})
  // Esc, Apply and Cancel return focus to the chip (UX P3). After an Apply the refetch
  // redraws that chip as its "current" marker, so focus follows it there too.
  const [returnTo, setReturnTo] = useState<string | null>(null)
  useEffect(() => {
    if (!returnTo) return
    refs.current[returnTo]?.focus()
    const t = window.setTimeout(() => setReturnTo(null), 3000)
    return () => window.clearTimeout(t)
  }, [returnTo, chips])
  if (!chips.length) return null
  const close = (id: string) => {
    setOpen(null)
    setReturnTo(id)
  }
  const picked = chips.find(c => c.id === open)
  return (
    <div className="col-span-2 -mt-0.5 relative" data-testid={`memory-chips-${spec.key}`}>
      <div className="flex flex-wrap gap-1">
        {chips.map(chip => {
          const [line1, line2] = chipWords(chip, spec)
          return chip.current ? (
            <span key={chip.id} data-testid="memory-chip-current" tabIndex={-1}
              ref={el => { refs.current[chip.id] = el }}
              className="inline-flex flex-col rounded border border-border/60 px-1.5 py-0.5 text-[9px] text-muted max-w-full">
              <span className="truncate">{line1} <span className="font-semibold">· current</span></span>
              <span className="truncate">{line2}</span>
            </span>
          ) : (
            <button key={chip.id} type="button" data-testid="memory-chip"
              ref={el => { refs.current[chip.id] = el }}
              aria-expanded={open === chip.id} aria-haspopup="dialog"
              onClick={() => setOpen(open === chip.id ? null : chip.id)}
              className="inline-flex flex-col items-start text-left rounded border border-border bg-panel px-1.5 py-0.5 text-[9px] text-text hover:border-accent aria-expanded:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent max-w-full">
              <span className="truncate max-w-full">{line1}</span>
              <span className="truncate max-w-full text-muted">{line2}</span>
            </button>
          )
        })}
      </div>
      {picked && (
        <PickCard key={`${picked.id}@${picked.version}`} chip={picked} spec={spec} target={target}
          onClose={() => close(picked.id)}
          onApplied={written => { onApplied(written); close(picked.id) }} />
      )}
    </div>
  )
}

/** The popover: the edit card with the memory line, or the refusal in its place. */
export function PickCard({ chip, spec, target, onClose, onApplied }: {
  chip: ParameterChip; spec: AssetFieldSpec; target: Omit<ParameterTarget, 'project_id'>
  onClose: () => void; onApplied: (written: Record<string, number>) => void
}) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const invalidate = useInvalidateMemory()
  const [state, setState] = useState<CardState>({ at: 'loading' })
  const [busy, setBusy] = useState(false)
  const [current, setCurrent] = useState(chip)
  const box = useRef<HTMLDivElement>(null)

  const preview = async (c: ParameterChip) => {
    if (!project) return
    setState({ at: 'loading' })
    try {
      setState({ at: 'card', preview: await memoryParameterApi.preview(c, { project_id: project, ...target }) })
    } catch (e) {
      setState(refused(e))
    }
  }
  useEffect(() => { void preview(chip) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { box.current?.focus() }, [])

  /** After a 409: refetch the chips and preview the item's current version, if offered. */
  const again = async () => {
    if (!project) return
    const fresh = await memoryParameterApi.suggestions({ project_id: project, ...target }).catch(() => null)
    await invalidate()
    const next = fresh?.fields[spec.key]?.find(c => c.id === chip.id && !c.current)
    if (!next) { setState({ at: 'gone' }); return }
    setCurrent(next)
    await preview(next)
  }

  const apply = async () => {
    if (state.at !== 'card' || !project) return
    setBusy(true)
    try {
      const out = await memoryParameterApi.apply(state.preview, project)
      onApplied(out.written)
      await qc.invalidateQueries({ queryKey: nk(project, ROOT[target.component_class] ?? 'network') })
      await qc.invalidateQueries({ queryKey: nk(project, 'results') })
      await invalidate()
      if (!out.apply_counted) toast('Applied. The saved value’s use count was not recorded.')
    } catch (e) {
      // A 409 applies nothing (GK B 47 A6): the card offers only "Preview again".
      setState(refused(e))
    } finally {
      setBusy(false)
    }
  }

  const card = state.at === 'card' ? editCardOf(state.preview) : null
  const m = state.at === 'card' ? state.preview.memory : null
  return (
    <div ref={box} tabIndex={-1} role="dialog" aria-label={`Saved value for ${spec.label}`}
      data-testid="memory-pick-card"
      onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }}
      className="mt-1 w-full max-w-full rounded border border-border bg-bg p-2 shadow-lg text-[10px] space-y-1.5 outline-none">
      {state.at === 'loading' && <p className="text-muted">Checking the saved value…</p>}
      {state.at === 'gone' && <p role="status">This saved value is no longer offered for {spec.label}.</p>}
      {state.at === 'refused' && <p role="status" data-testid="memory-pick-refusal">{state.message}</p>}
      {m && (
        <div data-testid="memory-pick-facts" className="space-y-0.5">
          <p className="font-semibold">
            {m.label}: {valueWords(shownAs(spec, m), m.value, m.currency)}
            {m.unit !== m.unit_display && (
              <span className="font-normal text-muted"> (stored as {m.value} {m.unit})</span>)}
          </p>
          <p className="text-muted" data-testid="memory-line">{memoryLine(m, current)}</p>
          <p className="text-muted" data-testid="memory-target">{targetWords(m, spec)}</p>
        </div>
      )}
      {card && <EditCardTable card={card} guided={false} labels={{ header: '', caption: 'Change to apply' }} />}
      <div className="flex gap-2 justify-end">
        {state.at === 'refused' && state.stale ? (
          <button type="button" className="text-accent underline" onClick={again} data-testid="memory-pick-again">
            Preview again</button>
        ) : (
          <>
            <button type="button" className="text-muted underline" onClick={onClose} data-testid="memory-pick-cancel">
              {state.at === 'card' ? 'Cancel' : 'Close'}</button>
            {state.at === 'card' && (
              <button type="button" disabled={busy} onClick={apply} data-testid="memory-pick-apply"
                className="px-2 py-0.5 rounded bg-accent text-white font-semibold disabled:opacity-50">Apply</button>
            )}
          </>
        )}
      </div>
    </div>
  )
}


// ── Memory dialog words (UX P5) ──────────────────────────────────────────────

/** A saved parameter's body, as the store holds it (plan §1.1). */
export interface ParameterBody {
  component_class: string; field: string; value: number; unit: string
  currency?: string | null; currency_year?: number | null; carrier?: string | null
}

export function parameterBodyOf(body: Record<string, unknown> | null): ParameterBody | null {
  if (!body || typeof body.component_class !== 'string' || typeof body.field !== 'string'
    || typeof body.value !== 'number' || typeof body.unit !== 'string') return null
  return body as unknown as ParameterBody
}

/** "Capacity (Generator): 100 MW · carrier gas": the field label the asset form shows
 *  (the schema's, per class), the class in user words, then the value as saved. */
export function parameterWords(body: ParameterBody, spec: AssetFieldSpec | undefined): string {
  const label = spec?.label ?? body.field.replace(/_/g, ' ')
  const value = spec ? valueWords(spec, body.value, body.currency)
    : `${body.value.toLocaleString('en', { maximumFractionDigits: 4 })} ${body.unit}`
  const stamp = moneyStamp({ currency: body.currency ?? null, currency_year: body.currency_year ?? null })
  return [`${label} (${componentLabel(body.component_class)}): ${value}`, stamp,
    body.carrier ? `carrier ${body.carrier}` : null].filter(Boolean).join(' · ')
}

/** The Memory dialog's summary cell for a `parameter` item. */
export function ParameterSummary({ body }: { body: ParameterBody }) {
  const schema = useAssetSchema(body.component_class)
  const spec = schema.data?.groups.flatMap(g => g.fields).find(f => f.key === body.field)
  return <>{parameterWords(body, spec)}</>
}
