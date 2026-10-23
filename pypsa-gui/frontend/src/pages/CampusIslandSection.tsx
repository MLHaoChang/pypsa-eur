// The island study, in the campus electrical panel (campus island plan, I9a).
//
// It edits the project's island sidecar (`island_config.json`) and shows the
// island report. Thin like the rest of the panel:
// - the form edits a few requirements in place (scenarios, the ride-through
//   numbers with their source tags, N+k, linearised UC) and each unit's
//   control mode; the JSON editor holds the whole sidecar, so nothing the
//   form leaves out is lost;
// - Save sends the sidecar, and the backend validates it (422 names the field);
// - Run solves the islanding variants on the saved project;
// - the report's tables are READ from `GET /island/report`, each with the
//   model behind it, the screening caveat, and its assumed rows tagged.
//   The panel derives nothing; it heads each of the report's groups (the
//   inputs, the hub-LP study, the island hours, and the island-hours
//   results of I4–I6) as the report orders them.
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Save } from 'lucide-react'
import {
  campusApi, errorText, ISLAND_SCENARIOS,
  type IslandConfig, type IslandGroup, type IslandReport, type IslandScenario, type IslandSource, type IslandTable,
} from '../api/campusElectrical'
import { Btn, PageSection, Tag } from '../components/PageKit'
import CampusIslandCutsSection, { scenarioWords } from './CampusIslandCutsSection'
import { showResultsTab } from '../layout/flowNavigation'

export const ISLAND_KEY = (name: string) => ['campusElectrical', 'island', name] as const
export const ISLAND_REPORT_KEY = (name: string) => ['campusElectrical', 'islandReport', name] as const

const INPUT = 'px-2 py-1 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
const SOURCES: IslandSource[] = ['measured', 'datasheet', 'assumed']
const CONTROLS = ['gfl_pq', 'gfl_qu', 'gfl_pf', 'gfl_fw', 'gfm_droop', 'gfm_vsm']
/** The requirements the form edits, each a tagged number. */
const NUMBERS: [string, string][] = [
  ['sustained_h', 'Sustained ride-through (h)'],
  ['bridge_s', 'UPS bridge (s)'],
  ['fuel_autonomy_h', 'Fuel autonomy (h)'],
  ['genset_redundancy_n', 'Genset redundancy (N+k)'],
]

type Tagged = { value: number; source: IslandSource }

const GROUP_TITLES: Record<IslandGroup, string> = {
  inputs: 'Inputs',
  study: 'Island study',
  hours: 'Island critical hours',
  static: 'Island steady state and short circuit',
  frequency: 'Island frequency',
}

/** Column headers in the user's words. The report's columns are the engine's contract
 *  (kept as they are in the data); a column not listed reads as its name with spaces. */
