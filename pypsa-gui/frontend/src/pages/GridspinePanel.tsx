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
import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Play, RefreshCw, FlaskConical, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  gridspineApi, isNotAStudy, STAGES,
  type CapacityKind, type CapacityRow, type ConnectionCheck, type ConnectionRow, type DispatchSource, type FigureName, type RankedSnapshot, type ReadbackShort, type StageState, type StageStatus,
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
export const CAPACITY_KEY = (name: string) => ['gridspine', 'capacity', name] as const
export const CONNECTION_KEY = (name: string) => ['gridspine', 'connection', name] as const

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
  // A capacity row's "Assess" fills the facility bus; the nonce re-applies it
  // when the same bus is picked twice after the field was edited by hand.
  const [assessAt, setAssessAt] = useState<{ bus: string; nonce: number } | null>(null)

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

      {done && (
        <>
          <CapacitySection
            name={name}
            locked={activeJob != null || status.data?.status === 'running'}
            onAssess={bus => setAssessAt(a => ({ bus, nonce: (a?.nonce ?? 0) + 1 }))}
          />
          <ConnectionSection
            name={name}
            locked={activeJob != null || status.data?.status === 'running'}
            assessAt={assessAt}
          />
        </>
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

// FOUR sources in the picker, three in `DispatchSource`. The client's own
// tables are set by UPLOADING files, not by naming a path in a JSON body —
// which is the security posture, not an implementation detail — so the extra
// member lives here rather than in the API's union.
type PickerMode = SourceKind | 'from_external'

// An Excel dispatch MAY carry its demand on a second sheet; a CSV cannot. So
// the demand file is required for a CSV and optional for a workbook, and the
// Apply button can say which before the request rather than after a 422.
const WORKBOOK = /\.(xlsx|xlsm|xls)$/i

// The suffixes the SERVER reads (`gridspine_service._EXTERNAL_SUFFIXES`, and
// `producers.external`'s two sets). `accept` on a file input is advisory —
// drag-and-drop and "All files" in the OS dialog both go round it — and the
// server RENAMES an unrecognised suffix to `dispatch.csv`, after which the
// producer reads the bytes as text and refuses them under a filename the
// engineer never used. Cheaper to say so here, before the upload.
const READABLE = /\.(csv|txt|xlsx|xlsm|xls)$/i
const READABLE_LIST = '.csv, .txt, .xlsx, .xlsm or .xls'

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'

function currentSourceKind(config: StudyConfig): PickerMode {
  if (config.from_external) return 'from_external'
  if (config.from_network) return 'from_project'
  if (config.from_dispatch) return 'from_dispatch'
  return 'generate'
}

function describeSource(config: StudyConfig): string {
  if (config.from_external) {
    // The filename, not the server path: what the engineer recognises is the
    // file they uploaded.
    const d = config.from_external_name ?? config.from_external
    const l = config.from_external_loads_name ?? config.from_external_loads
    return l ? `your own tables (${d} + ${l})` : `your own tables (${d})`
  }
  if (config.from_network) return `the solved network of ${config.from_project ?? config.from_network}`
  if (config.from_dispatch) return `the dispatch in ${config.from_dispatch}`
  return 'generated here (IEEE 39-bus, rolling unit commitment)'
}

/** What the demand input is actually for, given what is attached.
 *
 *  A workbook MAY carry its own `loads` sheet, so the file is optional — but an
 *  attached demand file is still SENT, and the server then takes its two-file
 *  path and reads sheet 0 of the workbook as the dispatch. Saying "optional"
 *  while sending it is how an engineer ends up reading a refusal about columns a
 *  sheet they never meant to use does not have.
 */
function demandLabel(dispatch: File | null, loads: File | null): string {
  if (dispatch && WORKBOOK.test(dispatch.name)) {
    return loads
      ? 'Demand table — this file will be used, not the workbook\u2019s "loads" sheet (clear it to use the sheet)'
      : 'Demand table — optional: this workbook may hold a "loads" sheet'
  }
  return 'Demand table — bus, hour, P, Q (required: a snapshot is generation AND demand)'
}

function DispatchSourcePicker({ name, config, locked }: { name: string; config: StudyConfig; locked: boolean }) {
  const qc = useQueryClient()
  const [kind, setKind] = useState<PickerMode | null>(null)
  const [dir, setDir] = useState(config.from_dispatch ?? '')
  const [project, setProject] = useState(config.from_project ?? '')
  const [dispatchFile, setDispatchFile] = useState<File | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [loadsFile, setLoadsFile] = useState<File | null>(null)
  // Bumping this remounts both file inputs, which is the only way to clear what
  // an uncontrolled file input DISPLAYS. Without it the inputs and the state
  // disagreed: after a mode round trip both read "No file chosen" while the
  // files were still attached, and Apply re-uploaded the invisible pair.
  const [filesKey, setFilesKey] = useState(0)
  const mode: PickerMode = kind ?? currentSourceKind(config)

  function forgetFiles() {
    setDispatchFile(null)
    setLoadsFile(null)
    setRefusal(null)
    setFilesKey(k => k + 1)
  }

  /** A picked file, or a refusal instead of it. */
  function pick(file: File | null, keep: (f: File | null) => void) {
    setRefusal(null)
    if (file && !READABLE.test(file.name)) {
      keep(null)
      setRefusal(`gridspine reads ${READABLE_LIST}; ${file.name} is none of those. `
                 + 'Export the table as CSV or as an Excel workbook and try again.')
      return
    }
    keep(file)
  }

  const projects = useQuery({ queryKey: ['projects'], queryFn: () => projectsApi.list(), enabled: mode === 'from_project' })
  const candidates = (projects.data ?? []).filter(p => p.project_kind !== 'planning_dynamics' && p.name !== name)

  const source: DispatchSource | null =
    mode === 'generate' ? { kind: 'generate' }
    : mode === 'from_dispatch' ? (dir.trim() ? { kind: 'from_dispatch', dir: dir.trim() } : null)
    : mode === 'from_project' ? (project ? { kind: 'from_project', project } : null)
    : null

  // A workbook may carry both tables; a CSV must be paired with a demand file.
  const externalReady = !!dispatchFile && (WORKBOOK.test(dispatchFile.name) || !!loadsFile)

  const apply = useMutation({
    mutationFn: (s: DispatchSource) => gridspineApi.setDispatchSource(name, s),
    onSuccess: (cfg) => {
      qc.setQueryData(CONFIG_KEY(name), cfg)
      setKind(null)
      // A refusal describes a file, and the source is no longer that file.
      forgetFiles()
      toast.success(`Dispatch source: ${describeSource(cfg)}`)
    },
    onError: (e) => toast.error(errorText(e)),
  })

  const upload = useMutation({
    mutationFn: () => gridspineApi.uploadExternalDispatch(name, dispatchFile!, loadsFile),
    onSuccess: async (summary) => {
      forgetFiles()
      setKind(null)
      await qc.invalidateQueries({ queryKey: CONFIG_KEY(name) })
      toast.success(`Dispatch source: your own tables — ${summary.units} units over ${summary.hours} h`)
    },
    // Inline as well as a toast: the producer's refusal names the units or the
    // columns that disagree, and the engineer reads it against their own file.
    // A toast that vanishes is the wrong home for a list of ids.
    // A 413 is refused before the server reads a byte, so "could not be read"
    // would send the engineer looking for a fault in their files.
    onError: (e) => {
      setRefusal(errorText(e))
      const status = (e as { response?: { status?: number } } | null)?.response?.status
      toast.error(status === 413 ? 'Those files are too large to upload' : 'That dispatch could not be read')
    },
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
            onChange={e => { setKind(e.target.value as PickerMode); forgetFiles() }}
          >
            <option value="generate">Generate here (unit commitment)</option>
            <option value="from_project">A solved project's network</option>
            <option value="from_dispatch">A finished study's directory</option>
            <option value="from_external">Your own dispatch (CSV / Excel)</option>
          </select>
        </Field>
        {mode === 'from_dispatch' && (
          <Field label="Study directory — the gridspine/run of one of your studies">
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
        {mode === 'from_external' && (
          <>
            <Field label="Dispatch table — unit id, hour, P, Q, status (CSV or Excel)">
              <input
                key={`dispatch-${filesKey}`}
                type="file"
                className={`${INPUT} w-[300px]`}
                accept=".csv,.txt,.xlsx,.xlsm,.xls"
                disabled={locked}
                aria-label="Dispatch table"
                onChange={e => pick(e.target.files?.[0] ?? null, setDispatchFile)}
              />
            </Field>
            <Field label={demandLabel(dispatchFile, loadsFile)}>
              <div className="flex items-center gap-1">
                <input
                  key={`loads-${filesKey}`}
                  type="file"
                  className={`${INPUT} w-[300px]`}
                  accept=".csv,.txt,.xlsx,.xlsm,.xls"
                  disabled={locked}
                  aria-label="Demand table"
                  onChange={e => pick(e.target.files?.[0] ?? null, setLoadsFile)}
                />
                {loadsFile && (
                  <button
                    type="button"
                    className="text-[11px] text-muted hover:text-danger px-1"
                    aria-label="Clear the demand table"
                    disabled={locked}
                    onClick={() => { setLoadsFile(null); setRefusal(null); setFilesKey(k => k + 1) }}
                  >
                    clear
                  </button>
                )}
              </div>
            </Field>
          </>
        )}
        {mode === 'from_external'
          ? <Btn onClick={() => upload.mutate()} disabled={locked || !externalReady || upload.isPending}>Apply</Btn>
          : <Btn onClick={() => source && apply.mutate(source)} disabled={locked || !source || apply.isPending}>Apply</Btn>}
      </div>
      {refusal && (
        <p className="mt-2 text-[11px] text-danger whitespace-pre-wrap font-mono" data-testid="external-refusal">
          {refusal}
        </p>
      )}
    </PageSection>
  )
}

// The editable part of the study config, after creation. Only the fields the
// user actually changed are PUT — the backend merges a patch, and sending the
// whole form back would silently overwrite a value the copilot changed in
// between. Locked while a job is queued or running: the backend answers 409
// then, and a disabled form says why before the request rather than after.
// `generationOnly`: the field is an input to the rolling unit commitment, so it
// means something only when the year is GENERATED here. A study fed a solved
// network, a finished study's dispatch or the client's own tables brings its
// own hours, and showing "Hours 8760" beside a 3-hour file (as the browser run
// found) invites the engineer to believe the study covers a year.
const NUMERIC_FIELDS: readonly { key: 'hours' | 'k' | 'window' | 'overlap' | 'n2_prune_threshold_pct'; label: string; step?: string; generationOnly?: true }[] = [
  { key: 'hours', label: 'Hours', generationOnly: true },
  { key: 'k', label: 'k (hours per criterion)' },
  { key: 'window', label: 'UC window (h)', generationOnly: true },
  { key: 'overlap', label: 'UC overlap (h)', generationOnly: true },
  { key: 'n2_prune_threshold_pct', label: 'N-2 prune threshold (%)', step: '0.1' },
]

function ConfigEditor({ name, config, locked }: { name: string; config: StudyConfig; locked: boolean }) {
  const qc = useQueryClient()
  const ownHours = !!(config.from_external || config.from_network || config.from_dispatch)
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
      {ownHours && (
        <p className="text-[11px] text-muted mb-2" data-testid="config-source-hours">
          Hours, UC window and overlap apply only to a year generated here — this study&rsquo;s
          dispatch source brings its own hours.
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3" data-testid="config-editor">
        {NUMERIC_FIELDS.map(f => (
          <Field key={f.key} label={f.label}>
            <input
              type="number"
              step={f.step}
              className="px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20 font-mono w-[120px] disabled:opacity-50"
              value={value(f.key)}
              disabled={locked || (ownHours && !!f.generationOnly)}
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


// ── Connection capacity (increment 9) ─────────────────────────────────────

/** What stopped the capacity, in words an engineer acts on. A limit set by a
 *  constraint that was ALREADY violated says so: that figure is the worsening
 *  tolerance, not headroom, and read as "13.8 MW available" it would mislead. */
function bindingText(r: CapacityRow): string {
  const base = bindingBase(r)
  if (!r.binding_preexisting) return base
  const what = r.binding_kind.startsWith('thermal') ? 'overloaded' : 'outside limits'
  return `${base} — already ${what} before connection`
}

function bindingBase(r: CapacityRow): string {
  const after = r.binding_contingency ? ` after losing ${r.binding_contingency}` : ''
  switch (r.binding_kind) {
    case 'thermal_intact': case 'thermal_n1': return `overload of ${r.binding_element}${after}`
    case 'v_low_intact': case 'v_low_n1': return `low voltage at ${r.binding_element}${after}`
    case 'v_high_intact': case 'v_high_n1': return `high voltage at ${r.binding_element}${after}`
    case 'n1_divergence': return `no load-flow solution${after}`
    case 'ac_divergence': return 'no load-flow solution'
    case 'none_up_to_cap': return 'nothing binds up to the cap'
    default: return r.binding_kind
  }
}

function capacityText(r: CapacityRow): string {
  const capped = r.binding_kind === 'none_up_to_cap'
  const v = r.method === 'ac' ? r.capacity_mw : r.dc_estimate_mw
  if (v == null) return '—'
  if (capped) return `≥ ${fmt(v)} MW`
  return r.method === 'ac' ? `${fmt(v)} MW` : `≈ ${fmt(v)} MW`
}

function CapacitySection({ name, locked, onAssess }: { name: string; locked: boolean; onAssess: (bus: string) => void }) {
  const qc = useQueryClient()
  const [kind, setKind] = useState<CapacityKind>('load')
  const [hour, setHour] = useState<number | null>(null)
  const table = useQuery({
    queryKey: CAPACITY_KEY(name),
    queryFn: () => gridspineApi.capacity(name),
    retry: false,
  })
  const compute = useMutation({
    mutationFn: (bus: string) => gridspineApi.computeCapacity(name, bus, kind),
    onSuccess: async (_r, bus) => {
      await qc.invalidateQueries({ queryKey: CAPACITY_KEY(name) })
      toast.success(`AC capacity at ${bus} (${kind}) computed for every selected hour`)
    },
    onError: (e) => toast.error(`Could not compute capacity: ${errorText(e)}`),
  })

  const hours = table.data?.hours ?? []
  const shownHour = hour ?? hours[0]
  const rows = (table.data?.rows ?? [])
    .filter(r => r.hour === shownHour && r.kind === kind)
    .sort((a, b) => a.bus.localeCompare(b.bus))

  return (
    <div data-testid="capacity-section">
      <PageSection
        title="Connection capacity"
        hint="MW that can connect at each bus with no new or worsened violation, intact and under N-1"
      >
        {table.isError ? (
          <p className="text-[12px] text-muted">{errorText(table.error)}</p>
        ) : !table.data ? (
          <p className="text-[12px] text-muted">Loading…</p>
        ) : (
          <>
            <div className="flex items-center gap-3 mb-2 text-[12px]">
              <label className="flex items-center gap-1">
                Hour
                <select
                  aria-label="Capacity hour"
                  className="bg-transparent border border-border rounded px-1"
                  value={shownHour ?? ''}
                  onChange={e => setHour(Number(e.target.value))}
                >
                  {hours.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-1">
                Connecting
                <select
                  aria-label="Connection kind"
                  className="bg-transparent border border-border rounded px-1"
                  value={kind}
                  onChange={e => setKind(e.target.value as CapacityKind)}
                >
                  <option value="load">load (pf 0.98)</option>
                  <option value="generation">generation</option>
                </select>
              </label>
              <span className="text-[11px] text-muted">
                ≈ DC estimate · AC is exact to 1 MW · balanced pro-rata over committed units
              </span>
            </div>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-muted">
                  <th className="font-normal pr-3 whitespace-nowrap">Bus</th>
                  <th className="font-normal pr-3 whitespace-nowrap">Capacity</th>
                  <th className="font-normal pr-3 whitespace-nowrap">Method</th>
                  <th className="font-normal">Binds on</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const busy = compute.isPending && compute.variables === r.bus
                  return (
                    <tr key={r.bus} data-testid={`capacity-row-${r.bus}`} className="border-t border-border">
                      <td className="py-1 pr-3 whitespace-nowrap align-top">{r.bus}</td>
                      <td className="pr-3 whitespace-nowrap align-top tabular-nums">{capacityText(r)}</td>
                      <td className="pr-3 whitespace-nowrap align-top">
                        <Tag tone={r.method === 'ac' ? 'ok' : 'neutral'}>{r.method === 'ac' ? 'AC' : 'DC est.'}</Tag>
                      </td>
                      <td className="text-muted align-top">{bindingText(r)}</td>
                      <td className="text-right whitespace-nowrap align-top pl-2">
                        <Btn
                          onClick={() => compute.mutate(r.bus)}
                          disabled={locked || compute.isPending}
                          aria-label={`Compute AC capacity at ${r.bus}`}
                          title={locked ? 'A study for this project is queued or running' : 'Exact AC capacity at every selected hour (a few seconds)'}
                        >
                          {busy ? 'Computing…' : 'Compute AC'}
                        </Btn>
                        <Btn
                          onClick={() => onAssess(r.bus)}
                          aria-label={`Assess a facility at ${r.bus}`}
                          title="Assess a facility at this bus against the grid code"
                        >
                          Assess
                        </Btn>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </>
        )}
      </PageSection>
    </div>
  )
}


// ── Connection-point assessment (increment 10) ────────────────────────────

const CHECK_LABEL: Record<ConnectionCheck, string> = {
  connection: 'Connection (intact + N-1)',
  energisation: 'Energisation step',
  load_trip: 'Load trip (on-site unit stays)',
  facility_trip: 'Whole-facility trip',
  q_lead: 'Reactive range — injecting',
  q_lag: 'Reactive range — absorbing',
  scr_onsite: 'SCR, on-site converter',
  scr_load: 'SCR, load',
}
const CHECK_ORDER = Object.keys(CHECK_LABEL) as ConnectionCheck[]

const STATUS_TONE: Record<ConnectionRow['status'], 'ok' | 'err' | 'neutral'> = {
  pass: 'ok', fail: 'err', reported: 'neutral',
}

function checkValueText(r: ConnectionRow): string {
  if (r.value == null || !isFinite(r.value)) return '—'
  switch (r.unit) {
    case '%': return `${r.value >= 0 ? '+' : ''}${r.value.toFixed(2)} %`
    case 'pu': return `${r.value.toFixed(3)} pu`
    case 'violations': return r.value === 1 ? '1 violation' : `${r.value} violations`
    default: return fmt(r.value)
  }
}

/** The threshold the value was held to: a voltage step is a magnitude cap,
 *  the injecting end of the reactive range is held under v_max and the
 *  absorbing end over v_min. */
function checkLimitText(r: ConnectionRow): string {
  if (r.limit == null || !isFinite(r.limit)) return ''
  if (r.unit === '%') return `≤ ${r.limit} %`
  if (r.check === 'q_lag') return `≥ ${r.limit} pu`
  return `≤ ${r.limit} pu`
}

function numberOr(v: string): number {
  const n = Number(v)
  return v.trim() === '' || !isFinite(n) ? NaN : n
}

function ConnectionSection({ name, locked, assessAt }: {
  name: string
  locked: boolean
  assessAt: { bus: string; nonce: number } | null
}) {
  const qc = useQueryClient()
  const ref = useRef<HTMLDivElement>(null)
  const [bus, setBus] = useState('')
  const [loadMw, setLoadMw] = useState('')
  const [pf, setPf] = useState('0.98')
  const [onsiteMw, setOnsiteMw] = useState('0')
  const [converter, setConverter] = useState(true)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [shown, setShown] = useState<string | null>(null)
  const [hour, setHour] = useState<number | null>(null)

  useEffect(() => {
    if (!assessAt) return
    setBus(assessAt.bus)
    ref.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [assessAt])

  const stored = useQuery({
    queryKey: CONNECTION_KEY(name),
    queryFn: () => gridspineApi.connection(name),
    retry: false,
  })
  const assess = useMutation({
    mutationFn: () => gridspineApi.assessConnection(name, {
      bus: bus.trim(), load_mw: numberOr(loadMw), load_pf: numberOr(pf),
      onsite_mw: numberOr(onsiteMw), onsite_converter: converter,
    }),
    onMutate: () => setRefusal(null),
    onSuccess: async (r) => {
      setShown(r.assessment_id)
      setHour(null)
      await qc.invalidateQueries({ queryKey: CONNECTION_KEY(name) })
    },
    onError: (e) => setRefusal(errorText(e)),
  })

  // Stored rows are the record; the mutation's own answer bridges the moment
  // before the refetch lands.
  const all = useMemo(() => {
    const rows = stored.data?.rows ?? []
    const fresh = assess.data
    if (fresh && !rows.some(r => r.assessment_id === fresh.assessment_id)) return [...rows, ...fresh.rows]
    return rows
  }, [stored.data, assess.data])
  const ids = [...new Set(all.map(r => r.assessment_id))]
  const current = shown != null && ids.includes(shown) ? shown : ids[ids.length - 1]
  const ofCurrent = all.filter(r => r.assessment_id === current)
  const hours = [...new Set(ofCurrent.map(r => r.hour))].sort((a, b) => a - b)
  const shownHour = hour != null && hours.includes(hour) ? hour : hours[0]
  const rows = ofCurrent
    .filter(r => r.hour === shownHour)
    .sort((a, b) => CHECK_ORDER.indexOf(a.check) - CHECK_ORDER.indexOf(b.check))
  const facilityText = (r: ConnectionRow) =>
    `${r.bus}: ${fmt(r.load_mw)} MW load` + (r.onsite_mw > 0 ? ` + ${fmt(r.onsite_mw)} MW on-site` : '')

  const ready = bus.trim() !== '' && isFinite(numberOr(loadMw)) && isFinite(numberOr(pf)) && isFinite(numberOr(onsiteMw))

  return (
    <div data-testid="connection-section" ref={ref}>
      <PageSection
        title="Connection-point assessment"
        hint="A load plus on-site unit, against EU RfG/DCC (Continental Europe)"
      >
        <p className="text-[11.5px] text-muted mb-2">
          A steady-state screen for the connection engineer, not a compliance certificate: fault
          ride-through and dynamic support need the RMS/EMT study the handoff bundle is for.
        </p>
        <div className="flex flex-wrap items-end gap-3 mb-2">
          <Field label="Facility bus">
            <input className={`${INPUT} w-[110px] font-mono`} value={bus} onChange={e => setBus(e.target.value)} />
          </Field>
          <Field label="Load (MW)">
            <input className={`${INPUT} w-[90px]`} inputMode="decimal" value={loadMw} onChange={e => setLoadMw(e.target.value)} />
          </Field>
          <Field label="Power factor">
            <input className={`${INPUT} w-[70px]`} inputMode="decimal" value={pf} onChange={e => setPf(e.target.value)} />
          </Field>
          <Field label="On-site unit (MW)">
            <input className={`${INPUT} w-[90px]`} inputMode="decimal" value={onsiteMw} onChange={e => setOnsiteMw(e.target.value)} />
          </Field>
          <Field label="Converter-interfaced" row>
            <input type="checkbox" checked={converter} onChange={e => setConverter(e.target.checked)} />
          </Field>
          <Btn
            variant="primary"
            onClick={() => assess.mutate()}
            disabled={locked || !ready || assess.isPending}
            title={locked ? 'A study for this project is queued or running' : 'Every check at every selected hour (a few seconds per hour)'}
          >
            {assess.isPending ? 'Assessing…' : 'Assess'}
          </Btn>
        </div>
        {refusal && <p className="text-[12px] text-danger mb-2">{refusal}</p>}

        {stored.isError && !assess.data ? (
          <p className="text-[12px] text-muted">{errorText(stored.error)}</p>
        ) : rows.length === 0 ? (
          <p className="text-[12px] text-muted">No facility assessed yet.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3 mb-2 text-[12px]">
              {ids.length > 1 ? (
                <label className="flex items-center gap-1">
                  Facility
                  <select
                    aria-label="Assessed facility"
                    className="bg-transparent border border-border rounded px-1"
                    value={current}
                    onChange={e => { setShown(e.target.value); setHour(null) }}
                  >
                    {ids.map(id => {
                      const r = all.find(x => x.assessment_id === id)!
                      return <option key={id} value={id}>{facilityText(r)}</option>
                    })}
                  </select>
                </label>
              ) : (
                <span>{facilityText(ofCurrent[0])}</span>
              )}
              <label className="flex items-center gap-1">
                Hour
                <select
                  aria-label="Assessment hour"
                  className="bg-transparent border border-border rounded px-1"
                  value={shownHour ?? ''}
                  onChange={e => setHour(Number(e.target.value))}
                >
                  {hours.map(h => <option key={h} value={h}>{h}</option>)}
                </select>
              </label>
              <span className="text-[11px] text-muted">
                no re-dispatch on a step or trip · SCR is reported, not gated
              </span>
            </div>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-muted">
                  <th className="font-normal pr-3 whitespace-nowrap">Check</th>
                  <th className="font-normal pr-3 whitespace-nowrap">Result</th>
                  <th className="font-normal pr-3 whitespace-nowrap">Value</th>
                  <th className="font-normal pr-3 whitespace-nowrap">Limit</th>
                  <th className="font-normal">Detail · rule</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.check} data-testid={`connection-check-${r.check}`} className="border-t border-border">
                    <td className="py-1 pr-3 whitespace-nowrap align-top">{CHECK_LABEL[r.check] ?? r.check}</td>
                    <td className="pr-3 whitespace-nowrap align-top"><Tag tone={STATUS_TONE[r.status]}>{r.status}</Tag></td>
                    <td className="pr-3 whitespace-nowrap align-top tabular-nums">{checkValueText(r)}</td>
                    <td className="pr-3 whitespace-nowrap align-top tabular-nums text-muted">{checkLimitText(r)}</td>
                    <td className="align-top">
                      <div>{r.detail ?? ''}</div>
                      <div className="text-[11px] text-muted flex items-start gap-1.5 mt-0.5">
                        <Tag tone={r.source === 'code' ? 'accent' : 'warn'}>{r.source}</Tag>
                        <span>{r.clause}</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </PageSection>
    </div>
  )
}
