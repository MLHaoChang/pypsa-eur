// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Section 8 — Island requirements (control-strategy 03, improvement 1): the
// Guided home of an island's control-strategy inputs, under the Goal card. At
// most three decisions: the dispatch limits (operating reserve and minimum
// renewable fraction, the Solver settings fields), the islanding scenarios to
// pass and the four backup numbers. Expert's editors stay the Solver settings
// blocks and Grid study › Island; both modes use one word map
// (`pages/islandWords.ts`) and one write each (the solver config PUT, the
// island PATCH). Shown for an off-grid site or a project with an island
// config; absent otherwise.
import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { simulationApi } from '../../../api/simulation'
import type { SolverConfig } from '../../../api/types'
import {
  campusApi, errorText, isOtherKind, ISLAND_SCENARIOS,
  type IslandConfig, type IslandScenario, type IslandSource,
} from '../../../api/campusElectrical'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { invalidateReliabilityGoal, useReliabilityGoal } from '../../../hooks/useReliabilityGoal'
import {
  MIN_RENEWABLE_FRACTION_LABEL, RESERVE_LABELS, ShareField,
} from '../../solver/OffGridConstraints'
import { ISLAND_KEY } from '../../CampusIslandSection'
import {
  ISLAND_OTHER_REQUIREMENT_LABELS, ISLAND_REQUIREMENT_LABELS, ISLAND_SCENARIO_LABELS,
  type IslandRequirementNumber,
} from '../../islandWords'
import { withReturnFrom } from '../../../layout/flowNavigation'
import type { FlowTarget } from '../../../api/flow'

type ShareKey = 'min_renewable_fraction' | keyof typeof RESERVE_LABELS
type Tagged = { value: number; source: IslandSource }

const unit = (label: string) => label.replace(/ \(%\)$/, '')
const pct = (v: number) => `${Math.round(v * 100 * 1e4) / 1e4} %`
const RESERVE_PARTS: [keyof typeof RESERVE_LABELS, string][] = [
  ['reserve_share_load', 'load'], ['reserve_share_pv', 'solar'], ['reserve_share_wind', 'wind'],
]
const RENEWABLE_NAME = unit(MIN_RENEWABLE_FRACTION_LABEL)
const BTN = 'text-[12px] text-accent hover:underline'
const SAVE = 'px-2 py-1 rounded border border-border text-[12px] disabled:opacity-50'

/** "Operating reserve: off (assumed)", or the shares that are set. */
export function reserveWords(cfg: Partial<SolverConfig> | undefined): string {
  const parts = RESERVE_PARTS.filter(([f]) => cfg?.[f]).map(([f, w]) => `${pct(cfg![f] as number)} of ${w}`)
  return `Operating reserve: ${parts.length ? parts.join(', ') : 'off (assumed)'}`
}

/** "Minimum renewable fraction: none (assumed)", or the fraction. */
export function renewableWords(cfg: Partial<SolverConfig> | undefined): string {
  const v = cfg?.min_renewable_fraction
  return `${RENEWABLE_NAME}: ${v ? pct(v) : 'none (assumed)'}`
}

/** The Assumptions card rows this card's dispatch limits answer (the App FieldDrawer branch). */
export function isDispatchLimitTarget(t: Pick<FlowTarget, 'section' | 'field'> | null | undefined): boolean {
  return !!t && t.section === 'scenarios'
    && (t.field === 'reserve_share_load' || t.field === 'min_renewable_fraction')
}

