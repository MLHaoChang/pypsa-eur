// The campus electrical study of a hub project (plan C6).
//
// A capacity-expansion (hub) project, solved and saved, is taken here to its
// electrical design:
// - transformer ratings;
// - reactive compensation at the PCC;
// - short circuit against the switchgear;
// - PCC grid-code compliance, at the critical hours, by AC load flow.
//
// Thin like GridspinePanel. Everything shown is READ from
// `GET /api/campus-electrical/{name}`, and every action is one request:
// draft, save, run. Refusals come back as the backend's message and render
// inline: another project kind (409), not saved or solved (422), a campus
// file that does not build (422, naming the field).
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, RefreshCw, Save, Wand2 } from 'lucide-react'
import {
  campusApi, isOtherKind,
  type CampusSettings, type CampusState, type CheckStatus, type ComplianceRow,
} from '../api/campusElectrical'
import { formatApiDetail } from '../api/client'
import { useUIStore } from '../store/uiStore'
import { Btn, Field, PageBody, PageSection, Tag } from '../components/PageKit'

export const CAMPUS_KEY = (name: string) => ['campusElectrical', 'state', name] as const

const INPUT = 'px-2.5 py-1.5 text-sm border border-border rounded focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20'
const DEFAULT_SETTINGS: CampusSettings = { k: 3, pf: null, profile: 'eu_rfg_dcc_ce', margin: 0.2, n_minus_1: true }

const CHECK_LABEL: Record<ComplianceRow['check'], string> = {
  pcc_reactive: 'PCC reactive power',
  pcc_voltage: 'PCC voltage',
  campus_voltage: 'Voltages inside the campus',
  transformer_loading: 'Transformer loading',
  switchgear: 'Switchgear short circuit',
}
const STATUS_TEXT: Record<CheckStatus, string> = {
  pass: 'pass', fail: 'fail', not_rated: 'not rated', not_rechecked: 'not re-checked',
}
const STATUS_TONE: Record<CheckStatus, 'ok' | 'err' | 'neutral' | 'warn'> = {
  pass: 'ok', fail: 'err', not_rated: 'neutral', not_rechecked: 'warn',
}

function errorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  return formatApiDetail(detail, (e as Error)?.message ?? 'Request failed')
}

function num(v: number | null | undefined, digits = 2): string {
  return v == null || !isFinite(v) ? '—' : v.toFixed(digits).replace(/\.?0+$/, m => (m.startsWith('.') ? '' : m))
}

function where(period: number | null, hour: number | null): string {
  if (period == null) return ''
  return hour == null ? `${period}` : `${period} · h${hour}`
}

export default function CampusElectricalPanel() {
  const currentProject = useUIStore(s => s.currentProject)
  if (!currentProject) {
    return (
      <PageBody>
        <PageSection title="Campus electrical">
          <p className="text-[12px] text-muted">
            Open a solved hub project to take it to its electrical design.
          </p>
        </PageSection>
      </PageBody>
    )
  }
  return <CampusView name={currentProject} />
}

function CampusView({ name }: { name: string }) {
  const qc = useQueryClient()
  const state = useQuery({ queryKey: CAMPUS_KEY(name), queryFn: () => campusApi.state(name), retry: false })

  if (state.isError && isOtherKind(state.error)) {
    return (
      <PageBody>
        <PageSection title="Campus electrical">
          <p className="text-[12px] text-muted">
            The campus electrical study is for capacity-expansion (hub) projects. {errorText(state.error)}
          </p>
        </PageSection>
      </PageBody>
    )
  }
  if (!state.data) {
    return (
      <PageBody>
        <PageSection title="Campus electrical">
          <p className="text-[12px] text-muted">{state.isError ? errorText(state.error) : 'Loading…'}</p>
        </PageSection>
      </PageBody>
    )
  }
  const refresh = (data?: CampusState) =>
    data ? qc.setQueryData(CAMPUS_KEY(name), data) : qc.invalidateQueries({ queryKey: CAMPUS_KEY(name) })

  return (
    <PageBody>
      <div data-testid="campus-intro">
        <PageSection title="Campus electrical" hint="From the solved hub to its electrical design">
          <p className="text-[12px] text-muted">
            A steady-state AC load-flow study at the hub's critical hours. It sizes the transformers and the
            reactive compensation, checks short circuit against the switchgear, and checks the PCC against
            the chosen grid code. It is not a dynamic study, and not a compliance certificate.
          </p>
        </PageSection>
      </div>
      <CampusFileSection name={name} state={state.data} onChange={refresh} />
      {state.data.campus_yaml && <RunSection name={name} state={state.data} onChange={refresh} />}
      {state.data.results && <ResultsSection state={state.data} />}
    </PageBody>
  )
}

