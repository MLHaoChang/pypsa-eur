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
import CampusIslandCutsSection from './CampusIslandCutsSection'
import CampusIslandGfmSection from './CampusIslandGfmSection'

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
  study: 'Island study (I2)',
  hours: 'Island critical hours (I3b)',
  static: 'Island steady state and short circuit (I4, I5, I5b)',
  frequency: 'Island frequency (I6)',
}

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
            <tr>{table.columns.map(c => <th key={c} className="text-left font-medium px-2">{c}</th>)}<th /></tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i} data-assumed={assumed.has(i) ? 'yes' : 'no'}>
                {row.map((v, j) => {
                  const text = cell(v)
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
  const report = useQuery({ queryKey: ISLAND_REPORT_KEY(name), queryFn: () => campusApi.islandReport(name),
                            retry: false })
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

        <IslandReportView report={report.data} />
        {saved && <CampusIslandCutsSection name={name} />}
        {saved && <CampusIslandGfmSection name={name} />}
      </PageSection>
    </div>
  )
}

function IslandReportView({ report }: { report: IslandReport | undefined }) {
  if (!report) return null
  return (
    <div className="mt-4" data-testid="island-report">
      {report.missing.length > 0 && (
        <p className="text-[12px] text-muted mb-2" data-testid="island-missing">
          Not yet available: {report.missing.join(', ')}.
        </p>
      )}
      {report.tables.map((t, i) => (
        <div key={t.id}>
          {t.group !== report.tables[i - 1]?.group && (
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
