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
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { simulationApi } from '../../../api/simulation'
import type { SolverConfig } from '../../../api/types'
import {
  campusApi, errorText, isIslandConfigChanged, ISLAND_SCENARIOS,
  type IslandConfig, type IslandScenario,
} from '../../../api/campusElectrical'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import { invalidateReliabilityGoal, useReliabilityGoal } from '../../../hooks/useReliabilityGoal'
import {
  MIN_RENEWABLE_FRACTION_LABEL, RESERVE_LABELS, ShareField,
} from '../../solver/OffGridConstraints'
import { ISLAND_KEY } from '../../CampusIslandSection'
import { islandScenarioLabel } from '../../../plainNames'
import {
  ISLAND_OTHER_REQUIREMENT_LABELS,
} from '../../islandWords'
import { openBackupInputs, withReturnFrom } from '../../../layout/flowNavigation'
import {
  backupWords, BTN, Decision, SAVE, SaveError, useIslandPatch, type Tagged,
} from './BackupInputs'
export { backupFromText } from './BackupInputs'
import type { FlowTarget } from '../../../api/flow'

type ShareKey = 'min_renewable_fraction' | keyof typeof RESERVE_LABELS

const unit = (label: string) => label.replace(/ \(%\)$/, '')
const pct = (v: number) => `${Math.round(v * 100 * 1e4) / 1e4} %`
const RESERVE_PARTS: [keyof typeof RESERVE_LABELS, string][] = [
  ['reserve_share_load', 'load'], ['reserve_share_pv', 'solar'], ['reserve_share_wind', 'wind'],
]
const RENEWABLE_NAME = unit(MIN_RENEWABLE_FRACTION_LABEL)

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
  const viewerReadOnly = useUIStore(s => s.readOnly && s.readOnlyReason === 'viewer')
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
          disabled={!changed || refused || save.isPending || viewerReadOnly} onClick={() => save.mutate()}>Save</button>
        {onDone && <button type="button" className={BTN} onClick={() => { setPatch({}); onDone() }}>Cancel</button>}
        {save.isError && <span className="text-[12px] text-danger" role="alert">{errorText(save.error)}</span>}
      </div>
      <p className="text-[11px] text-muted">
        In Expert: Solver settings › Dispatch (reserve) and › Network (renewable fraction).
      </p>
    </div>
  )
}

function Scenarios({ name, config, revision, reload }: {
  name: string; config: IslandConfig; revision: string | null | undefined; reload: () => void
}) {
  const viewerReadOnly = useUIStore(s => s.readOnly && s.readOnlyReason === 'viewer')
  const save = useIslandPatch(name, revision)
  const chosen = config.requirements.scenarios
  const toggle = (sc: IslandScenario) =>
    save.mutate({ scenarios: chosen.includes(sc) ? chosen.filter(s => s !== sc) : [...chosen, sc] })
  return (
    <Decision id="scenarios" title="Scenarios to pass">
      <div className="flex flex-wrap gap-3 text-[12px]">
        {ISLAND_SCENARIOS.map(sc => (
          <label key={sc} className="flex gap-1 items-center">
            <input type="checkbox" checked={chosen.includes(sc)} disabled={save.isPending || viewerReadOnly}
              onChange={() => toggle(sc)} />
            {islandScenarioLabel(sc)}
          </label>
        ))}
      </div>
      <SaveError error={save.error} onReload={() => { save.reset(); reload() }} />
    </Decision>
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


/** The backup numbers, read-only: section 5 is where they are edited. */
function BackupSummary({ config }: { config: IslandConfig }) {
  return (
    <Decision id="backup" title="Backup requirements">
      <p data-testid="island-backup" className="text-[12px] text-text">{backupWords(config)}</p>
      <button type="button" data-testid="island-backup-edit" className={`${BTN} self-start`}
        onClick={() => openBackupInputs('Island requirements')}>Edit in Assets &amp; sizing ›</button>
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
      {config && <BackupSummary config={config} />}
      {config && <Remainder config={config} />}
    </section>
  )
}