function CampusFileSection({ name, state, onChange }: {
  name: string; state: CampusState; onChange: (d?: CampusState) => unknown
}) {
  const [text, setText] = useState(state.campus_yaml ?? '')
  const [refusal, setRefusal] = useState<string | null>(null)
  useEffect(() => { setText(state.campus_yaml ?? '') }, [state.campus_yaml])

  const draft = useMutation({
    mutationFn: (overwrite: boolean) => campusApi.draft(name, overwrite),
    onMutate: () => setRefusal(null),
    onSuccess: () => onChange(),
    onError: e => setRefusal(errorText(e)),
  })
  const save = useMutation({
    mutationFn: () => campusApi.save(name, text),
    onMutate: () => setRefusal(null),
    onSuccess: () => onChange(),
    onError: e => setRefusal(errorText(e)),
  })
  const has = state.campus_yaml != null
  const dirty = has && text !== state.campus_yaml

  return (
    <PageSection
      title="Campus file"
      hint={has ? 'Edit, then save; every value carries its source tag' : 'Draft it from the project, or paste your own'}
      right={has ? (
        <Btn
          onClick={() => { if (window.confirm('Draft again from the project? This replaces your edits.')) draft.mutate(true) }}
          disabled={draft.isPending}
        >
          <RefreshCw size={13} /> Draft again
        </Btn>
      ) : (
        <Btn variant="primary" onClick={() => draft.mutate(false)} disabled={draft.isPending}>
          <Wand2 size={13} /> Draft from project
        </Btn>
      )}
    >
      {refusal && <p className="text-[12px] text-danger mb-2">{refusal}</p>}
      {state.skipped.length > 0 && (
        <div className="text-[11.5px] text-muted mb-2">
          Left out of the draft: <ul className="list-disc ml-5">{state.skipped.map(s => <li key={s}>{s}</li>)}</ul>
        </div>
      )}
      <textarea
        aria-label="Campus file"
        className={`${INPUT} w-full font-mono text-[11.5px] h-[260px]`}
        spellCheck={false}
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="campus:\n  pcc: …"
      />
      <div className="flex justify-end mt-2">
        <Btn onClick={() => save.mutate()} disabled={save.isPending || !text.trim() || (has && !dirty)}>
          <Save size={13} /> Save campus file
        </Btn>
      </div>
    </PageSection>
  )
}