const COLUMN_WORDS: Record<string, string> = {
  field: 'Setting', value: 'Value', source: 'Source', unit: 'Unit', kind: 'Kind', scenario: 'Scenario', row: 'Limit', 'cost of island capability': 'Cost of island capability',
  'hours binding': 'Hours reached', 'sum of duals': 'Cost of the limit',
  status: 'Status', objective: 'Objective', period: 'Period', hour: 'Hour', event: 'Event', case: 'Case',
  viable: 'Viable', reason: 'Reason', assumed: 'Assumed inputs', ok: 'Passes', verdict: 'Verdict', detail: 'Detail',
  df_hz: 'Frequency shift (Hz)', delta_p_ac_mw: 'Power step (MW)', sync_df_hz: 'Sync frequency gap (Hz)',
  sync_dv_pu: 'Sync voltage gap (pu)', gate_ok: 'Gate passes', gate_problems: 'Gate problems',
  unit_id: 'Unit', control: 'Control mode', role: 'Role', p0_mw: 'Power before (MW)', p_mw: 'Power (MW)',
  q_mvar: 'Reactive power (MVAr)', k_mw_per_hz: 'Droop gain (MW/Hz)', saturated: 'At current limit',
  loading_pct: 'Loading (%)', bus: 'Bus', vm_pu: 'Voltage (pu)', in_band: 'In band', branch: 'Branch',
  ikss_max_ka: 'Max fault current (kA)', ikss_min_ka: 'Min fault current (kA)',
  ikss_min_n1_ka: 'Min fault current, one out (kA)', largest_out: 'Largest unit out', method_min: 'Min-current method',
  relay: 'Relay', fault_bus: 'Fault bus', timing: 'Timing', relay_ka: 'Relay current (kA)',
  initial_ka: 'Initial current (kA)', fault_total_ka: 'Total fault current (kA)', bypass_ka: 'Bypass current (kA)',
  pickup_ka: 'Pickup (kA)', s: 'Margin', grid_ikss_max_ka: 'Grid max fault current (kA)',
  grid_to_island: 'Grid-to-island ratio', relay_n1_ka: 'Relay current, one out (kA)', i_max_pu: 'Current limit (pu)',
  ik1_max_ka: '1-phase max (kA)', ik2_max_ka: '2-phase max (kA)', ik3_max_ka: '3-phase max (kA)',
  ik1_min_ka: '1-phase min (kA)', ik2_min_ka: '2-phase min (kA)', ik3_min_ka: '3-phase min (kA)',
  earth_path: 'Earth path', earth_assumed: 'Earth assumed', ic_e_ka: 'Earth current (kA)', note: 'Note',
  method: 'Method', earthed_grid: 'Earthed, grid', earthed_island: 'Earthed, island',
  lost_on_islanding: 'Lost on islanding', island_assumed: 'Island assumed', finding: 'Finding',
  i_pickup_e_ka: 'Earth pickup (kA)', relay_e_ka: 'Relay earth current (kA)', fault_e_ka: 'Fault earth current (kA)',
  grid_e_ka: 'Grid earth current (kA)', sensitivity: 'Sensitivity', direction: 'Direction', dp_source: 'Step source',
  ups_drops: 'UPS drops', dp_mw: 'Power step (MW)', rocof0_hz_per_s: 'Initial rate of change (Hz/s)',
  rocof_window_hz_per_s: 'Rate of change over window (Hz/s)', nadir_hz: 'Lowest frequency (Hz)',
  t_nadir_s: 'Time to lowest (s)', df_qss_hz: 'Settled shift (Hz)', t_return_s: 'Time to return (s)',
  collapse_s: 'Collapse time (s)', trip_s: 'Trip time (s)', binding: 'Limiting check', worst: 'Worst case', emt: 'EMT check',
  block: 'Block', t_block_s: 'Block time (s)', i4_viable: 'Island load flow viable', i4_reason: 'Reason',
  dp_ac_mw: 'AC power step (MW)', import_lossless_mw: 'Import, lossless (MW)', losses_mw: 'Losses (MW)',
  e_mwh: 'Energy (MWh)', it_mw: 'IT load (MW)', autonomy_s: 'Autonomy (s)', needed_s: 'Needed (s)',
  pickup_complete: 'Pickup complete', ready_s: 'Ready after (s)', dropped: 'Dropped', energy_mwh: 'Energy used (MWh)',
  join_s: 'Joins after (s)', until_s: 'Holds until (s)', import_mw: 'Import (MW)', feasible: 'Feasible',
  ramp_s: 'Ramp time (s)', shortfall_mw: 'Shortfall (MW)', ups: 'UPS', drops: 'Drops', first_drop_s: 'First drop (s)',
  t_online_s: 'Back online (s)', autonomy_ok: 'Autonomy enough',
}
/** A title inside a sentence: its first letter lowercased unless it starts an acronym ("UPS", "E1"). */
const inSentence = (t: string) => (/^[A-Z][a-z]/.test(t) ? t.charAt(0).toLowerCase() + t.slice(1) : t)
const columnWords = (c: string) => COLUMN_WORDS[c] ?? (c.charAt(0).toUpperCase() + c.slice(1)).replace(/_/g, ' ')

/** The island requirement fields in words ("bridge_s" → "UPS bridge (s)"); a field not listed
 *  reads as its name with spaces. A dotted field is a frequency limit of a named mode. */
const FIELD_WORDS: Record<string, string> = {
  sustained_h: 'Sustained ride-through (h)', bridge_s: 'UPS bridge (s)', fuel_autonomy_h: 'Fuel autonomy (h)',
  genset_redundancy_n: 'Genset redundancy (N+k)', gfm_margin: 'Grid-forming margin',
  i_max_sensitivity_pu: 'Current limits tried (pu)', relay_sensitivity: 'Relay sensitivity',
  rocof_window_ms: 'Rate-of-change window (ms)', scenarios: 'Scenarios', linearised_uc: 'Linearised unit commitment',
  ride_through_storage: 'Ride-through storage', pickup_blocks: 'Pickup blocks',
  f_max_hz: 'Highest frequency (Hz)', f_min_hz: 'Lowest frequency (Hz)', qss_band_hz: 'Settled band (Hz)',
  rocof_hz_per_s: 'Rate of change (Hz/s)', rocof_hz_per_s_max: 'Rate of change (Hz/s)',
}
const fieldWords = (f: string) => {
  const [head, ...rest] = f.split('.')
  if (!rest.length) return FIELD_WORDS[f] ?? f.replace(/_/g, ' ')
  const field = rest.join('.')
  return `${head.replace(/_/g, ' ')}: ${(FIELD_WORDS[field] ?? field.replace(/_/g, ' ')).replace(/^./, c => c.toLowerCase())}`.replace(/^./, c => c.toUpperCase())
}

