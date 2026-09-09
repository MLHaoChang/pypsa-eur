// The planning → dynamics study view (increment 4, task 7).
//
// Thin by design — the spec calls GUI wiring "a thin, late, path-limited
// backend change". Everything shown here is READ from the backend's own
// answers (`/api/gridspine/{name}/status|snapshots|ledger`) and every action is
// one POST; the panel derives nothing the copilot could not also see through
// the `gridspine_*` tools, so the two surfaces cannot disagree.
//
// Polling: the status query refetches every 1.5 s only while this project has
// an active solve-queue job or the status itself says `running` — the same
// rule `useSolveQueue` applies to the queue list — and stops on any terminal
// state. A study is a `kind: 'gridspine'` job in the ordinary queue, so
// watching and aborting it are the Solve Queue panel's job, not this one's.
import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Play, RefreshCw, FlaskConical, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  gridspineApi, isNotAStudy, STAGES,
  type DispatchSource, type FigureName, type RankedSnapshot, type ReadbackShort, type StageState, type StageStatus,
  type StudyConfig, type StudyConfigPatch,
} from '../api/gridspine'
import { projectsApi } from '../api/projects'
import { formatApiDetail } from '../api/client'
import { useUIStore } from '../store/uiStore'
import { useSolveQueue, activeJobForProject, QUEUE_KEY } from '../hooks/useSolveQueue'
import { PageBody, PageSection, Btn, Tag, Field } from '../components/PageKit'

export const STATUS_KEY = (name: string) => ['gridspine', 'status', name] as const
export const SNAPSHOTS_KEY = (name: string) => ['gridspine', 'snapshots', name] as const
export const LEDGER_KEY = (name: string) => ['gridspine', 'ledger', name] as const
export const CONFIG_KEY = (name: string) => ['gridspine', 'config', name] as const
export const FIGURE_KEY = (name: string, hour: number, figure: FigureName) => ['gridspine', 'figure', name, hour, figure] as const

const STAGE_LABEL: Record<string, string> = {
  ingest: 'Ingest', dispatch: 'Unit commitment', ranking: 'Ranking (AC N-1 at every hour)',
  loadflow: 'Load flow', screening: 'N-1 / N-2 screening + fault levels', handoff: 'Handoff bundles',
}

const STATE_TONE: Record<StageState, 'neutral' | 'accent' | 'ok' | 'err' | 'warn'> = {
  pending: 'neutral', running: 'accent', done: 'ok', failed: 'err', aborted: 'warn',
}

const REASON_LABEL: Record<string, string> = {
  min_inertia_excl_equiv_mws: 'min inertia',
  max_ibr_share: 'max IBR share',
  max_load_mw: 'peak load',
  max_import_mw: 'max import',
  max_n1_severity: 'worst N-1',
}

function fmt(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return '—'
  return v.toFixed(digits)
}

function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return formatApiDetail(detail, (e as Error)?.message ?? 'Request failed')
}

export default function GridspinePanel() {
  const currentProject = useUIStore(s => s.currentProject)
  if (!currentProject) {
    return (
      <PageBody>
        <PageSection title="Planning → dynamics">
          <p className="text-[12px] text-muted">
            Open a planning → dynamics project, or create one with New project → Study.
          </p>
        </PageSection>
      </PageBody>
    )
  }
  return <StudyView name={currentProject} />
}

