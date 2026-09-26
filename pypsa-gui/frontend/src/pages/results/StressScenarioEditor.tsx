// Class-C stress-scenario editor (P15) — the authoring UI for the per-project
// registry the B/C sweep reads (`services/adequacy/stress.py`). Whole-list
// replace, like the worksheet: every save PUTs the full list, and the
// backend's 422 is shown verbatim (it names the rule a scenario breaks).
// Client-side checks mirror the backend's bounds so most mistakes never
// leave the form; the backend stays the authority. Inline profile upload is
// deferred — a `profiles` scenario names a shipped `profile_pack`, and any
// inline series already on a scenario round-trips untouched.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import {
  resultsApi,
  type StressProfilePack,
  type StressScenario,
} from '../../api/simulation'
import { nk } from '../../utils/queryKeys'
import { blockerMessage } from './McPanel'

// Mirrors `stress.py` (MAX_SCENARIOS, _ID_RE, _validate bounds).
export const MAX_STRESS_SCENARIOS = 10
const ID_RE = /^[a-z0-9_-]{1,64}$/

export interface ScenarioDraft {
  id: string
  name: string
  kind: 'parametric' | 'profiles'
  frequency: string
  loadMult: string
  availMult: string
  pack: string
}

export const EMPTY_DRAFT: ScenarioDraft = {
  id: '', name: '', kind: 'parametric', frequency: '',
  loadMult: '1', availMult: '1', pack: '',
}

export function draftFrom(sc: StressScenario): ScenarioDraft {
  const num = (v: unknown) => (v == null ? '1' : String(v))
  return {
    id: sc.id,
    name: sc.name ?? '',
    kind: sc.kind,
    frequency: String(sc.frequency_per_year ?? ''),
    loadMult: num(sc.electrical_load_multiplier),
    availMult: num(sc.renewable_availability_multiplier),
    pack: sc.profile_pack ?? '',
  }
}

function hasInlineSeries(sc: StressScenario | undefined): boolean {
  return !!sc && (sc.loads_p_set != null || sc.generators_p_max_pu != null)
}

/** The first rule the draft breaks, or null. `others` are the ids of every
 * OTHER scenario in the list (duplicate check). */
export function validateDraft(
  d: ScenarioDraft, others: string[], base?: StressScenario,
): string | null {
  const id = d.id.trim()
  if (!ID_RE.test(id)) return 'Id must be 1–64 of a-z, 0-9, _ or -'
  if (others.includes(id)) return `Id '${id}' is already used`
  const f = Number(d.frequency)
  if (d.frequency.trim() === '' || !Number.isFinite(f) || f <= 0 || f > 365) {
    return 'Frequency must be in (0, 365] events per year'
  }
  if (d.kind === 'parametric') {
    const lm = Number(d.loadMult)
    if (d.loadMult.trim() === '' || !Number.isFinite(lm) || lm <= 0 || lm > 10) {
      return 'Load multiplier must be in (0, 10]'
    }
    const rm = Number(d.availMult)
    if (d.availMult.trim() === '' || !Number.isFinite(rm) || rm < 0 || rm > 1.5) {
      return 'Renewable availability multiplier must be in [0, 1.5]'
    }
  } else if (!d.pack && !hasInlineSeries(base)) {
    return 'A profiles scenario needs a profile pack'
  }
  return null
}

/** The scenario to store: the draft's fields over `base`, so fields the
 * editor does not own (inline series, provenance) survive an edit. Fields
 * of the other kind are dropped so a kind switch cannot leave stale bounds. */
export function scenarioFrom(d: ScenarioDraft, base?: StressScenario): StressScenario {
  const out: StressScenario = {
    ...(base ?? {}),
    id: d.id.trim(),
    kind: d.kind,
    frequency_per_year: Number(d.frequency),
  }
  if (d.name.trim()) out.name = d.name.trim()
  else delete out.name
  if (d.kind === 'parametric') {
    out.electrical_load_multiplier = Number(d.loadMult)
    out.renewable_availability_multiplier = Number(d.availMult)
    delete out.profile_pack
    delete out.loads_p_set
    delete out.generators_p_max_pu
  } else {
    delete out.electrical_load_multiplier
    delete out.renewable_availability_multiplier
    if (d.pack) out.profile_pack = d.pack
    else delete out.profile_pack
  }
  return out
}