function RunSection({ name, state, onChange }: {
  name: string; state: CampusState; onChange: (d?: CampusState) => unknown
}) {
  const initial = state.settings ?? DEFAULT_SETTINGS
  const [k, setK] = useState(String(initial.k))
  const [pf, setPf] = useState(initial.pf == null ? '' : String(initial.pf))
  const [profile, setProfile] = useState(initial.profile)
  const [margin, setMargin] = useState(String(Math.round(initial.margin * 100)))
  const [n1, setN1] = useState(initial.n_minus_1)
  const [refusal, setRefusal] = useState<string | null>(null)

  const settings = (): CampusSettings => ({
    k: Number(k), pf: pf.trim() === '' ? null : Number(pf), profile, margin: Number(margin) / 100, n_minus_1: n1,
  })
  const run = useMutation({
    mutationFn: () => campusApi.run(name, settings()),
    onMutate: () => setRefusal(null),
    onSuccess: data => onChange(data),
    onError: e => setRefusal(errorText(e)),
  })

  return (
    <PageSection title="Study settings" hint="Applies to the next run">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Hours per criterion">
          <input aria-label="Hours per criterion" className={`${INPUT} w-[70px]`} inputMode="numeric" value={k} onChange={e => setK(e.target.value)} />
        </Field>
        <Field label="Connection-agreement power factor" hint="empty: the grid code's widest range">
          <input aria-label="Connection-agreement power factor" className={`${INPUT} w-[90px]`} inputMode="decimal" value={pf} placeholder="code"
                 onChange={e => setPf(e.target.value)} />
        </Field>
        <Field label="Grid code">
          <select aria-label="Grid code" className={`${INPUT} w-[320px]`} value={profile}
                  onChange={e => setProfile(e.target.value)}>
            {Object.entries(state.profiles).map(([key, title]) => <option key={key} value={key}>{title}</option>)}
          </select>
        </Field>
        <Field label="Design margin (%)">
          <input aria-label="Design margin (%)" className={`${INPUT} w-[70px]`} inputMode="numeric" value={margin} onChange={e => setMargin(e.target.value)} />
        </Field>
        <Field label="Transformer N-1" row>
          <input type="checkbox" aria-label="Transformer N-1" checked={n1} onChange={e => setN1(e.target.checked)} />
        </Field>
        <Btn variant="primary" onClick={() => run.mutate()} disabled={run.isPending}>
          <Play size={13} /> {run.isPending ? 'Running…' : 'Run study'}
        </Btn>
      </div>
      {refusal && <p className="text-[12px] text-danger mt-2">{refusal}</p>}
    </PageSection>
  )
}

function StatusTag({ s }: { s: CheckStatus }) {
  return <Tag tone={STATUS_TONE[s]}>{STATUS_TEXT[s]}</Tag>
}

