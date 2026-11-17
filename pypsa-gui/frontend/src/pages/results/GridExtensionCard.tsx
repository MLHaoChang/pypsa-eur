// "Grid extension vs island" (walkthrough asset-investment improvement 3,
// homer-parity 03d): the island user's first question is the distance from
// the grid within which extending it beats supplying the site off-grid.
//
// One component, two homes with the same words (Guided Results card, Expert
// Results › Investment case; parity test in GridExtensionCard.parity.test.tsx).
// It keeps one bare decision-study record on the open project (question id
// `grid_extension`, no question pack), runs the 03a supply-architecture
// comparison on it, and asks 03d for the break-even distance against the best
// (or chosen) architecture. The basis is the study's own ledger, shown as text
// and sent back unchanged, so the route's exact-basis check always holds.
// Shown only for an island site (`off_grid`); outside local mode the card
// stays, with its reason.
import { useEffect, useId, useState, type ChangeEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  decisionStudiesApi as api, GRID_EXTENSION_QUESTION_ID, GRID_EXTENSION_STUDY_NAME, studyError,
  type DecisionStudy, type GridBasis, type GridExtensionRequest, type GridExtensionResult,
  type StudyError, type SupplyArchitectureRow, type SupplyArchitectureRowStatus,
  type SupplyArchitecturesRecord,
} from '../../api/decisionStudies'
import { networkApi } from '../../api/network'
import { useAuthMode } from '../../auth/AuthModeProvider'
import { STATE_PILL } from '../../components/ResultState'
import { useReliabilityGoal } from '../../hooks/useReliabilityGoal'
import { pointAtHeaderSave } from '../../site3d/SiteEntryAction'
import { useUIStore } from '../../store/uiStore'
import { ERROR_FALLBACK, errorCopy } from '../../utils/decisionVocabulary'
import { money } from '../../utils/money'
import { nk } from '../../utils/queryKeys'

/** Every user-visible word of the card, so both homes and the tests read one list. */
export const GRID_EXTENSION_WORDS = {
  title: GRID_EXTENSION_STUDY_NAME,
  lead: 'How far from the grid does extending it cost less than supplying the site off-grid?',
  needsLocalMode: 'Needs local mode',
  saveFirst: 'Save the project first',
  saveInHeader: 'Save in the header ›',
  compare: 'Compare supply architectures',
  savesNote: 'Saves this comparison with the project',
  stop: 'Stop',
  rerun: 'Re-run',
  staleReason: 'the network changed since this comparison',
  aborted: 'Stopped before every architecture was solved.',
  failed: 'The comparison did not finish.',
  colArchitecture: 'Supply architecture',
  colCost: 'Cost per year',
  colStatus: 'Status',
  noCost: 'no cost',
  architecture: 'Architecture',
  eurPerKm: 'Grid extension cost (€ per km)',
  fixedEur: 'Fixed connection cost (€)',
  gridSupplyNpc: 'Grid supply net present cost (€)',
  gridSupplyNpcShort: 'Grid supply net present cost',
  maxKm: 'Maximum distance (km)',
  find: 'Find the break-even distance',
  basisUnset: 'Basis: not set yet. Compare the supply architectures again to set it.',
  offgridNpc: 'Island net present cost',
  approximation: 'Annualised approximation: the island’s net present cost is its cost per year times '
    + 'the annuity factor at this basis, not a year-by-year cash flow.',
} as const

const W = GRID_EXTENSION_WORDS

export const ROW_STATUS_WORDS: Record<SupplyArchitectureRowStatus, string> = {
  ok: 'Solved',
  infeasible: 'No design meets the load',
  failed: 'Solve failed',
  not_reached: 'Not solved (stopped first)',
}

/** The 03c engine's `reason` codes, in words. */
const NOT_ESTABLISHED_WORDS: Record<string, string> = {
  grid_supply_npc_required: 'the grid supply net present cost is missing',
  input_not_finite_or_out_of_range: 'a value is out of range',
  basis_missing: 'the basis is missing',
  basis_mismatch: 'the two sides are in different currencies or currency years',
  result_not_finite: 'the result is not a finite number',
}

const FIELD_WORDS: Record<string, string> = {
  eur_per_km: W.eurPerKm,
  fixed_eur: W.fixedEur,
  grid_supply_npc: W.gridSupplyNpc,
  max_km: W.maxKm,
  offgrid_npc: W.offgridNpc,
}