function StudyView({ name }: { name: string }) {
  const qc = useQueryClient()
  const { data: queue } = useSolveQueue()
  const activeJob = activeJobForProject(queue, name)

  const status = useQuery({
    queryKey: STATUS_KEY(name),
    queryFn: () => gridspineApi.status(name),
    retry: false,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return activeJob != null || s === 'running' ? 1500 : false
    },
  })

  const notAStudy = status.isError && isNotAStudy(status.error)
  const done = status.data?.status === 'completed'

  const snapshots = useQuery({
    queryKey: SNAPSHOTS_KEY(name),
    queryFn: () => gridspineApi.snapshots(name),
    enabled: done,
    retry: false,
  })
  const ledger = useQuery({
    queryKey: LEDGER_KEY(name),
    queryFn: () => gridspineApi.ledger(name),
    enabled: status.isSuccess,
    retry: false,
  })
  const config = useQuery({
    queryKey: CONFIG_KEY(name),
    queryFn: () => gridspineApi.config(name),
    enabled: status.isSuccess,
    retry: false,
  })

  const run = useMutation({
    mutationFn: () => gridspineApi.run(name),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: QUEUE_KEY })
      qc.invalidateQueries({ queryKey: STATUS_KEY(name) })
      toast.success(job.position != null ? `Study queued (#${job.position} in line)` : 'Study started')
    },
    onError: (e) => toast.error(`Could not start the study: ${errorText(e)}`),
  })

  if (notAStudy) {
    return (
      <PageBody>
        <PageSection title="Not a planning → dynamics project">
          <p className="text-[12px] text-muted">
            <b>{name}</b> is a capacity-expansion project. Studies are their own project kind:
            create one with New project → Study, then open it here.
          </p>
        </PageSection>
      </PageBody>
    )
  }

  const canRun = !activeJob && status.isSuccess && status.data.status !== 'running'

  return (
    <PageBody>
      <PageSection
        title={<span className="inline-flex items-center gap-2"><FlaskConical size={13} /> {name}</span>}
        hint={status.data ? `status: ${status.data.status}` : undefined}
        right={
          <div className="flex items-center gap-2">
            <Btn onClick={() => status.refetch()} title="Refresh" aria-label="Refresh status">
              <RefreshCw size={12} />
            </Btn>
            <Btn
              variant="primary"
              onClick={() => run.mutate()}
              disabled={!canRun || run.isPending}
              title={activeJob ? 'A job for this project is already queued or running' : 'Queue the study'}
            >
              <Play size={12} /> {status.data?.resumable && status.data.status !== 'completed' ? 'Resume' : 'Run study'}
            </Btn>
          </div>
        }
      >
        {status.isError && !notAStudy && (
          <p className="text-[12px] text-danger">{errorText(status.error)}</p>
        )}
        {status.data && <Stages status={status.data} />}
        {status.data?.error && (
          <p className="mt-2 text-[11px] text-danger" data-testid="study-error">
            {status.data.error.stage}: {status.data.error.cause}
          </p>
        )}
      </PageSection>

      {config.data && (
        <ConfigEditor
          name={name}
          config={config.data}
          locked={activeJob != null || status.data?.status === 'running'}
        />
      )}

      {config.data && (
        <DispatchSourcePicker
          name={name}
          config={config.data}
          locked={activeJob != null || status.data?.status === 'running'}
        />
      )}

      {done && (
        <PageSection
          title="Ranked snapshots"
          count={snapshots.data?.length}
          hint="The union of the k most extreme hours under each criterion"
        >
          {snapshots.data ? (
            <SnapshotTable rows={snapshots.data} name={name} bundles={status.data?.bundles ?? {}} />
          ) : (
            <p className="text-[12px] text-muted">{snapshots.isError ? errorText(snapshots.error) : 'Loading…'}</p>
          )}
        </PageSection>
      )}

      {ledger.data && (
        <PageSection
          title="Assumptions ledger"
          hint={ledger.data.from_run === false ? 'From the templates this study will use — no run yet' : 'From the latest run'}
        >
          <div className="flex items-center gap-2 mb-2" data-testid="provenance-counts">
            <Tag tone="ok">{ledger.data.provenance_counts.measured} measured</Tag>
            <Tag tone="accent">{ledger.data.provenance_counts.datasheet} datasheet</Tag>
            <Tag tone="warn">{ledger.data.provenance_counts.assumed} assumed</Tag>
            <span className="text-[11px] text-muted">{ledger.data.entries.length} ledger entries</span>
          </div>
          {ledger.data.edits.length > 0 && (
            <ul className="text-[11px] text-muted list-disc pl-4" data-testid="template-edits">
              {ledger.data.edits.map(e => (
                <li key={`${e.unit_id}.${e.param}`}>
                  {e.unit_id}.{e.param} = {e.value} ({e.source}, edited by {e.edited_by})
                </li>
              ))}
            </ul>
          )}
        </PageSection>
      )}

      {done && status.data && Object.keys(status.data.bundles).length > 0 && (
        <ReadbackSection name={name} status={status.data} />
      )}
    </PageBody>
  )
}

