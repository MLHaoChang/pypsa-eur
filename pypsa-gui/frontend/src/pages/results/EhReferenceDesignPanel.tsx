import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hexagon, Square } from 'lucide-react'
import {
  resultsApi,
  type EhArchetype,
  type EhReferenceDesignReport,
  type EhSectionStatus,
  type EhStudyPayload,
} from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import { blockerMessage } from './McPanel'

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

/** Stable display order for completeness chips (matches REPORT_SECTIONS). */
export const COMPLETENESS_ORDER = [
  'target', 'cost', 'frontier', 'sizing', 'redundancy', 'levers', 'dtc',
  'fmea_top', 'tea', 'gates',
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

export function EhReferenceDesignPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [archetype, setArchetype] = useState<EhArchetype>('strong_grid')
  const [blocked, setBlocked] = useState<string | null>(null)

  const studyKey = nk(currentProject, 'results', 'eh_study')
  const reportKey = nk(currentProject, 'results', 'eh_reference_design')

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
    // Refresh when a study finishes so the durable report lands even if the
    // study record omitted an embedded copy.
    enabled: !running,
  })

  const report: EhReferenceDesignReport | null =
    study?.report
    ?? ((reportData ?? null) as EhReferenceDesignReport | null)

  const run = useMutation({
    mutationFn: () => resultsApi.startEhStudy({ archetype }),
    onMutate: () => setBlocked(null),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: studyKey })
      void qc.invalidateQueries({ queryKey: reportKey })
    },
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

  const selected = ARCHETYPES.find(a => a.id === archetype)!

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
            availability and cost. Shares the study mesh with frontier, MC and
            the planning loops — only one study at a time.
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

          {!study && !report && (
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
                    >
                      {name}: {status}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