const RATE_ROW = 'discount_rate'
const HORIZON_ROW = 'battery_storage_lifetime_years'
const POLL_MS = 1500

const BTN_PRIMARY = 'self-start rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white '
  + 'disabled:cursor-not-allowed disabled:opacity-50'
const BTN_SECONDARY = 'self-start rounded-[6px] border border-border-2 bg-bg px-2 py-1 text-[11px] '
  + 'font-semibold text-text hover:border-border-3 disabled:opacity-50'
const INPUT = 'w-32 rounded border border-border bg-bg px-1 py-0.5 text-[11px] text-text'

/** A distance in words: one decimal under 10 km, whole km above. */
export function kmText(km: number): string {
  return `${km.toLocaleString('en', { maximumFractionDigits: km < 10 ? 1 : 0 })} km`
}

/** The study's basis (route `study_assumptions`), or null while any part is missing. */
export function studyBasis(study: DecisionStudy | null | undefined): GridBasis | null {
  if (!study?.ledger || study.currency_year == null) return null
  const value = (key: string) => study.ledger!.rows.find(r => r.key === key)?.value
  const rate = value(RATE_ROW)
  const life = value(HORIZON_ROW)
  if (typeof rate !== 'number' || !Number.isFinite(rate)) return null
  if (typeof life !== 'number' || !Number.isFinite(life)) return null
  return { currency: 'EUR', currency_year: study.currency_year, discount_rate: rate, horizon_years: Math.round(life) }
}

export function basisText(b: GridBasis): string {
  const pct = (b.discount_rate * 100).toLocaleString('en', { maximumFractionDigits: 2 })
  return `Basis: ${b.currency} ${b.currency_year} · ${pct} % discount rate · ${b.horizon_years} years · annualised approximation`
}

/** Solved rows cheapest first, then the rows with no cost in their table order. */
export function rankedRows(rows: SupplyArchitectureRow[]): SupplyArchitectureRow[] {
  const cost = (r: SupplyArchitectureRow) =>
    r.status === 'ok' && typeof r.total_cost_eur_per_year === 'number' ? r.total_cost_eur_per_year : null
  const solved = rows.filter(r => cost(r) != null).sort((a, b) => cost(a)! - cost(b)!)
  return [...solved, ...rows.filter(r => cost(r) == null)]
}

/** The result sentence for one 03d answer; never a code, never a km the route did not give. */
export function resultSentence(r: GridExtensionResult, maxKm: number): string {
  switch (r.status) {
    case 'break_even':
      return r.km != null
        ? `Grid extension pays off within ${kmText(r.km)} of the grid; beyond that, the island design is cheaper.`
        : 'The break-even distance could not be established.'
    case 'offgrid_cheaper_everywhere':
      return 'The island design is cheaper at any distance.'
    case 'grid_cheaper_everywhere':
      return 'Extending the grid is cheaper at any distance.'
    case 'beyond_max_km':
      return r.km != null
        ? `Break-even at ${kmText(r.km)}, beyond the ${kmText(maxKm)} searched.`
        : `Break-even beyond the ${kmText(maxKm)} searched.`
    default: {
      const why = r.reason ? NOT_ESTABLISHED_WORDS[r.reason] : undefined
      const fields = (r.invalid ?? []).map(k => FIELD_WORDS[k]).filter(Boolean)
      if (!why) return 'The break-even distance could not be established.'
      return `No break-even distance: ${why}${fields.length ? ` (${fields.join(', ')})` : ''}.`
    }
  }
}

/** A refusal in words: the app's copy for a known code, else the route's message. */
export function refusalText(e: StudyError): string {
  const c = errorCopy(e.code)
  return c !== ERROR_FALLBACK ? `${c.title} ${c.action}` : `${c.title} ${e.message}`
}

async function ensureStudy(project: string, existing: DecisionStudy | null): Promise<DecisionStudy> {
  let s = existing ?? await api.create(project, {
    question_id: GRID_EXTENSION_QUESTION_ID, name: GRID_EXTENSION_STUDY_NAME, intake: {},
  })
  if (s.currency_year == null) {
    // A bare record has no currency year; the route needs one, the library's.
    const lib = await api.library(project)
    s = await api.patchSettings(project, s.study_id, { currency_year: lib.currency_year })
  }
  if (!s.ledger) {
    await api.putLedger(project, s.study_id, { reseed: true })
    s = await api.get(project, s.study_id)
  }
  return s
}