/** The reserve shares and minimum renewable fraction, edited in place; Save writes only the changed fields. */
export function DispatchLimitsEditor({ onDone }: { onDone?: () => void }) {
  const project = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const cfg = useQuery({
    queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig, enabled: !!project,
  })
  const [patch, setPatch] = useState<Partial<SolverConfig>>({})
  const [errors, setErrors] = useState<Partial<Record<ShareKey, string>>>({})
  const save = useMutation({
    mutationFn: () => simulationApi.updateSolverConfig(patch),
    onSuccess: () => { invalidateReliabilityGoal(qc, project); setPatch({}); onDone?.() },
  })
  const refused = Object.values(errors).some(Boolean)
  const changed = Object.keys(patch).length > 0
  const onError = (f: ShareKey, e: string | null) => setErrors(prev => ({ ...prev, [f]: e ?? undefined }))
  const fields: [ShareKey, string][] = [
    ...(Object.entries(RESERVE_LABELS) as [ShareKey, string][]),
    ['min_renewable_fraction', MIN_RENEWABLE_FRACTION_LABEL],
  ]
  if (!cfg.data) return <p className="text-[12px] text-muted">Loading…</p>
  return (
    <div data-testid="island-dispatch-editor" className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-3">
        {fields.map(([f, label]) => (
          <div key={f}>
            <ShareField field={f} label={label} value={(f in patch ? patch[f] : cfg.data[f]) as number | null}
              patch={p => setPatch(prev => ({ ...prev, ...p }))} onError={onError}
              errorTestId={`island-dispatch-error-${f}`} />
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <button type="button" data-testid="island-dispatch-save" className={SAVE}
          disabled={!changed || refused || save.isPending} onClick={() => save.mutate()}>Save</button>
        {onDone && <button type="button" className={BTN} onClick={() => { setPatch({}); onDone() }}>Cancel</button>}
        {save.isError && <span className="text-[12px] text-danger" role="alert">{errorText(save.error)}</span>}
      </div>
      <p className="text-[11px] text-muted">
        In Expert: Solver settings › Dispatch (reserve) and › Network (renewable fraction).
      </p>
    </div>
  )
}

function Decision({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <div data-decision={id} className="flex flex-col gap-1">
      <span className="text-[12px] font-semibold text-text">{title}</span>
      {children}
    </div>
  )
}

function DispatchLimits() {
  const project = useUIStore(s => s.currentProject)
  const cfg = useQuery({
    queryKey: nk(project, 'solverConfig'), queryFn: simulationApi.getSolverConfig, enabled: !!project,
  })
  const [open, setOpen] = useState(false)
  return (
    <Decision id="dispatch-limits" title="Dispatch limits">
      <p data-testid="island-reserve" className="text-[12px] text-text">{reserveWords(cfg.data)}</p>
      <p data-testid="island-renewable" className="text-[12px] text-text">{renewableWords(cfg.data)}</p>
      {open ? <DispatchLimitsEditor onDone={() => setOpen(false)} />
        : <button type="button" data-testid="island-dispatch-edit" className={`${BTN} self-start`}
            onClick={() => setOpen(true)}>Edit the reserve and renewable fraction ›</button>}
    </Decision>
  )
}

const STALE = 'The island settings changed since you opened them. Reload and try again.'

/** The island PATCH with the stored revision; a 409 keeps the edit and offers a reload. */
function useIslandPatch(name: string, revision: string | null | undefined, onSaved?: () => void) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (requirements: Record<string, unknown>) =>
      campusApi.patchIsland(name, { requirements }, revision),
    onSuccess: data => { qc.setQueryData(ISLAND_KEY(name), data); onSaved?.() },
  })
}

function SaveError({ error, onReload }: { error: unknown; onReload: () => void }) {
  if (!error) return null
  if (isOtherKind(error)) {
    return (
      <p role="alert" data-testid="island-stale" className="text-[12px] text-danger">
        {STALE}{' '}<button type="button" className={BTN} onClick={onReload}>Reload</button>
      </p>
    )
  }
  return <p role="alert" data-testid="island-save-error" className="text-[12px] text-danger">{errorText(error)}</p>
}

function Scenarios({ name, config, revision, reload }: {
  name: string; config: IslandConfig; revision: string | null | undefined; reload: () => void
}) {
  const save = useIslandPatch(name, revision)
  const chosen = config.requirements.scenarios
  const toggle = (sc: IslandScenario) =>
    save.mutate({ scenarios: chosen.includes(sc) ? chosen.filter(s => s !== sc) : [...chosen, sc] })
  return (
    <Decision id="scenarios" title="Scenarios to pass">
      <div className="flex flex-wrap gap-3 text-[12px]">
        {ISLAND_SCENARIOS.map(sc => (
          <label key={sc} className="flex gap-1 items-center">
            <input type="checkbox" checked={chosen.includes(sc)} disabled={save.isPending}
              onChange={() => toggle(sc)} />
            {ISLAND_SCENARIO_LABELS[sc]}
          </label>
        ))}
      </div>
      <SaveError error={save.error} onReload={() => { save.reset(); reload() }} />
    </Decision>
  )
}

const BACKUP_WORDS: [IslandRequirementNumber, (v: number) => string][] = [
  ['sustained_h', v => `ride-through ${v} h`],
  ['bridge_s', v => `UPS ${v} s`],
  ['fuel_autonomy_h', v => `fuel ${v} h`],
  ['genset_redundancy_n', v => `generators N+${v}`],
]
const NUMBER_FIELDS = Object.entries(ISLAND_REQUIREMENT_LABELS) as [IslandRequirementNumber, string][]

/** A typed backup number → its value, or a refusal opening with the field's name. */
export function backupFromText(label: string, text: string): { value: number } | { error: string } {
  const name = label.replace(/ \([^)]*\)$/, '')
  const t = text.trim()
  const v = Number(t)
  if (t === '' || !Number.isFinite(v)) return { error: `${name}: enter a number.` }
  if (v < 0) return { error: `${name}: ${t} is below 0.` }
  return { value: v }
}