const sentence = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)
const intFmt = (v: number) => Math.round(v).toLocaleString('en-US')

/** A cell longer than this stays on one line, its full text on hover. */
const LONG = 48

function cell(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toPrecision(6).replace(/\.?0+$/, '')
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  return String(v)
}

function ReportTable({ table }: { table: IslandTable }) {
  const assumed = new Set(table.assumed_rows)
  return (
    <div data-testid={`island-table-${table.id}`} className="mb-4">
      <h4 className="text-[12.5px] font-semibold">{table.title}</h4>
      <p className="text-[11px] text-muted" data-testid="island-model">Model: {table.model}</p>
      <p className="text-[11px] text-muted italic mb-1" data-testid="island-caveat">{table.caveat}</p>
      {/* A table scrolls in its own box, and a long cell (a list of assumed
          inputs, a ledger reason) stays on one line with the full text on
          hover, so an island-hours table of many rows keeps its height. */}
      <div className="max-h-[420px] overflow-auto border border-border rounded" data-testid="island-table-scroll">
        <table className="text-[12px] whitespace-nowrap">
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr>{table.columns.map(c => <th key={c} className="text-left font-medium px-2">{columnWords(c)}</th>)}<th /></tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i} data-assumed={assumed.has(i) ? 'yes' : 'no'}>
                {row.map((v, j) => {
                  const col = table.columns[j]
                  const text = table.id === 'requirements' && j === 0 && typeof v === 'string' ? fieldWords(v)
                    : col === 'scenario' && typeof v === 'string' ? scenarioWords(v)
                    : col === 'status' && v === 'ok' ? 'OK'
                    : col === 'row' && typeof v === 'string' ? sentence(v.replace(/_/g, ' '))
                    : (table.id === 'costs' || (table.id === 'binding' && col === 'sum of duals')) && typeof v === 'number' ? intFmt(v)
                    : cell(v)
                  return text.length > LONG
                    ? <td key={j} className="px-2 max-w-[320px] truncate" title={text}>{text}</td>
                    : <td key={j} className="px-2">{text}</td>
                })}
                <td className="px-2">{assumed.has(i) && <Tag tone="warn">assumed</Tag>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function CampusIslandSection({ name }: { name: string }) {
  const qc = useQueryClient()
  const island = useQuery({ queryKey: ISLAND_KEY(name), queryFn: () => campusApi.island(name), retry: false })
  const [config, setConfig] = useState<IslandConfig | null>(null)
  const [text, setText] = useState('')
  const [textError, setTextError] = useState<string | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const saved = island.data?.config ?? null

  useEffect(() => {
    setConfig(saved)
    setText(saved ? JSON.stringify(saved, null, 2) : '')
  }, [JSON.stringify(saved)])   // eslint-disable-line react-hooks/exhaustive-deps

  const edit = (next: IslandConfig) => { setConfig(next); setText(JSON.stringify(next, null, 2)); setTextError(null) }
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ISLAND_KEY(name) })
    qc.invalidateQueries({ queryKey: ISLAND_REPORT_KEY(name) })
  }
  const save = useMutation({
    mutationFn: () => campusApi.saveIsland(name, config as IslandConfig),
    onMutate: () => setRefusal(null),
    onSuccess: d => { qc.setQueryData(ISLAND_KEY(name), d); refresh() },
    onError: e => setRefusal(errorText(e)),
  })
  const run = useMutation({
    mutationFn: () => campusApi.runIslandStudy(name),
    onMutate: () => setRefusal(null),
    onSuccess: refresh,
    onError: e => setRefusal(errorText(e)),
  })

  const req = config?.requirements
  const dirty = config !== null && JSON.stringify(config) !== JSON.stringify(saved)
  const setReq = (field: string, value: unknown) =>
    config && edit({ ...config, requirements: { ...config.requirements, [field]: value } })
  const toggleScenario = (sc: IslandScenario) => {
    if (!req) return
    const has = req.scenarios.includes(sc)
    setReq('scenarios', has ? req.scenarios.filter(s => s !== sc) : [...req.scenarios, sc])
  }
  const setUnitControl = (unit: string, control: string) =>
    config && edit({ ...config, units: { ...config.units, [unit]: { ...config.units[unit], control } } })

  return (
    <div data-testid="island-section">
      <PageSection title="Island" hint="Ride-through, the instant of islanding, and the cost of island capability">
        {island.isError && <p className="text-[12px] text-danger mb-2">{errorText(island.error)}</p>}
        {refusal && <p className="text-[12px] text-danger mb-2" data-testid="island-refusal">{refusal}</p>}
        {island.isSuccess && !saved && (
          <p className="text-[12px] text-muted mb-2">
            This project has no island requirements yet. Paste an island config below and save it.
          </p>
        )}

        {req && (
          <div className="grid gap-2 mb-3" data-testid="island-form">
            <fieldset className="flex gap-3 items-center text-[12px]">
              <legend className="font-medium mr-2">Scenarios</legend>
              {ISLAND_SCENARIOS.map(sc => (
                <label key={sc} className="flex gap-1 items-center">
                  <input type="checkbox" checked={req.scenarios.includes(sc)} onChange={() => toggleScenario(sc)} />
                  {sc}
                </label>
              ))}
            </fieldset>
            {NUMBERS.map(([field, label]) => {
              const tagged = req[field] as Tagged | undefined
              return (
                <div key={field} className="flex gap-2 items-center text-[12px]">
                  <label htmlFor={`island-${field}`} className="w-56">{label}</label>
                  <input
                    id={`island-${field}`} type="number" className={`${INPUT} w-28`}
                    value={tagged?.value ?? ''}
                    onChange={e => setReq(field, e.target.value === '' ? undefined
                      : { value: Number(e.target.value), source: tagged?.source ?? 'assumed' })}
                  />
                  <select
                    aria-label={`${label} source`} className={INPUT} disabled={!tagged}
                    value={tagged?.source ?? 'assumed'}
                    onChange={e => tagged && setReq(field, { ...tagged, source: e.target.value as IslandSource })}
                  >
                    {SOURCES.map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                  {tagged?.source === 'assumed' && <Tag tone="warn">assumed</Tag>}
                </div>
              )
            })}
            <label className="flex gap-2 items-center text-[12px]">
              <input type="checkbox" checked={!!req.linearised_uc}
                     onChange={e => setReq('linearised_uc', e.target.checked)} />
              Linearised unit commitment for the fixed-size gensets (u may be fractional)
            </label>
            {Object.entries(config!.units).filter(([, u]) => u.control !== undefined).map(([unit, u]) => (
              <div key={unit} className="flex gap-2 items-center text-[12px]">
                <label htmlFor={`island-control-${unit}`} className="w-56">Control mode of {unit}</label>
                <select id={`island-control-${unit}`} className={INPUT} value={u.control}
                        onChange={e => setUnitControl(unit, e.target.value)}>
                  {CONTROLS.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            ))}
          </div>
        )}

        <textarea
          aria-label="Island config"
          className={`${INPUT} w-full font-mono text-[11.5px] h-[200px]`}
          spellCheck={false}
          value={text}
          onChange={e => {
            setText(e.target.value)
            try { setConfig(JSON.parse(e.target.value)); setTextError(null) }
            catch { setTextError('not valid JSON yet') }
          }}
        />
        {textError && <p className="text-[11px] text-danger">{textError}</p>}
        <div className="flex justify-end gap-2 mt-2">
          <Btn onClick={() => save.mutate()} disabled={save.isPending || !dirty || !!textError || !config}>
            <Save size={13} /> Save island config
          </Btn>
          <Btn onClick={() => run.mutate()} disabled={run.isPending || !saved || dirty}>
            <Play size={13} /> {run.isPending ? 'Solving the variants…' : 'Run cost-of-islanding study'}
          </Btn>
        </div>

        {/* The report, the proposed cuts and the grid-forming settings are results: they
            live in Results › Control (W10 R3). The cuts' loop is started here. */}
        <p className="text-[12px] text-muted mt-3" data-testid="island-results-link">
          The island report, the frequency cuts and the grid-forming settings are in Results › Control.{' '}
          <button type="button" className="text-accent hover:underline" data-testid="island-results-open"
            onClick={() => showResultsTab('island')}>Open Island & ride-through ›</button>
        </p>
        {saved && <CampusIslandCutsSection name={name} mode="actions" />}
      </PageSection>
    </div>
  )
}

export function IslandReportView({ report }: { report: IslandReport | undefined }) {
  if (!report) return null
  // A table with no rows says nothing a header can; they are named in one line instead.
  const tables = report.tables.filter(t => t.rows.length > 0)
  const empty = report.tables.filter(t => t.rows.length === 0)
  return (
    <div className="mt-4" data-testid="island-report">
      {report.missing.length > 0 && (
        <p className="text-[12px] text-muted mb-2" data-testid="island-missing">
          Not yet available: {report.missing.join(', ')}.
        </p>
      )}
      {empty.length > 0 && (
        <p className="text-[12px] text-muted mb-2" data-testid="island-empty">
          Nothing to report for this run: {empty.map(t => inSentence(t.title)).join('; ')}.
        </p>
      )}
      {tables.map((t, i) => (
        <div key={t.id}>
          {t.group !== tables[i - 1]?.group && (
            <h3 className="text-[13px] font-semibold mt-3 mb-1" data-testid="island-group">
              {GROUP_TITLES[t.group] ?? t.group}
            </h3>
          )}
          <ReportTable table={t} />
        </div>
      ))}
    </div>
  )
}