function SaveFirst() {
  return (
    <p data-testid="grid-ext-save-first" className="text-[11px] text-warn">
      {W.saveFirst}{' '}
      <button type="button" onClick={pointAtHeaderSave} className="text-text underline">{W.saveInHeader}</button>
    </p>
  )
}

export default function GridExtensionCard() {
  const { authEnabled } = useAuthMode()
  const project = useUIStore(s => s.currentProject)
  const { archetype, isPending: goalPending } = useReliabilityGoal()
  const island = !goalPending && archetype === 'off_grid'
  const qc = useQueryClient()
  const studyKey = ['decision', project ?? '', 'gridExtension', 'study']

  const studyQ = useQuery({
    queryKey: studyKey,
    queryFn: async () =>
      (await api.list(project!)).find(s => s.question_id === GRID_EXTENSION_QUESTION_ID) ?? null,
    enabled: island && !authEnabled && !!project,
    retry: false,
  })
  const study = studyQ.data ?? null
  const studyId = study?.study_id ?? null
  const archKey = ['decision', project ?? '', 'gridExtension', studyId ?? '', 'architectures']
  const archQ = useQuery({
    queryKey: archKey,
    queryFn: async (): Promise<SupplyArchitecturesRecord | null> => {
      try {
        return await api.supplyArchitectures(project!, studyId!)
      } catch (e) {
        if (studyError(e).code === 'supply_architectures_never_run') return null
        throw e
      }
    },
    enabled: !!studyId && !!project,
    retry: false,
    refetchInterval: q => (q.state.data?.status === 'running' ? POLL_MS : false),
  })
  const rec = archQ.data ?? null

  const compare = useMutation({
    mutationFn: async () => {
      const s = await ensureStudy(project!, study)
      qc.setQueryData(studyKey, s)
      await api.startSupplyArchitectures(project!, s.study_id, {})
      return s
    },
    onSettled: () => { void qc.invalidateQueries({ queryKey: ['decision', project ?? '', 'gridExtension'] }) },
  })
  const abort = useMutation({
    mutationFn: () => api.abortSupplyArchitectures(project!, studyId!),
    onSettled: () => { void qc.invalidateQueries({ queryKey: archKey }) },
  })
  const compareErr = compare.error ? studyError(compare.error) : null
  const unsaved = compareErr?.code === 'baseline_not_saved'
  // The refusal clears once the project is saved (the status bar's own poll).
  const undo = useQuery({
    queryKey: nk(project, 'undoInfo'),
    queryFn: () => networkApi.undoInfo(),
    refetchInterval: 3000,
    enabled: unsaved && !!project,
  })
  const { reset: resetCompare } = compare
  useEffect(() => {
    if (unsaved && undo.data?.unsaved === false) resetCompare()
  }, [unsaved, undo.data?.unsaved, resetCompare])

  if (!island) return null

  const unavailable = authEnabled || studyError(studyQ.error).code === 'decision_studies_unavailable'
  const shell = (body: ReactNode) => (
    <section data-testid="grid-extension-card" aria-label={W.title}
      className="flex flex-col gap-2 rounded-[8px] border border-border bg-bg-2 px-3 py-2.5">
      <h3 className="text-[12px] font-semibold text-text">{W.title}</h3>
      <p className="text-[11px] text-muted">{W.lead}</p>
      {body}
    </section>
  )

  if (unavailable) {
    return shell(
      <>
        <button type="button" disabled className={BTN_PRIMARY}>{W.compare}</button>
        <p data-testid="grid-ext-needs-local" className="text-[11px] text-muted">{W.needsLocalMode}</p>
      </>,
    )
  }
  if (!project) {
    return shell(<><button type="button" disabled className={BTN_PRIMARY}>{W.compare}</button><SaveFirst /></>)
  }
  if (studyQ.isPending || (studyId && archQ.isPending)) {
    return shell(<p className="text-[11px] text-muted" aria-busy="true">Reading the last comparison…</p>)
  }
  if (studyQ.isError || archQ.isError) {
    return shell(
      <p data-testid="grid-ext-error" className="text-[11px] text-warn">
        {refusalText(studyError(studyQ.error ?? archQ.error))}
      </p>,
    )
  }

  const running = rec?.status === 'running' || compare.isPending
  const compareButton = (label: string) => (
    <div className="flex flex-col gap-1">
      <button type="button" data-testid="grid-ext-compare" className={label === W.compare ? BTN_PRIMARY : BTN_SECONDARY}
        disabled={compare.isPending} onClick={() => compare.mutate()}>
        {label}
      </button>
      {/* A muted pre-save note hides while the matching refusal shows (#1259). */}
      {!unsaved && <span data-testid="grid-ext-saves-note" className="text-[11px] text-muted">{W.savesNote}</span>}
    </div>
  )
  const refusal = compareErr && (unsaved ? <SaveFirst /> : (
    <p data-testid="grid-ext-error" role="status" className="text-[11px] text-warn">{refusalText(compareErr)}</p>
  ))

  if (running) {
    const n = rec?.architectures?.length ?? rec?.solves_estimated
    return shell(
      <div role="status" data-testid="grid-ext-running" className="flex flex-wrap items-center gap-2 text-[11px] text-text">
        <span>
          {n != null ? `Comparing ${n} architectures…` : 'Comparing the supply architectures…'}
          {rec?.solves_estimated != null && ` (${rec.solves_charged ?? 0} of ${rec.solves_estimated} solves)`}
        </span>
        {rec?.status === 'running' && (
          <button type="button" data-testid="grid-ext-stop" className={BTN_SECONDARY}
            disabled={abort.isPending} onClick={() => abort.mutate()}>{W.stop}</button>
        )}
      </div>,
    )
  }

  const rows = rec?.rows ?? []
  if (!rec || rows.length === 0) {
    const note = rec?.status === 'not_evaluated' ? refusalText({ status: null, code: 'supply_architectures_not_evaluated', message: '' })
      : rec ? W.failed : null
    return shell(
      <>
        {note && <p data-testid="grid-ext-note" className="text-[11px] text-warn">{note}</p>}
        {compareButton(W.compare)}
        {refusal}
      </>,
    )
  }

  const stale = rec.stale === true
  return shell(
    <>
      {stale && (
        <p data-testid="grid-ext-stale" className="flex flex-wrap items-center gap-2 text-[11px] text-warn">
          <span>{STATE_PILL.stale.label}: {W.staleReason}</span>
          <button type="button" data-testid="grid-ext-rerun" className={BTN_SECONDARY}
            disabled={compare.isPending} onClick={() => compare.mutate()}>{W.rerun}</button>
        </p>
      )}
      {stale && refusal}
      {rec.status === 'aborted' && <p className="text-[11px] text-muted">{W.aborted}</p>}
      <ArchitectureTable rows={rows} dimmed={stale} />
      {!stale && <BreakEvenForm project={project} study={study!} rec={rec} />}
    </>,
  )
}