// Where the next run's dispatch comes from (increment 5, D3 added the third
// option). Three sources, one at a time: generate it here (case39 + the
// rolling unit commitment), reuse a finished study's tables, or study the
// SOLVED network of one of the user's own projects — the flow the product is
// for: solve a capacity-expansion project, save it, study its dispatch. The
// project list is the ordinary projects query; studies are not offered (a
// study has no network) and an unsolved project is shown but not selectable,
// with the reason, because the backend would refuse it with the same reason.
type SourceKind = DispatchSource['kind']

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'

function currentSourceKind(config: StudyConfig): SourceKind {
  if (config.from_network) return 'from_project'
  if (config.from_dispatch) return 'from_dispatch'
  return 'generate'
}

function describeSource(config: StudyConfig): string {
  if (config.from_network) return `the solved network of ${config.from_project ?? config.from_network}`
  if (config.from_dispatch) return `the dispatch in ${config.from_dispatch}`
  return 'generated here (IEEE 39-bus, rolling unit commitment)'
}

function DispatchSourcePicker({ name, config, locked }: { name: string; config: StudyConfig; locked: boolean }) {
  const qc = useQueryClient()
  const [kind, setKind] = useState<SourceKind | null>(null)
  const [dir, setDir] = useState(config.from_dispatch ?? '')
  const [project, setProject] = useState(config.from_project ?? '')
  const mode: SourceKind = kind ?? currentSourceKind(config)

  const projects = useQuery({ queryKey: ['projects'], queryFn: () => projectsApi.list(), enabled: mode === 'from_project' })
  const candidates = (projects.data ?? []).filter(p => p.project_kind !== 'planning_dynamics' && p.name !== name)

  const source: DispatchSource | null =
    mode === 'generate' ? { kind: 'generate' }
    : mode === 'from_dispatch' ? (dir.trim() ? { kind: 'from_dispatch', dir: dir.trim() } : null)
    : (project ? { kind: 'from_project', project } : null)

  const apply = useMutation({
    mutationFn: (s: DispatchSource) => gridspineApi.setDispatchSource(name, s),
    onSuccess: (cfg) => {
      qc.setQueryData(CONFIG_KEY(name), cfg)
      setKind(null)
      toast.success(`Dispatch source: ${describeSource(cfg)}`)
    },
    onError: (e) => toast.error(errorText(e)),
  })

  return (
    <PageSection title="Dispatch source" hint={locked ? 'Locked while the study is queued or running' : 'Applies to the next run'}>
      <p className="text-[11px] text-muted mb-2" data-testid="dispatch-source-current">
        Current: {describeSource(config)}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Source">
          <select
            className={`${INPUT} w-[260px]`}
            value={mode}
            disabled={locked}
            aria-label="Dispatch source"
            onChange={e => setKind(e.target.value as SourceKind)}
          >
            <option value="generate">Generate here (unit commitment)</option>
            <option value="from_project">A solved project's network</option>
            <option value="from_dispatch">A finished study's directory</option>
          </select>
        </Field>
        {mode === 'from_dispatch' && (
          <Field label="Study directory (holding dispatch.csv and loads.csv)">
            <input
              className={`${INPUT} font-mono w-[380px]`}
              value={dir}
              disabled={locked}
              onChange={e => setDir(e.target.value)}
              placeholder="e.g. /path/to/other-study/gridspine/run"
              aria-label="Dispatch source directory"
            />
          </Field>
        )}
        {mode === 'from_project' && (
          <Field label="Project (solved and saved; generators must be the IEEE 39-bus units)">
            <select
              className={`${INPUT} w-[300px]`}
              value={project}
              disabled={locked}
              aria-label="Source project"
              onChange={e => setProject(e.target.value)}
            >
              <option value="">{projects.isLoading ? 'Loading projects…' : 'Choose a project'}</option>
              {candidates.map(p => (
                <option key={p.name} value={p.name} disabled={p.objective == null}>
                  {p.name}{p.objective == null ? ' (not solved yet)' : ''}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Btn onClick={() => source && apply.mutate(source)} disabled={locked || !source || apply.isPending}>Apply</Btn>
      </div>
    </PageSection>
  )
}

// The editable part of the study config, after creation. Only the fields the
// user actually changed are PUT — the backend merges a patch, and sending the
// whole form back would silently overwrite a value the copilot changed in
// between. Locked while a job is queued or running: the backend answers 409
// then, and a disabled form says why before the request rather than after.
const NUMERIC_FIELDS: readonly { key: 'hours' | 'k' | 'window' | 'overlap' | 'n2_prune_threshold_pct'; label: string; step?: string }[] = [
  { key: 'hours', label: 'Hours' },
  { key: 'k', label: 'k (hours per criterion)' },
  { key: 'window', label: 'UC window (h)' },
  { key: 'overlap', label: 'UC overlap (h)' },
  { key: 'n2_prune_threshold_pct', label: 'N-2 prune threshold (%)', step: '0.1' },
]

function ConfigEditor({ name, config, locked }: { name: string; config: StudyConfig; locked: boolean }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<StudyConfigPatch>({})
  const dirty = Object.keys(draft).length > 0

  const save = useMutation({
    mutationFn: () => gridspineApi.updateConfig(name, draft),
    onSuccess: (cfg) => {
      qc.setQueryData(CONFIG_KEY(name), cfg)
      setDraft({})
      toast.success('Study configuration saved')
    },
    onError: (e) => toast.error(`Could not save the configuration: ${errorText(e)}`),
  })

  const value = <K extends keyof StudyConfigPatch>(key: K): StudyConfig[K] =>
    (key in draft ? draft[key] : config[key]) as StudyConfig[K]

  return (
    <PageSection
      title="Configuration"
      hint={locked ? 'Locked while the study is queued or running' : 'Applies to the next run'}
      right={
        <Btn
          variant="primary"
          onClick={() => save.mutate()}
          disabled={locked || !dirty || save.isPending}
          title={locked ? 'A job for this project is queued or running' : dirty ? 'Save the changed fields' : 'Nothing changed'}
        >
          Save
        </Btn>
      }
    >
      <div className="flex flex-wrap items-end gap-3" data-testid="config-editor">
        {NUMERIC_FIELDS.map(f => (
          <Field key={f.key} label={f.label}>
            <input
              type="number"
              step={f.step}
              className="px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 font-mono w-[120px]"
              value={value(f.key)}
              disabled={locked}
              aria-label={f.label}
              onChange={e => {
                const n = Number(e.target.value)
                setDraft(d => ({ ...d, [f.key]: Number.isFinite(n) ? n : config[f.key] }))
              }}
            />
          </Field>
        ))}
        <label className="inline-flex items-center gap-2 text-[12px] pb-2">
          <input
            type="checkbox"
            checked={value('screen')}
            disabled={locked}
            aria-label="Screen N-1 / N-2 at the selected hours"
            onChange={e => setDraft(d => ({ ...d, screen: e.target.checked }))}
          />
          Screen N-1 / N-2 at the selected hours
        </label>
      </div>
    </PageSection>
  )
}

// Spec stage 6 (increment 6): the engineer imports a bundle's .raw into
// PowerFactory, runs the load flow, exports the bus CSV (and, same session,
// the branch CSV) the fixture runbook specifies, and uploads them here. The
// backend compares them with the bundle's OWN load flow and records the
// verdict in the bundle; this section shows that verdict per hour and the
// per-element comparison on demand. Nothing is computed client-side.
const FIGURE_LABEL: Record<FigureName, string> = {
  vm: '|V| (p.u.)', va: 'angle (°)', branch_p: 'P from-end (MW)', branch_q: 'Q from-end (Mvar)',
}

function verdict(short: ReadbackShort | undefined): { tone: 'ok' | 'err' | 'neutral'; text: string } {
  if (!short) return { tone: 'neutral', text: 'not uploaded' }
  const bus = short.bus ? `${short.bus.n_ok}/${short.bus.n} buses` : ''
  const br = short.branches ? `, ${short.branches.n_ok}/${short.branches.n} branches` : ''
  return { tone: short.pass ? 'ok' : 'err', text: `${short.pass ? 'pass' : 'fail'} — ${bus}${br}` }
}

function ReadbackSection({ name, status }: { name: string; status: StageStatus }) {
  const hours = Object.keys(status.bundles).map(Number).sort((a, b) => a - b)
  return (
    <PageSection
      title="PowerFactory read-back"
      hint="Import a bundle's .raw, run the load flow, export the bus CSV (and the branch CSV) per the runbook, upload them here"
    >
      <ul className="flex flex-col gap-2" aria-label="Read-back per hour">
        {hours.map(hour => <ReadbackRow key={hour} name={name} hour={hour} short={status.readback?.[String(hour)]} />)}
      </ul>
    </PageSection>
  )
}

function ReadbackRow({ name, hour, short }: { name: string; hour: number; short: ReadbackShort | undefined }) {
  const qc = useQueryClient()
  const busRef = useRef<HTMLInputElement>(null)
  const branchRef = useRef<HTMLInputElement>(null)
  const [figure, setFigure] = useState<FigureName | null>(null)
  const v = verdict(short)

  const upload = useMutation({
    mutationFn: () => {
      const bus = busRef.current?.files?.[0]
      if (!bus) throw new Error('Choose the PowerFactory bus CSV first')
      return gridspineApi.uploadReadback(name, hour, bus, branchRef.current?.files?.[0] ?? null)
    },
    onSuccess: (summary) => {
      qc.invalidateQueries({ queryKey: STATUS_KEY(name) })
      qc.invalidateQueries({ queryKey: ['gridspine', 'figure', name, hour] })
      toast.success(summary.pass ? `Hour ${hour}: PowerFactory agrees within the gate` : `Hour ${hour}: outside the gate — see the table`)
    },
    onError: (e) => toast.error(`Read-back failed: ${errorText(e)}`),
  })

  const fig = useQuery({
    queryKey: FIGURE_KEY(name, hour, figure ?? 'vm'),
    queryFn: () => gridspineApi.figure(name, hour, figure ?? 'vm'),
    enabled: figure != null,
    retry: false,
  })

  return (
    <li className="flex flex-col gap-1.5 text-[12px]" data-testid={`readback-${hour}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono w-[60px]">h{hour}</span>
        <Tag tone={v.tone}>{v.text}</Tag>
        <input ref={busRef} type="file" accept=".csv,text/csv" aria-label={`PowerFactory bus CSV for hour ${hour}`} className="text-[11px]" />
        <input ref={branchRef} type="file" accept=".csv,text/csv" aria-label={`PowerFactory branch CSV for hour ${hour} (optional)`} className="text-[11px]" />
        <Btn onClick={() => upload.mutate()} disabled={upload.isPending} title="Compare with this bundle's load flow">
          <Upload size={12} /> Upload
        </Btn>
        {short && (
          <select
            className="px-2 py-1 text-[11px] border border-border rounded"
            aria-label={`Comparison for hour ${hour}`}
            value={figure ?? ''}
            onChange={e => setFigure((e.target.value || null) as FigureName | null)}
          >
            <option value="">Show comparison…</option>
            {(Object.keys(FIGURE_LABEL) as FigureName[]).map(f => <option key={f} value={f}>{FIGURE_LABEL[f]}</option>)}
          </select>
        )}
      </div>
      {figure && fig.data && (
        fig.data.available && fig.data.rows ? (
          <div className="overflow-x-auto" data-testid={`figure-${hour}`}>
            <table className="text-[11px]">
              <thead className="text-muted text-left">
                <tr><th className="pr-3">Element</th><th className="pr-3 text-right">pandapower</th><th className="pr-3 text-right">PowerFactory</th><th className="pr-3 text-right">error</th><th></th></tr>
              </thead>
              <tbody>
                {fig.data.rows.map(r => (
                  <tr key={r.element} className="border-t border-border">
                    <td className="pr-3 font-mono">{r.element}</td>
                    <td className="pr-3 text-right font-mono">{fmt(r.pandapower, 4)}</td>
                    <td className="pr-3 text-right font-mono">{fmt(r.powerfactory, 4)}</td>
                    <td className="pr-3 text-right font-mono">{fmt(r.err, 4)}</td>
                    <td>{r.ok ? <Tag tone="ok">ok</Tag> : <Tag tone="err">out</Tag>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[10.5px] text-muted mt-1">tolerance {Object.entries(fig.data.tolerance ?? {}).map(([k, t]) => `${k} < ${t}`).join(', ')}</p>
          </div>
        ) : (
          <p className="text-[11px] text-muted">{fig.data.reason}</p>
        )
      )}
    </li>
  )
}

function Stages({ status }: { status: StageStatus }) {
  return (
    <ol className="flex flex-col gap-1" aria-label="Study stages">
      {STAGES.map(stage => {
        const s = status.stages[stage]
        const perHour = stage === 'loadflow' || stage === 'screening' || stage === 'handoff'
        return (
          <li key={stage} className="flex items-center gap-2 text-[12px]" data-testid={`stage-${stage}`}>
            <Tag tone={STATE_TONE[s.state]}>{s.state}</Tag>
            <span className="text-text">{STAGE_LABEL[stage]}</span>
            {perHour && s.total > 0 && (
              <span className="text-[10.5px] font-mono text-muted">{s.done}/{s.total} hours</span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

function SnapshotTable({ rows, name, bundles }: { rows: RankedSnapshot[]; name: string; bundles: Record<string, string> }) {
  const sorted = useMemo(() => [...rows].sort((a, b) => a.hour - b.hour), [rows])
  const [downloading, setDownloading] = useState<number | null>(null)

  async function download(hour: number) {
    setDownloading(hour)
    try {
      const blob = await gridspineApi.bundle(name, hour)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name}_bundle_h${hour}.zip`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      toast.error(`Bundle download failed: ${errorText(e)}`)
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11.5px]">
        <thead className="text-muted text-left">
          <tr>
            <th className="py-1 pr-3">Hour</th>
            <th className="py-1 pr-3">Why</th>
            <th className="py-1 pr-3 text-right">Load MW</th>
            <th className="py-1 pr-3 text-right">Import MW</th>
            <th className="py-1 pr-3 text-right">Inertia MWs (excl. equiv.)</th>
            <th className="py-1 pr-3 text-right">IBR share</th>
            <th className="py-1 pr-3 text-right">N-1 severity (AC)</th>
            <th className="py-1 pr-3">LF</th>
            <th className="py-1">Bundle</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(r => (
            <tr key={r.hour} className="border-t border-border" data-testid={`snapshot-${r.hour}`}>
              <td className="py-1 pr-3 font-mono">{r.hour}</td>
              <td className="py-1 pr-3">
                <span className="inline-flex flex-wrap gap-1">
                  {r.reasons.map(reason => <Tag key={reason}>{REASON_LABEL[reason] ?? reason}</Tag>)}
                </span>
              </td>
              <td className="py-1 pr-3 text-right font-mono">{fmt(r.load_mw, 0)}</td>
              <td className="py-1 pr-3 text-right font-mono">{fmt(r.import_mw, 0)}</td>
              <td className="py-1 pr-3 text-right font-mono">{fmt(r.inertia_excl_equiv_mws, 0)}</td>
              <td className="py-1 pr-3 text-right font-mono">{fmt(r.ibr_share * 100, 1)} %</td>
              <td className="py-1 pr-3 text-right font-mono">{fmt(r.n1_severity_ac, 2)}</td>
              <td className="py-1 pr-3">{r.converged ? <Tag tone="ok">ok</Tag> : <Tag tone="err">diverged</Tag>}</td>
              <td className="py-1">
                {bundles[String(r.hour)] ? (
                  <Btn onClick={() => download(r.hour)} disabled={downloading === r.hour} title="Download the handoff bundle (.raw, .dyr, contingencies, ledger)">
                    <Download size={12} /> zip
                  </Btn>
                ) : <span className="text-muted">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