function Backup({ name, config, revision, reload }: {
  name: string; config: IslandConfig; revision: string | null | undefined; reload: () => void
}) {
  const req = config.requirements
  const tagged = (f: IslandRequirementNumber) => req[f] as Tagged | undefined
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<Partial<Record<IslandRequirementNumber, string>>>({})
  const save = useIslandPatch(name, revision, () => { setOpen(false); setText({}) })
  const set = BACKUP_WORDS.filter(([f]) => tagged(f) != null)
  const assumed = set.some(([f]) => tagged(f)?.source === 'assumed')
  const parsed = Object.entries(text).map(([f, t]) =>
    [f, backupFromText(ISLAND_REQUIREMENT_LABELS[f as IslandRequirementNumber], t ?? '')] as const)
  const errors = parsed.flatMap(([, r]) => ('error' in r ? [r.error] : []))
  const changes = Object.fromEntries(parsed.flatMap(([f, r]) =>
    'value' in r && r.value !== tagged(f as IslandRequirementNumber)?.value ? [[f, r.value]] : []))
  return (
    <Decision id="backup" title="Backup requirements">
      <p data-testid="island-backup" className="text-[12px] text-text">
        {set.length
          ? `Backup: ${set.map(([f, w]) => w(tagged(f)!.value)).join(' · ')}${assumed ? ' (assumed)' : ''}`
          : 'Backup: not set'}
      </p>
      {open ? (
        <div data-testid="island-backup-editor" className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-3">
            {NUMBER_FIELDS.map(([f, label]) => (
              <label key={f} className="flex flex-col gap-1">
                <span className="text-[10px] text-muted">{label}</span>
                <input type="text" inputMode="decimal"
                  value={text[f] ?? (tagged(f)?.value != null ? String(tagged(f)!.value) : '')}
                  onChange={e => setText(prev => ({ ...prev, [f]: e.target.value }))}
                  className="w-28 px-2 py-1 border border-border rounded text-xs font-mono bg-bg focus:outline-none focus:border-accent" />
              </label>
            ))}
          </div>
          {errors.map(e => <p key={e} role="alert" className="text-[10px] text-danger">{e}</p>)}
          <div className="flex items-center gap-2">
            <button type="button" data-testid="island-backup-save" className={SAVE}
              disabled={errors.length > 0 || Object.keys(changes).length === 0 || save.isPending}
              onClick={() => save.mutate(changes)}>Save</button>
            <button type="button" className={BTN}
              onClick={() => { setOpen(false); setText({}); save.reset() }}>Cancel</button>
          </div>
          <SaveError error={save.error} onReload={() => { save.reset(); reload() }} />
        </div>
      ) : (
        <button type="button" data-testid="island-backup-edit" className={`${BTN} self-start`}
          onClick={() => setOpen(true)}>Edit the backup requirements ›</button>
      )}
    </Decision>
  )
}

/** Grid study › Island in Expert, scrolled to the Island block; Back returns here. */
function openIslandInExpert(): void {
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    ui.requestPanelSection('campusStudy', 'island')
    ui.setActiveSection('scenarios')
    ui.setUiMode('expert')
    ui.setSlidePanel('campusStudy')
  })
}

function Remainder({ config }: { config: IslandConfig }) {
  const rest = Object.entries(ISLAND_OTHER_REQUIREMENT_LABELS).flatMap(([f, label]) => {
    const v = config.requirements[f] as Tagged | undefined
    if (v == null || typeof v !== 'object' || !('value' in v)) return []
    return [`${label} ${v.value}${v.source === 'assumed' ? ' (assumed)' : ''}`]
  })
  return (
    <div className="flex flex-col gap-1">
      {rest.length > 0 && (
        <p data-testid="island-remainder" className="text-[12px] text-muted">Also set: {rest.join(' · ')}</p>
      )}
      <button type="button" data-testid="island-open-expert" className={`${BTN} self-start`}
        onClick={openIslandInExpert}>More in Expert › Grid study › Island ›</button>
    </div>
  )
}

export function IslandRequirementsCard() {
  const project = useUIStore(s => s.currentProject)
  const { archetype } = useReliabilityGoal()
  const island = useQuery({
    queryKey: ISLAND_KEY(project ?? ''), queryFn: () => campusApi.island(project as string),
    enabled: !!project, retry: false,
  })
  const config = island.data?.config ?? null
  const offGrid = archetype === 'off_grid'
  if (!project || (!offGrid && !config)) return null
  const revision = island.data?.revision
  const reload = () => { void island.refetch() }
  return (
    <section data-testid="hub-island-requirements"
      className="flex flex-col gap-4 rounded-lg border border-border bg-panel p-5">
      <header className="flex flex-col gap-1">
        <h2 className="text-[15px] font-semibold text-text">Island requirements</h2>
        <p className="text-[12px] text-muted leading-relaxed">
          How the site runs without the grid: the reserve it holds, the islanding events it must pass and its backup.
        </p>
      </header>
      {offGrid && <DispatchLimits />}
      {config && <Scenarios name={project} config={config} revision={revision} reload={reload} />}
      {config && <Backup name={project} config={config} revision={revision} reload={reload} />}
      {config && <Remainder config={config} />}
    </section>
  )
}