function ArchitectureTable({ rows, dimmed }: { rows: SupplyArchitectureRow[]; dimmed: boolean }) {
  return (
    <table data-testid="grid-ext-table" className={`w-full text-[11px] ${dimmed ? 'opacity-60' : ''}`}>
      <thead>
        <tr className="text-muted">
          <th scope="col" className="text-left font-semibold">{W.colArchitecture}</th>
          <th scope="col" className="text-right font-semibold">{W.colCost}</th>
          <th scope="col" className="text-left font-semibold pl-3">{W.colStatus}</th>
        </tr>
      </thead>
      <tbody>
        {rankedRows(rows).map(r => (
          <tr key={r.architecture_id} data-testid={`grid-ext-row-${r.architecture_id}`}>
            <td className="text-text">{r.label}</td>
            <td className="text-right tabular-nums">
              {r.status === 'ok' && typeof r.total_cost_eur_per_year === 'number'
                ? money(r.total_cost_eur_per_year, 'EUR') : <span className="text-muted">{W.noCost}</span>}
            </td>
            <td className={`pl-3 ${r.status === 'ok' ? 'text-text' : 'text-warn'}`}>{ROW_STATUS_WORDS[r.status] ?? r.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

interface FormText { arch: string; eurPerKm: string; fixed: string; gridNpc: string; maxKm: string }

/** The request from the typed values, or the first field's refusal in words. */
export function breakEvenRequest(t: FormText, basis: GridBasis):
  { request: GridExtensionRequest; error: null } | { request: null; error: string } {
  const num = (s: string) => (s.trim() === '' ? NaN : Number(s))
  const perKm = num(t.eurPerKm)
  const fixed = t.fixed.trim() === '' ? 0 : num(t.fixed)
  const npc = num(t.gridNpc)
  const maxKm = num(t.maxKm)
  if (!t.arch) return { request: null, error: `${W.architecture}: choose a solved architecture.` }
  if (!Number.isFinite(perKm) || perKm < 0) return { request: null, error: `${W.eurPerKm}: enter 0 or more.` }
  if (!Number.isFinite(fixed) || fixed < 0) return { request: null, error: `${W.fixedEur}: enter 0 or more.` }
  if (!Number.isFinite(npc)) return { request: null, error: `${W.gridSupplyNpc}: enter an amount.` }
  if (!Number.isFinite(maxKm) || maxKm <= 0) return { request: null, error: `${W.maxKm}: enter more than 0.` }
  return {
    request: { architecture_id: t.arch, eur_per_km: perKm, fixed_eur: fixed, grid_supply_npc: npc,
      max_km: maxKm, grid_basis: basis },
    error: null,
  }
}

function BreakEvenForm({ project, study, rec }: {
  project: string; study: DecisionStudy; rec: SupplyArchitecturesRecord
}) {
  const id = useId()
  const solved = rankedRows(rec.rows ?? []).filter(r => r.status === 'ok' && r.total_cost_eur_per_year != null)
  const best = solved.find(r => r.architecture_id === rec.best_architecture_id) ?? solved[0]
  const [text, setText] = useState<FormText>({
    arch: '', eurPerKm: '', fixed: '0', gridNpc: '', maxKm: '10000',
  })
  const arch = text.arch || best?.architecture_id || ''
  const basis = studyBasis(study)
  const [inputError, setInputError] = useState<string | null>(null)
  const find = useMutation({
    mutationFn: (req: GridExtensionRequest) => api.gridExtensionBreakEven(project, study.study_id, req),
  })
  const edit = (k: keyof FormText) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setText(t => ({ ...t, [k]: e.target.value }))
    // A result describes the values it was asked with; an edit clears it.
    find.reset()
    setInputError(null)
  }
  const submit = () => {
    if (!basis) return
    const out = breakEvenRequest({ ...text, arch }, basis)
    if (out.error != null) { setInputError(out.error); return }
    find.mutate(out.request)
  }
  if (solved.length === 0) return null
  const field = (k: keyof FormText, label: string, extra?: string) => (
    <label className="flex items-center justify-between gap-3 text-[11px] text-text" htmlFor={`${id}-${k}`}>
      <span>{label}</span>
      <input id={`${id}-${k}`} inputMode="decimal" className={INPUT} value={text[k]} onChange={edit(k)}
        placeholder={extra} />
    </label>
  )
  const req = find.variables
  const res = find.data
  return (
    <div data-testid="grid-ext-form" className="flex flex-col gap-1.5 border-t border-border pt-2">
      <label className="flex items-center justify-between gap-3 text-[11px] text-text" htmlFor={`${id}-arch`}>
        <span>{W.architecture}</span>
        <select id={`${id}-arch`} className={INPUT} value={arch} onChange={edit('arch')}>
          {solved.map(r => <option key={r.architecture_id} value={r.architecture_id}>{r.label}</option>)}
        </select>
      </label>
      {field('eurPerKm', W.eurPerKm)}
      {field('fixed', W.fixedEur)}
      {field('gridNpc', W.gridSupplyNpc)}
      {field('maxKm', W.maxKm)}
      <p data-testid="grid-ext-basis" className="text-[11px] text-muted">{basis ? basisText(basis) : W.basisUnset}</p>
      <button type="button" data-testid="grid-ext-find" className={BTN_PRIMARY}
        disabled={!basis || find.isPending} onClick={submit}>{W.find}</button>
      <div role="status" className="flex flex-col gap-1">
        {inputError && <p data-testid="grid-ext-input-error" className="text-[11px] text-warn">{inputError}</p>}
        {find.error && (
          <p data-testid="grid-ext-error" className="text-[11px] text-warn">{refusalText(studyError(find.error))}</p>
        )}
        {res && req && (
          <div data-testid="grid-ext-result" className="flex flex-col gap-1">
            <p data-testid="grid-ext-result-line" className="text-[12px] font-semibold text-text">
              {resultSentence(res, req.max_km ?? 10_000)}
            </p>
            <p className="text-[11px] text-text">
              {W.offgridNpc}: {money(res.offgrid_npc, 'EUR')} · {W.gridSupplyNpcShort}:{' '}
              {req.grid_supply_npc < 0 ? '−' : ''}{money(req.grid_supply_npc, 'EUR')}
            </p>
            <p className="text-[11px] text-muted">{W.approximation}</p>
          </div>
        )}
      </div>
    </div>
  )
}