function describe(sc: StressScenario): string {
  if (sc.kind === 'parametric') {
    const lm = sc.electrical_load_multiplier ?? 1
    const rm = sc.renewable_availability_multiplier ?? 1
    return `load ×${lm}, renewables ×${rm}`
  }
  if (sc.profile_pack) return `pack ${sc.profile_pack}`
  return hasInlineSeries(sc) ? 'inline series' : 'no profiles (incomplete)'
}

const INPUT = 'bg-bg border border-border rounded px-2 py-1 text-[10.5px] ' +
  'focus:outline-none focus:border-accent'

export default function StressScenarioEditor({ project }: { project: string | null }) {
  const qc = useQueryClient()
  const regKey = nk(project, 'adequacy', 'stress_scenarios')
  const { data: reg } = useQuery({
    queryKey: regKey,
    queryFn: () => resultsApi.getStressScenarios(project ?? ''),
    enabled: !!project,
  })
  const { data: packData } = useQuery({
    queryKey: nk(project, 'adequacy', 'stress_profile_packs'),
    queryFn: () => resultsApi.getStressProfilePacks(project ?? ''),
    enabled: !!project,
    staleTime: Infinity,
  })
  const scenarios = ((reg as { scenarios?: StressScenario[] } | undefined)
    ?.scenarios ?? []) as StressScenario[]
  const packs: StressProfilePack[] = packData?.packs ?? []

  // null = no form open; '' = adding; otherwise the id being edited.
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<ScenarioDraft>(EMPTY_DRAFT)
  const [serverError, setServerError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (next: StressScenario[]) =>
      resultsApi.putStressScenarios(project ?? '', next),
    onSuccess: () => {
      setServerError(null)
      setEditing(null)
      void qc.invalidateQueries({ queryKey: regKey })
    },
    onError: (e: unknown) => setServerError(blockerMessage(e)),
  })

  if (!project) return null

  const base = editing ? scenarios.find(s => s.id === editing) : undefined
  const others = scenarios.map(s => s.id).filter(id => id !== editing)
  const clientError = editing === null ? null : validateDraft(draft, others, base)
  const full = scenarios.length >= MAX_STRESS_SCENARIOS

  const open = (sc?: StressScenario) => {
    setServerError(null)
    setEditing(sc ? sc.id : '')
    setDraft(sc ? draftFrom(sc) : EMPTY_DRAFT)
  }
  const submit = () => {
    if (clientError) return
    const sc = scenarioFrom(draft, base)
    const next = editing
      ? scenarios.map(s => (s.id === editing ? sc : s))
      : [...scenarios, sc]
    save.mutate(next)
  }
  const remove = (id: string) => {
    setServerError(null)
    save.mutate(scenarios.filter(s => s.id !== id))
  }
  const set = (k: keyof ScenarioDraft) =>
    (e: { target: { value: string } }) => setDraft(p => ({ ...p, [k]: e.target.value }))

  return (
    <div className="border border-border rounded p-2" data-testid="stress-editor">
      <div className="flex items-center justify-between mb-1.5">
        <p className="text-[10px] font-semibold text-muted uppercase tracking-wide">
          Stress scenarios (class C) · {scenarios.length}/{MAX_STRESS_SCENARIOS}
        </p>
        <button onClick={() => open()} disabled={full || editing !== null}
          data-testid="stress-add"
          title={full ? `At most ${MAX_STRESS_SCENARIOS} scenarios` : undefined}
          className="inline-flex items-center gap-1 px-2 py-0.5 border border-border rounded text-[10px] text-muted hover:border-accent hover:text-accent disabled:opacity-50">
          <Plus size={11} /> New scenario
        </button>
      </div>

      {scenarios.length === 0 ? (
        <p className="text-[10px] text-muted">
          No stress scenarios. Each one is a whole-scenario re-solve in the
          B/C sweep: correlated weather and demand extremes that independent
          outage draws cannot compose.
        </p>
      ) : (
        <table className="w-full text-[10.5px]" data-testid="stress-list">
          <thead>
            <tr className="text-left text-muted border-b border-border">
              <th className="py-1 pr-2">Id</th>
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Kind</th>
              <th className="py-1 pr-2">Events/yr</th>
              <th className="py-1 pr-2">Stress</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody>
            {scenarios.map(sc => (
              <tr key={sc.id} className="border-b border-border/40">
                <td className="py-1 pr-2 font-mono">{sc.id}</td>
                <td className="py-1 pr-2">{sc.name ?? ''}</td>
                <td className="py-1 pr-2">{sc.kind}</td>
                <td className="py-1 pr-2 font-mono">{sc.frequency_per_year}</td>
                <td className="py-1 pr-2">{describe(sc)}</td>
                <td className="py-1 text-right whitespace-nowrap">
                  <button onClick={() => open(sc)} title={`Edit ${sc.id}`}
                    aria-label={`Edit ${sc.id}`} disabled={editing !== null}
                    className="text-muted hover:text-accent mr-1.5 disabled:opacity-50">
                    <Pencil size={11} />
                  </button>
                  <button onClick={() => remove(sc.id)} title={`Delete ${sc.id}`}
                    aria-label={`Delete ${sc.id}`} disabled={save.isPending}
                    className="text-muted hover:text-danger disabled:opacity-50">
                    <Trash2 size={11} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing !== null && (
        <div className="flex flex-wrap items-end gap-2 mt-2" data-testid="stress-form">
          <input className={`${INPUT} w-32 font-mono`} placeholder="id (a-z0-9_-)"
            aria-label="Scenario id" value={draft.id} onChange={set('id')} />
          <input className={`${INPUT} w-40`} placeholder="Name (optional)"
            aria-label="Scenario name" value={draft.name} onChange={set('name')} />
          <select className={INPUT} aria-label="Scenario kind" value={draft.kind}
            onChange={set('kind')}>
            <option value="parametric">parametric</option>
            <option value="profiles">profiles</option>
          </select>
          <input className={`${INPUT} w-24 font-mono`} type="number" step="any"
            placeholder="events/yr" aria-label="Events per year"
            value={draft.frequency} onChange={set('frequency')}
            title="Empirical events per year of this stress condition, (0, 365]." />
          {draft.kind === 'parametric' ? (
            <>
              <input className={`${INPUT} w-24 font-mono`} type="number" step="any"
                aria-label="Load multiplier" value={draft.loadMult}
                onChange={set('loadMult')}
                title="Electrical loads × this, (0, 10]." />
              <input className={`${INPUT} w-24 font-mono`} type="number" step="any"
                aria-label="Renewable availability multiplier" value={draft.availMult}
                onChange={set('availMult')}
                title="Profile-borne generators' availability × this, [0, 1.5]. 0 = no renewable output." />
            </>
          ) : (
            <select className={`${INPUT} w-56`} aria-label="Profile pack"
              value={draft.pack} onChange={set('pack')}>
              <option value="">
                {hasInlineSeries(base) ? '(keep inline series)' : '— choose a pack —'}
              </option>
              {packs.map(p => (
                <option key={p.id} value={p.id} disabled={!!p.error}
                  title={p.error ?? p.provenance ?? undefined}>
                  {p.error ? `${p.id} (unreadable)` : `${p.name ?? p.id}` +
                    (p.snapshots ? ` · ${p.snapshots} h` : '')}
                </option>
              ))}
            </select>
          )}
          <button onClick={submit} disabled={!!clientError || save.isPending}
            data-testid="stress-save"
            className="inline-flex items-center gap-1 px-2 py-1 bg-accent text-white rounded text-[10px] font-semibold hover:bg-accent/90 disabled:opacity-50">
            {editing ? 'Save' : 'Add'}
          </button>
          <button onClick={() => { setEditing(null); setServerError(null) }}
            className="px-2 py-1 border border-border rounded text-[10px] text-muted hover:text-text">
            Cancel
          </button>
          {clientError && (
            <span className="text-[10px] text-warn w-full" data-testid="stress-client-error">
              {clientError}
            </span>
          )}
        </div>
      )}
      {serverError && (
        <p className="text-[10px] text-danger mt-1.5" data-testid="stress-server-error">
          {serverError}
        </p>
      )}
      {draft.kind === 'profiles' && editing !== null && (
        <p className="text-[9.5px] text-muted mt-1.5">
          A profile pack swaps whole load / availability series; its length
          must match the network horizon when the sweep runs. Uploading your
          own series is not supported yet.
        </p>
      )}
    </div>
  )
}