function ResultsSection({ state }: { state: CampusState }) {
  const r = state.results!
  const req = r.requirement
  return (
    <>
      {state.stale && (
        <p className="text-[12px] text-warn border border-warn/40 rounded px-3 py-2">
          The campus file or the project has changed since this run. Run the study again before using these numbers.
        </p>
      )}
      <PageSection title="PCC compliance" hint={`as the campus is, and with the recommended measures`}>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="font-normal pr-3">Check</th><th className="font-normal pr-3">As is</th>
              <th className="font-normal pr-3">With measures</th><th className="font-normal pr-3">Worst</th>
              <th className="font-normal pr-3">Limit</th><th className="font-normal">Detail · rule</th>
            </tr>
          </thead>
          <tbody>
            {r.compliance.map(c => (
              <tr key={c.check} data-testid={`compliance-${c.check}`} className="border-t border-border">
                <td className="py-1 pr-3 whitespace-nowrap align-top">{CHECK_LABEL[c.check] ?? c.check}</td>
                <td className="pr-3 align-top"><StatusTag s={c.status_as_is} /></td>
                <td className="pr-3 align-top"><StatusTag s={c.status_with_measures} /></td>
                <td className="pr-3 whitespace-nowrap align-top tabular-nums">
                  {num(c.value, 3)} {c.unit} <span className="text-muted">{where(c.worst_period, c.worst_hour)}</span>
                </td>
                <td className="pr-3 whitespace-nowrap align-top tabular-nums text-muted">{num(c.limit, 3)} {c.unit}</td>
                <td className="align-top">
                  <div>{c.detail}</div>
                  <div className="text-[11px] text-muted flex items-start gap-1.5 mt-0.5">
                    <Tag tone={c.source === 'code' ? 'accent' : 'warn'}>{c.source}</Tag><span>{c.clause}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-[11px] text-muted mt-2">
          PCC reactive band ±{num(req.q_limit_mvar)} Mvar ({req.clause}); P_ref {num(req.p_ref_mw)} MW, {req.p_ref_from}.
        </p>
      </PageSection>

      <PageSection title="Transformers" hint="worst selected hour, intact and N-1, with the design margin">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="font-normal pr-3">Group</th><th className="font-normal pr-3">Now</th>
              <th className="font-normal pr-3">Peak S intact</th><th className="font-normal pr-3">Peak S N-1</th>
              <th className="font-normal pr-3">Required per unit</th><th className="font-normal pr-3">Recommended</th>
              <th className="font-normal">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {r.transformers.map(t => (
              <tr key={t.group} data-testid={`trafo-${t.group}`} className="border-t border-border">
                <td className="py-1 pr-3 whitespace-nowrap">{t.group}</td>
                <td className="pr-3 whitespace-nowrap tabular-nums">{t.units} × {num(t.unit_rating_mva)} MVA</td>
                <td className="pr-3 tabular-nums">{num(t.max_s_intact_mva)} MVA</td>
                <td className="pr-3 tabular-nums">{t.n_minus_1 ? `${num(t.max_s_n1_mva)} MVA` : '—'}</td>
                <td className="pr-3 tabular-nums">{num(t.required_unit_mva)} MVA</td>
                <td className="pr-3 whitespace-nowrap tabular-nums">{t.units} × {num(t.recommended_unit_mva)} MVA</td>
                <td><Tag tone={t.adequate ? 'ok' : 'err'}>{t.adequate ? 'adequate' : 'undersized'}</Tag></td>
              </tr>
            ))}
          </tbody>
        </table>
      </PageSection>

      <PageSection title="Reactive compensation" hint="after the inverters' own headroom, sized to the worst hour">
        <table className="w-full text-[12px]">
          <tbody>
            {r.compensation.map(c => (
              <tr key={c.direction} data-testid={`comp-${c.direction}`} className="border-t border-border first:border-0">
                <td className="py-1 pr-3 capitalize">{c.direction}</td>
                <td className="pr-3 tabular-nums">required {num(c.required_mvar)} Mvar</td>
                <td className="pr-3 tabular-nums font-semibold">recommended {num(c.recommended_mvar)} Mvar</td>
                <td className="text-muted">{where(c.worst_period, c.worst_hour)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </PageSection>

      <PageSection title="Short circuit" hint="IEC 60909, every installed unit energised, per period">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="font-normal pr-3">Period</th><th className="font-normal pr-3">Bus</th>
              <th className="font-normal pr-3">kV</th><th className="font-normal pr-3">Ik'' max</th>
              <th className="font-normal pr-3">ip</th><th className="font-normal pr-3">Ik'' min</th>
              <th className="font-normal pr-3">Rating</th><th className="font-normal">Verdict</th>
            </tr>
          </thead>
          <tbody>
            {r.short_circuit.map(f => (
              <tr key={`${f.bus}-${f.period}`} data-testid={`fault-${f.bus}-${f.period}`} className="border-t border-border">
                <td className="py-1 pr-3">{f.period}</td><td className="pr-3">{f.bus}</td>
                <td className="pr-3 tabular-nums">{num(f.vn_kv)}</td>
                <td className="pr-3 tabular-nums">{num(f.ikss_max_ka)} kA</td>
                <td className="pr-3 tabular-nums">{num(f.ip_max_ka)} kA</td>
                <td className="pr-3 tabular-nums">{num(f.ikss_min_ka)} kA</td>
                <td className="pr-3 tabular-nums">{f.rated_ka == null ? '—' : `${num(f.rated_ka)} kA`}</td>
                <td>{f.adequate == null ? <Tag tone="neutral">not rated</Tag>
                  : <Tag tone={f.adequate ? 'ok' : 'err'}>{f.adequate ? 'within rating' : 'over rating'}</Tag>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </PageSection>

      <PageSection title="Critical hours" count={r.selection.length} hint="selected per investment period">
        <ul className="text-[12px] space-y-0.5">
          {r.selection.map(s => (
            <li key={`${s.period}-${s.hour}`} className="tabular-nums">
              {s.period} · h{s.hour} <span className="text-muted">— {s.reasons.join(', ')}</span>
            </li>
          ))}
        </ul>
      </PageSection>
    </>
  )
}
