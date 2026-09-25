import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hexagon, Square } from 'lucide-react'
import {
  resultsApi,
  type EhArchetype,
  type EhDtcPlanningTable,
  type EhDtcStressTable,
  type EhLeverTable,
  type EhRedundancyTable,
  type EhReferenceDesignReport,
  type EhScrVerdict,
  type EhSectionStatus,
  type EhStudyPayload,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { blockerMessage } from './McPanel'
import { downloadCSV } from './shared'

const ARCHETYPES: { id: EhArchetype; label: string; blurb: string }[] = [
  {
    id: 'strong_grid',
    label: 'Strong grid',
    blurb: 'Sizing + ENS target; redundancy/DtC may stay skipped.',
  },
  {
    id: 'weak_flexible',
    label: 'Weak / flexible',
    blurb: 'Import + storage levers and DtC stress by default.',
  },
  {
    id: 'off_grid',
    label: 'Off-grid',
    blurb: 'Island overlay; storage-duration levers (no import MW lever).',
  },
]

const eur = (v: number) =>
  v >= 1e9 ? `€${(v / 1e9).toFixed(2)}bn`
    : v >= 1e6 ? `€${(v / 1e6).toFixed(1)}m`
      : `€${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`

const cell = (v: unknown) =>
  v == null || v === '' ? '—' : String(v)

/** Stable display order for completeness chips (matches REPORT_SECTIONS). */
export const COMPLETENESS_ORDER = [
  'target', 'cost', 'frontier', 'sizing', 'redundancy', 'levers', 'dtc',
  'fmea_top', 'tea', 'gates', 'multi_energy',
] as const

export function completenessRows(
  map: Record<string, EhSectionStatus> | null | undefined,
): { name: string; status: EhSectionStatus }[] {
  if (!map) return []
  const seen = new Set<string>()
  const out: { name: string; status: EhSectionStatus }[] = []
  for (const name of COMPLETENESS_ORDER) {
    if (name in map) {
      out.push({ name, status: map[name] })
      seen.add(name)
    }
  }
  for (const [name, status] of Object.entries(map)) {
    if (!seen.has(name)) out.push({ name, status })
  }
  return out
}

export function statusTone(status: EhSectionStatus): string {
  if (status === 'ok') return 'text-accent'
  if (status === 'skipped') return 'text-muted'
  return 'text-warn'
}

/** Sections that are not_established with a reason, in chip order.
 *  Gates / multi-energy notes render in their own blocks, so they are left out. */
export function notEstablishedNotes(
  report: EhReferenceDesignReport,
): { name: string; note: string }[] {
  return completenessRows(report.completeness)
    .filter(({ name, status }) =>
      status === 'not_established'
      && name !== 'gates' && name !== 'multi_energy'
      && Boolean(report.sections?.[name]?.note))
    .map(({ name }) => ({ name, note: String(report.sections![name].note) }))
}

/** Tone for the P9 SCR product verdict (fail reserved; thin slice uses warn). */
export function scrTone(scr: EhScrVerdict): string {
  if (scr === 'pass') return 'text-accent'
  if (scr === 'fail') return 'text-danger'
  return 'text-warn'
}

/** True when the report carries SCR/EMT values or an honest gates note. */
export function hasGatesBlock(report: EhReferenceDesignReport): boolean {
  const gate = report.gates
  if (gate?.scr != null) return true
  if (gate?.emt_recommended != null) return true
  const section = report.sections?.gates
  if (section?.note) return true
  const payload = section?.payload
  if (payload && typeof payload.min_scr === 'number') return true
  return false
}


/** True when multi-energy ENS is established or fail-closed with a note. */
export function hasMultiEnergyBlock(report: EhReferenceDesignReport): boolean {
  const section = report.sections?.multi_energy
  if (!section) return false
  if (section.status === 'skipped') return false
  if (section.note) return true
  const payload = section.payload
  if (payload && typeof payload === 'object') {
    const by = (payload as { ens_by_carrier_mwh?: unknown }).ens_by_carrier_mwh
    if (by && typeof by === 'object') return true
    const v = (payload as { violations?: unknown }).violations
    if (Array.isArray(v) && v.length > 0) return true
  }
  return section.status === 'ok' || section.status === 'not_established'
}

/** Carrier → MWh pairs from the multi_energy section payload. */
export function multiEnergyCarrierEntries(
  report: EhReferenceDesignReport,
): { carrier: string; mwh: number }[] {
  const payload = report.sections?.multi_energy?.payload
  if (!payload || typeof payload !== 'object') return []
  const by = (payload as { ens_by_carrier_mwh?: unknown }).ens_by_carrier_mwh
  if (!by || typeof by !== 'object') return []
  return Object.entries(by as Record<string, unknown>)
    .filter(([, v]) => typeof v === 'number' && Number.isFinite(v))
    .map(([carrier, mwh]) => ({ carrier, mwh: Number(mwh) }))
}

/** Load → MWh pairs from per-Load slack capture (P6b). */
export function multiEnergyLoadEntries(
  report: EhReferenceDesignReport,
): { load: string; mwh: number }[] {
  const payload = report.sections?.multi_energy?.payload
  if (!payload || typeof payload !== 'object') return []
  const by = (payload as { ens_by_load_mwh?: unknown }).ens_by_load_mwh
  if (!by || typeof by !== 'object') return []
  return Object.entries(by as Record<string, unknown>)
    .filter(([, v]) => typeof v === 'number' && Number.isFinite(v) && Number(v) > 0)
    .map(([load, mwh]) => ({ load, mwh: Number(mwh) }))
}

/** CSV rows for the redundancy comparison table. */
export function redundancyCsvRows(table: EhRedundancyTable): unknown[][] {
  const selected = table.selection?.selected_id ?? ''
  return (table.options ?? []).map(o => [
    o.scenario_id,
    o.status,
    o.cost_at_target_eur ?? '',
    o.achieved_ens_mwh ?? '',
    o.meets_target == null ? '' : o.meets_target ? 'yes' : 'no',
    o.scenario_id === selected ? 'selected' : '',
    o.condition ?? '',
  ])
}

/** CSV rows for lever options. */
export function leverCsvRows(table: EhLeverTable): unknown[][] {
  return (table.options ?? []).map(o => [
    o.kind,
    o.value,
    o.unit ?? '',
    o.status,
    o.cost_at_target_eur ?? '',
    o.achieved_ens_mwh ?? '',
    o.meets_target == null ? '' : o.meets_target ? 'yes' : 'no',
    o.ineffective ? (o.ineffective_reason ?? 'ineffective') : '',
  ])
}

/** CSV rows for DtC stress contingencies. */
export function dtcStressCsvRows(table: EhDtcStressTable): unknown[][] {
  return (table.contingencies ?? []).map(c => [
    c.contingency,
    c.status,
    c.critical_unserved_mwh ?? '',
    c.noncritical_unserved_mwh ?? '',
    c.condition ?? '',
  ])
}

/** CSV rows for DtC planning contingencies. */
export function dtcPlanningCsvRows(table: EhDtcPlanningTable): unknown[][] {
  return (table.contingencies ?? []).map(c => [
    c.contingency,
    c.status,
    c.cost_at_target_eur ?? '',
    c.built_p_nom_mw ?? '',
    c.condition ?? '',
  ])
}

function CsvButton({
  testId, label, onClick, disabled,
}: { testId: string; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      className="px-2 py-0.5 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-40"
    >
      {label}
    </button>
  )
}

export function EhReferenceDesignPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [archetype, setArchetype] = useState<EhArchetype>('strong_grid')
  const [blocked, setBlocked] = useState<string | null>(null)

  const studyKey = nk(currentProject, 'results', 'eh_study')
  const reportKey = nk(currentProject, 'results', 'eh_reference_design')
  const redKey = nk(currentProject, 'results', 'eh_redundancy')
  const levKey = nk(currentProject, 'results', 'eh_levers')
  const dtcKey = nk(currentProject, 'results', 'eh_dtc')
  const dtcPlanKey = nk(currentProject, 'results', 'eh_dtc_planning')

  const { data: studyData } = useQuery({
    queryKey: studyKey,
    queryFn: () => resultsApi.getEhStudy(),
    refetchInterval: (q) =>
      (q.state.data as EhStudyPayload | null)?.status === 'running' ? 2000 : false,
  })
  const study = (studyData ?? null) as EhStudyPayload | null
  const running = study?.status === 'running'

  const { data: reportData } = useQuery({
    queryKey: reportKey,
    queryFn: () => resultsApi.getEhReferenceDesign(),
    enabled: !running,
  })
  const { data: redundancy } = useQuery({
    queryKey: redKey,
    queryFn: () => resultsApi.getEhRedundancy(),
    enabled: !running,
  })
  const { data: levers } = useQuery({
    queryKey: levKey,
    queryFn: () => resultsApi.getEhLevers(),
    enabled: !running,
  })
  const { data: dtcStress } = useQuery({
    queryKey: dtcKey,
    queryFn: () => resultsApi.getEhDtc(),
    enabled: !running,
  })
  const { data: dtcPlanning } = useQuery({
    queryKey: dtcPlanKey,
    queryFn: () => resultsApi.getEhDtcPlanning(),
    enabled: !running,
  })

  // While a new study runs, the previous report and sibling tables describe
  // a different run — hide them rather than show them under "Studying…".
  const report: EhReferenceDesignReport | null = running ? null
    : study?.report
      ?? ((reportData ?? null) as EhReferenceDesignReport | null)

  const redTable = running ? null : (redundancy ?? null) as EhRedundancyTable | null
  const levTable = running ? null : (levers ?? null) as EhLeverTable | null
  const dtcTable = running ? null : (dtcStress ?? null) as EhDtcStressTable | null
  const dtcPlanTable = running ? null
    : (dtcPlanning ?? null) as EhDtcPlanningTable | null

  const invalidateAll = () => {
    for (const key of [studyKey, reportKey, redKey, levKey, dtcKey, dtcPlanKey]) {
      void qc.invalidateQueries({ queryKey: key })
    }
  }

  const run = useMutation({
    mutationFn: () => resultsApi.startEhStudy({ archetype }),
    onMutate: () => setBlocked(null),
    onSuccess: () => invalidateAll(),
    onError: (e: unknown) => setBlocked(blockerMessage(e)),
  })

  const abort = useMutation({
    mutationFn: () => resultsApi.abortEhStudy(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: studyKey }),
  })

  const completeness = useMemo(
    () => completenessRows(report?.completeness),
    [report?.completeness],
  )
  const meCarriers = report ? multiEnergyCarrierEntries(report) : []
  const meLoads = report ? multiEnergyLoadEntries(report) : []

  const selected = ARCHETYPES.find(a => a.id === archetype)!
  const selectedId = redTable?.selection?.selected_id ?? null
  const hasAnyTable = Boolean(
    (redTable?.options?.length)
    || (levTable?.options?.length)
    || (dtcTable?.contingencies?.length)
    || (dtcPlanTable?.contingencies?.length),
  )

  return (
    <section
      className="border border-border rounded"
      data-testid="eh-reference-design-panel"
    >
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        data-testid="eh-reference-design-toggle"
        className="w-full flex items-center gap-2 px-3 py-1.5 border-b border-border bg-panel text-[10px] font-semibold uppercase tracking-wide text-muted hover:text-accent"
      >
        <Hexagon size={11} /> Reference design {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="p-3 flex flex-col gap-3">
          <p className="text-[11px] text-muted">
            Runs an Energy Hub archetype pack through the reference-design
            pipeline (apply pack → ENS solve → assemble, plus any levers the
            pack enables). Produces one{' '}
            <code className="font-mono">ReferenceDesignReport</code> linking
            availability and cost. Sibling tables (redundancy, levers, DtC)
            appear when those stages ran. Shares the study mesh — one study at
            a time.
          </p>

          <label className="flex flex-col gap-1 text-[10px] text-muted">
            <span className="uppercase tracking-wide font-semibold">Archetype</span>
            <select
              data-testid="eh-archetype"
              value={archetype}
              disabled={running}
              onChange={e => setArchetype(e.target.value as EhArchetype)}
              className="bg-bg border border-border rounded px-2 py-1 text-[11px] text-text"
            >
              {ARCHETYPES.map(a => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
            <span className="text-muted">{selected.blurb}</span>
          </label>

          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => run.mutate()}
              disabled={running}
              data-testid="eh-run"
              className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50"
            >
              {running ? 'Studying…' : 'Run study'}
            </button>
            {running && (
              <button
                type="button"
                onClick={() => abort.mutate()}
                data-testid="eh-abort"
                className="inline-flex items-center gap-1 px-2 py-1 border border-border rounded text-[10px] text-muted hover:border-danger hover:text-danger"
                title="Stops at the next stage boundary; the pack overlay is restored."
              >
                <Square size={9} /> Abort
              </button>
            )}
            {study?.status === 'aborted' && (
              <span className="text-[10px] text-warn" data-testid="eh-aborted">
                Stopped — the report reflects stages that finished before abort,
                not a full reference design.
              </span>
            )}
            {blocked && (
              <span className="text-[10px] text-warn" data-testid="eh-blocked">
                Blocked: {blocked}
              </span>
            )}
            {study?.error && (
              <span className="text-[10px] text-danger" data-testid="eh-error">
                {study.error}
              </span>
            )}
          </div>

          {!study && !report && !hasAnyTable && (
            <p className="text-[10px] text-muted" data-testid="eh-not-run">
              No Energy Hub study has been run in this session yet. Pick an
              archetype and run the study to get a reference-design report.
            </p>
          )}

          {report && (
            <div
              className="flex flex-col gap-2 border border-border/60 rounded p-2"
              data-testid="eh-report"
            >
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                <span data-testid="eh-report-archetype">
                  <span className="text-muted">Archetype </span>
                  <span className="text-text font-medium">{report.archetype}</span>
                </span>
                {report.ens_cap_permyriad != null && (
                  <span data-testid="eh-report-ens-cap">
                    <span className="text-muted">ENS cap </span>
                    <span className="text-text font-mono">
                      {report.ens_cap_permyriad}‱
                    </span>
                  </span>
                )}
                {report.achieved_ens_permyriad != null && (
                  <span data-testid="eh-report-ens-achieved">
                    <span className="text-muted">Achieved </span>
                    <span className="text-text font-mono">
                      {Number(report.achieved_ens_permyriad).toFixed(2)}‱
                    </span>
                  </span>
                )}
                {report.cost_at_target_eur != null && (
                  <span data-testid="eh-report-cost">
                    <span className="text-muted">Cost@target </span>
                    <span className="text-text font-mono">
                      {eur(report.cost_at_target_eur)}
                    </span>
                    {report.excludes_shed_cost !== false && (
                      <span className="text-muted"> excl. shed</span>
                    )}
                  </span>
                )}
                {report.pipeline?.solves_consumed != null && (
                  <span data-testid="eh-report-solves">
                    <span className="text-muted">Solves </span>
                    <span className="text-text font-mono">
                      {report.pipeline.solves_consumed}
                      {report.pipeline.budget_solves != null
                        ? ` / ${report.pipeline.budget_solves}` : ''}
                    </span>
                  </span>
                )}
                {report.tea?.lcoe_eur_per_mwh != null && (
                  <span data-testid="eh-report-lcoe">
                    <span className="text-muted">LCOE </span>
                    <span className="text-text font-mono">
                      {eur(report.tea.lcoe_eur_per_mwh)}/MWh
                    </span>
                  </span>
                )}
              </div>

              {completeness.length > 0 && (
                <ul
                  className="flex flex-wrap gap-1.5"
                  data-testid="eh-completeness"
                >
                  {completeness.map(({ name, status }) => (
                    <li
                      key={name}
                      className={`text-[10px] border border-border rounded px-1.5 py-0.5 ${statusTone(status)}`}
                      data-testid={`eh-section-${name}`}
                      data-status={status}
                      title={report.sections?.[name]?.note ?? undefined}
                    >
                      {name}: {status}
                    </li>
                  ))}
                </ul>
              )}

              {notEstablishedNotes(report).length > 0 && (
                <ul
                  className="flex flex-col gap-0.5 text-[10px] text-muted"
                  data-testid="eh-section-notes"
                >
                  {notEstablishedNotes(report).map(({ name, note }) => (
                    <li key={name} data-testid={`eh-section-note-${name}`}>
                      <span className="text-warn">{name}</span>: {note}
                    </li>
                  ))}
                </ul>
              )}

              {hasGatesBlock(report) && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-gates"
                >
                  <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Dynamics gate
                  </h4>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                    {report.gates?.scr != null && (
                      <span
                        data-testid="eh-gates-scr"
                        data-scr={report.gates.scr}
                        className={scrTone(report.gates.scr)}
                      >
                        <span className="text-muted">SCR </span>
                        <span className="font-mono font-medium">
                          {report.gates.scr}
                        </span>
                      </span>
                    )}
                    {report.gates?.emt_recommended != null && (
                      <span data-testid="eh-gates-emt">
                        <span className="text-muted">EMT </span>
                        <span className="text-text">
                          {report.gates.emt_recommended
                            ? 'recommended'
                            : 'no'}
                        </span>
                      </span>
                    )}
                    {typeof report.sections?.gates?.payload?.min_scr === 'number' && (
                      <span data-testid="eh-gates-min-scr">
                        <span className="text-muted">min SCR </span>
                        <span className="text-text font-mono">
                          {Number(report.sections.gates.payload.min_scr).toFixed(2)}
                          {typeof report.sections.gates.payload.pass_scr === 'number'
                            ? ` (pass ≥ ${report.sections.gates.payload.pass_scr})`
                            : ''}
                        </span>
                      </span>
                    )}
                  </div>
                  {report.sections?.gates?.note && (
                    <p
                      className="text-[10px] text-muted"
                      data-testid="eh-gates-note"
                    >
                      {report.sections.gates.note}
                    </p>
                  )}
                </div>
              )}

              {hasMultiEnergyBlock(report) && (
                <div
                  className="flex flex-col gap-1 border-t border-border/50 pt-2"
                  data-testid="eh-multi-energy"
                >
                  <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                    Multi-energy ENS
                  </h4>
                  {meCarriers.length > 0 && (
                    <div
                      className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]"
                      data-testid="eh-multi-energy-by-carrier"
                    >
                      {meCarriers.map(({ carrier, mwh }) => (
                        <span key={carrier} data-testid={`eh-multi-energy-${carrier}`}>
                          <span className="text-muted">{carrier} </span>
                          <span className="text-text font-mono">
                            {mwh.toFixed(2)} MWh
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                  {meLoads.length > 0 && (
                    <div
                      className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]"
                      data-testid="eh-multi-energy-by-load"
                    >
                      {meLoads.map(({ load, mwh }) => (
                        <span key={load} data-testid={`eh-multi-energy-load-${load}`}>
                          <span className="text-muted">{load} </span>
                          <span className="text-text font-mono">
                            {mwh.toFixed(2)} MWh
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                  {typeof report.sections?.multi_energy?.payload?.attribution === 'string' && (
                    <p className="text-[10px] text-muted" data-testid="eh-multi-energy-attribution">
                      attribution: {String(report.sections.multi_energy.payload.attribution)}
                    </p>
                  )}
                  {report.sections?.multi_energy?.note && (
                    <p
                      className="text-[10px] text-muted"
                      data-testid="eh-multi-energy-note"
                    >
                      {report.sections.multi_energy.note}
                    </p>
                  )}
                </div>
              )}

            </div>
          )}

          {/* ── Redundancy ─────────────────────────────────────────────── */}
          {redTable?.options && redTable.options.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-redundancy">
              <div className="flex items-center gap-2">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  Redundancy
                </h4>
                {selectedId && (
                  <span className="text-[10px] text-accent" data-testid="eh-redundancy-selected">
                    selected: {selectedId}
                  </span>
                )}
                <CsvButton
                  testId="eh-redundancy-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-redundancy.csv',
                    ['scenario_id', 'status', 'cost_at_target_eur',
                     'achieved_ens_mwh', 'meets_target', 'selection', 'condition'],
                    redundancyCsvRows(redTable),
                  )}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Scenario</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Cost</th>
                      <th className="text-right font-medium py-1 pr-3">ENS MWh</th>
                      <th className="text-left font-medium py-1">Meets</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {redTable.options.map(o => (
                      <tr
                        key={o.scenario_id}
                        className="border-t border-border/50"
                        data-testid={`eh-redundancy-row-${o.scenario_id}`}
                        data-selected={o.scenario_id === selectedId ? 'true' : 'false'}
                      >
                        <td className="py-0.5 pr-3 font-sans">{o.scenario_id}</td>
                        <td className="py-0.5 pr-3 font-sans">{o.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {o.cost_at_target_eur != null ? eur(o.cost_at_target_eur) : '—'}
                        </td>
                        <td className="py-0.5 pr-3 text-right">
                          {cell(o.achieved_ens_mwh)}
                        </td>
                        <td className="py-0.5 font-sans">
                          {o.meets_target == null ? '—' : o.meets_target ? 'yes' : 'no'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── Levers ─────────────────────────────────────────────────── */}
          {levTable?.options && levTable.options.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-levers">
              <div className="flex items-center gap-2 flex-wrap">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  Levers{levTable.kind ? ` (${levTable.kind})` : ''}
                </h4>
                <CsvButton
                  testId="eh-levers-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-levers.csv',
                    ['kind', 'value', 'unit', 'status', 'cost_at_target_eur',
                     'achieved_ens_mwh', 'meets_target', 'note'],
                    leverCsvRows(levTable),
                  )}
                />
              </div>
              {(levTable.skipped_kinds?.length ?? 0) > 0 && (
                <p className="text-[10px] text-warn" data-testid="eh-levers-skipped">
                  Soft-skipped: {levTable.skipped_kinds!.join('; ')}
                </p>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Kind</th>
                      <th className="text-right font-medium py-1 pr-3">Value</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Cost</th>
                      <th className="text-left font-medium py-1">Note</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {levTable.options.map((o, i) => (
                      <tr
                        key={`${o.kind}:${o.value}:${i}`}
                        className="border-t border-border/50"
                        data-testid={`eh-lever-row-${i}`}
                      >
                        <td className="py-0.5 pr-3 font-sans">{o.kind}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {o.value}{o.unit ? ` ${o.unit}` : ''}
                        </td>
                        <td className="py-0.5 pr-3 font-sans">{o.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {o.cost_at_target_eur != null ? eur(o.cost_at_target_eur) : '—'}
                        </td>
                        <td className="py-0.5 font-sans text-muted">
                          {o.ineffective
                            ? (o.ineffective_reason ?? 'ineffective')
                            : (o.meets_target == null
                              ? ''
                              : o.meets_target ? 'meets' : 'miss')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── DtC stress ─────────────────────────────────────────────── */}
          {dtcTable?.contingencies && dtcTable.contingencies.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-dtc-stress">
              <div className="flex items-center gap-2 flex-wrap">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  DtC stress
                </h4>
                {dtcTable.attribution && (
                  <span className="text-[10px] text-muted" data-testid="eh-dtc-attribution">
                    {dtcTable.attribution}
                  </span>
                )}
                <CsvButton
                  testId="eh-dtc-stress-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-dtc-stress.csv',
                    ['contingency', 'status', 'critical_unserved_mwh',
                     'noncritical_unserved_mwh', 'condition'],
                    dtcStressCsvRows(dtcTable),
                  )}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Contingency</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Critical MWh</th>
                      <th className="text-right font-medium py-1">Other MWh</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {dtcTable.contingencies.map(c => (
                      <tr
                        key={c.contingency}
                        className="border-t border-border/50"
                        data-testid={`eh-dtc-stress-row-${c.contingency}`}
                      >
                        <td className="py-0.5 pr-3 font-sans">{c.contingency}</td>
                        <td className="py-0.5 pr-3 font-sans">{c.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {cell(c.critical_unserved_mwh)}
                        </td>
                        <td className="py-0.5 text-right">
                          {cell(c.noncritical_unserved_mwh)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── DtC planning ───────────────────────────────────────────── */}
          {dtcPlanTable?.contingencies && dtcPlanTable.contingencies.length > 0 && (
            <div className="flex flex-col gap-1.5" data-testid="eh-dtc-planning">
              <div className="flex items-center gap-2">
                <h4 className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  DtC planning
                </h4>
                <CsvButton
                  testId="eh-dtc-planning-csv"
                  label="CSV"
                  onClick={() => downloadCSV(
                    'eh-dtc-planning.csv',
                    ['contingency', 'status', 'cost_at_target_eur',
                     'built_p_nom_mw', 'condition'],
                    dtcPlanningCsvRows(dtcPlanTable),
                  )}
                />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-[10px]">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-medium py-1 pr-3">Contingency</th>
                      <th className="text-left font-medium py-1 pr-3">Status</th>
                      <th className="text-right font-medium py-1 pr-3">Cost</th>
                      <th className="text-right font-medium py-1">Built MW</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {dtcPlanTable.contingencies.map(c => (
                      <tr
                        key={c.contingency}
                        className="border-t border-border/50"
                        data-testid={`eh-dtc-planning-row-${c.contingency}`}
                      >
                        <td className="py-0.5 pr-3 font-sans">{c.contingency}</td>
                        <td className="py-0.5 pr-3 font-sans">{c.status}</td>
                        <td className="py-0.5 pr-3 text-right">
                          {c.cost_at_target_eur != null
                            ? eur(c.cost_at_target_eur) : '—'}
                        </td>
                        <td className="py-0.5 text-right">
                          {cell(c.built_p_nom_mw)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
