// The generic site form on the Guided Start card (generic-site E2E, G2): one
// parametrized site for anything that is not a data center. The site type
// fills every value from its preset (shown inline, each value tagged
// "default" until the user sets it); the user overrides any of them and
// creates the project with POST /projects/from_template/eh_site. Checks run
// beside each field, in the backend's words and on its field keys, so a
// server refusal lands on the same field. Nothing asked here is asked again
// later in Guided: the Site card shows it read-only.
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { projectsApi } from '../../../api/projects'
import { kpiFigure } from '../../../utils/format'
import { siteParamsRefusal, type TemplateCreate } from '../../../hooks/useCreateFromTemplate'
import {
  addStage, draftFromSite, firstErrorField, initialDraft, isDefault, presetSummary, removeStage, requestBody,
  setEnergization, setField, setStage, validate, withSiteType,
  type SiteDraft, type SiteForm, type SiteParams, type SiteTypeId,
} from '../genericSite'

// The tile rounding (3 significant figures) without its padding zeros: an
// input's preview reads "8 → 12 MW", not "8.00 → 12.0 MW".
const mwFigure = (x: number) => kpiFigure(String(x), 'MW').value.replace(/\.0+$|(\.\d*?)0+$/, '$1')

interface CreateMutation {
  mutate: (arg: TemplateCreate, opts?: { onError?: (e: unknown) => void }) => void
  isPending: boolean
}

export function GenericSiteForm({ create, disabledReason, onCancel, seed }: {
  create: CreateMutation
  /** Why creating is not possible now (a study runs), or null. */
  disabledReason: string | null
  onCancel: () => void
  /** Start from an existing site's values instead of a preset. */
  seed?: { params: SiteParams; entered: string[] } | null
}) {
  const form = useQuery({ queryKey: ['eh_site_form'], queryFn: projectsApi.getSiteTemplateForm,
    staleTime: Infinity })
  if (form.isError) {
    return (
      <p data-testid="site-form-unavailable" className="text-[12px] text-muted">
        The site form could not be loaded. Reload the page, or start from an example site.
      </p>
    )
  }
  if (!form.data) return <p className="text-[12px] text-muted">Loading the site form…</p>
  return <SiteFormBody form={form.data} create={create} disabledReason={disabledReason}
    onCancel={onCancel} seed={seed ?? null} />
}

function SiteFormBody({ form, create, disabledReason, onCancel, seed }: {
  form: SiteForm
  create: CreateMutation
  disabledReason: string | null
  onCancel: () => void
  seed: { params: SiteParams; entered: string[] } | null
}) {
  const [draft, setDraft] = useState<SiteDraft>(
    () => (seed ? draftFromSite(seed.params, seed.entered) : initialDraft(form)))
  const [server, setServer] = useState<{ field: string; message: string } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const errors = useMemo(() => {
    const e = validate(draft, form.bounds)
    return server && !e[server.field] ? { ...e, [server.field]: server.message } : e
  }, [draft, form.bounds, server])
  const nErrors = Object.keys(errors).length
  const update = (d: SiteDraft) => { setDraft(d); setServer(null) }
  const v = draft.values
  const bounds = form.bounds

  const field = (key: string) => ({
    field: key, error: errors[key], isDefault: isDefault(draft, key),
  })
  const focusFirst = () => {
    const k = firstErrorField(errors)
    const el = k ? rootRef.current?.querySelector<HTMLElement>(`[data-field="${k}"]`) : null
    el?.scrollIntoView?.({ block: 'center' })
    el?.focus()
  }
  const submit = () => {
    if (nErrors) { focusFirst(); return }
    create.mutate({ templateId: 'eh_site', params: requestBody(draft) }, {
      onError: (e: unknown) => { const r = siteParamsRefusal(e); if (r) setServer(r) },
    })
  }
  useEffect(() => { if (server) focusFirst() }, [server]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={rootRef} data-testid="site-form" className="flex flex-col gap-4 rounded-lg border border-accent/40 p-3">
      <Group title="Site type">
        <div role="radiogroup" aria-label="Site type" data-testid="site-type"
          className="grid gap-2 sm:grid-cols-3">
          {form.site_types.map(t => (
            <label key={t.id} data-testid={`site-type-${t.id}`}
              className={`flex cursor-pointer flex-col gap-0.5 rounded-lg border p-2 text-left focus-within:ring-2 focus-within:ring-accent
                ${v.site_type === t.id ? 'border-accent bg-accent/5' : 'border-border hover:border-accent/60'}`}>
              <input type="radio" name="site-type" value={t.id} className="sr-only"
                checked={v.site_type === t.id}
                onChange={() => update(withSiteType(form, draft, t.id as SiteTypeId))} />
              <span className="text-[12px] font-semibold text-text">{t.label}</span>
              <span className="text-[11px] leading-snug text-muted">{t.example}</span>
            </label>
          ))}
        </div>
        <p data-testid="site-preset-summary" className="text-[11.5px] text-muted">
          Starts from: {presetSummary(form.presets[v.site_type], mwFigure)}
        </p>
      </Group>

      <Group title="Load">
        {/* One row per stage: the year, its peak and what the grid makes
            available that year, side by side as the Site card's schedule. */}
        <table data-testid="site-stages" className="text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="pr-2 font-normal">Year</th>
              <th className="pr-2 font-normal">
                <span className="flex items-baseline gap-1.5">Peak load (MW) <DefaultTag on={isDefault(draft, 'stages')} /></span>
              </th>
              <th className="pr-2 font-normal">
                <span className="flex items-baseline gap-1.5">
                  Available from grid (MW) <DefaultTag on={isDefault(draft, 'energization')} />
                </span>
              </th>
              <th><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody>
            {v.stages.map((s, i) => (
              <tr key={i} className="align-top">
                <td className="pr-2">
                  <NumInput {...field(`stages[${i}].year`)} label={`Year of stage ${i + 1}`}
                    value={s.year} step={1} onChange={x => update(setStage(draft, i, 'year', x))} />
                </td>
                <td className="pr-2">
                  <NumInput {...field(`stages[${i}].peak_mw`)} label={`Peak load in stage ${i + 1} (MW)`}
                    value={s.peak_mw} onChange={x => update(setStage(draft, i, 'peak_mw', x))} />
                </td>
                <td className="pr-2">
                  <NumInput {...field(`energization[${i}].mw`)}
                    label={`Available from the grid in ${s.year} (MW)`}
                    value={v.energization[i]?.mw ?? NaN} onChange={x => update(setEnergization(draft, i, x))} />
                </td>
                <td className="pt-1">
                  {v.stages.length > 1 && (
                    <button type="button" data-testid={`site-stage-remove-${i}`}
                      onClick={() => update(removeStage(draft, i))}
                      className="text-[11px] text-muted hover:text-danger">Remove</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {v.stages.length < bounds.max_stages && (
          <button type="button" data-testid="site-stage-add" onClick={() => update(addStage(draft, bounds))}
            className="self-start text-[11.5px] text-accent hover:underline">Add a growth stage</button>
        )}
        <Hint>Each stage is the year the load grows to its new peak, up to {bounds.max_stages} stages, studied until {v.horizon_end}. Available from grid is what the utility has energized that year: the connection limit unless you set it.</Hint>
        <SelectField {...field('load_profile')} label="Load shape" value={v.load_profile}
          options={form.load_profiles.map(p => ({ id: p.id, label: p.label }))}
          onChange={x => update(setField(draft, 'load_profile', x))} />
        <NumField {...field('critical_share')} label="Must stay on (% of load)"
          hint="Typical 15–50 %: the load kept running through a grid outage."
          value={round(v.critical_share * 100)} onChange={x => update(setField(draft, 'critical_share', x / 100))} />
      </Group>

      <Group title="Grid connection">
        <NumField {...field('connection_mw')} label="Connection limit (MW)"
          hint="What the grid connection can carry once fully energized."
          value={v.connection_mw} onChange={x => update(setField(draft, 'connection_mw', x))} />
        <details className="text-[12px]">
          <summary className="cursor-pointer text-muted">More</summary>
          <div className="mt-2">
            <NumField {...field('fault_level_mva')} label="Grid strength — fault level (MVA)"
              hint="20 × the connection unless you know the utility's figure."
              value={v.fault_level_mva} onChange={x => update(setField(draft, 'fault_level_mva', x))} />
          </div>
        </details>
      </Group>

      <Group title="On site today">
        <div className="flex flex-wrap gap-3">
          <NumField {...field('existing.gensets.count')} label="Gensets" value={v.existing.gensets.count} step={1}
            onChange={x => update(setField(draft, 'existing.gensets.count', x))} />
          <NumField {...field('existing.gensets.unit_mw')} label="Unit size (MW)" value={v.existing.gensets.unit_mw}
            onChange={x => update(setField(draft, 'existing.gensets.unit_mw', x))} />
          <SelectField {...field('existing.gensets.fuel')} label="Fuel" value={v.existing.gensets.fuel}
            options={[{ id: 'gas', label: 'Gas' }, { id: 'diesel', label: 'Diesel' }]}
            onChange={x => update(setField(draft, 'existing.gensets.fuel', x))} />
        </div>
        <NumField {...field('existing.pv_mw')} label="Solar PV (MW)" value={v.existing.pv_mw}
          onChange={x => update(setField(draft, 'existing.pv_mw', x))} />
        <div className="flex flex-wrap gap-3">
          <NumField {...field('existing.battery.mw')} label="Battery power (MW)" value={v.existing.battery.mw}
            onChange={x => update(setField(draft, 'existing.battery.mw', x))} />
          <NumField {...field('existing.battery.mwh')} label="Battery energy (MWh)" value={v.existing.battery.mwh}
            onChange={x => update(setField(draft, 'existing.battery.mwh', x))} />
        </div>
        <div className="flex flex-wrap gap-3">
          <NumField {...field('existing.ups.mw')} label="UPS power (MW)" value={v.existing.ups.mw}
            hint="Sized to the must-stay-on load unless you set it; 0 for no UPS."
            onChange={x => update(setField(draft, 'existing.ups.mw', x))} />
          <NumField {...field('existing.ups.minutes')} label="UPS bridge time (min)" value={v.existing.ups.minutes}
            onChange={x => update(setField(draft, 'existing.ups.minutes', x))} />
        </div>
      </Group>

      <Group title="What the study may add">
        {/* Two columns (what | up to), so the inputs line up whatever the label. */}
        <div className="grid grid-cols-[7rem_auto] items-start gap-x-3 gap-y-2 text-[12px]">
          <span className="text-muted">Add</span>
          <span className="flex items-baseline gap-1.5 text-muted">
            Up to (MW) per stage <DefaultTag on={!draft.touched.some(p => p.startsWith('candidates.'))} />
          </span>
          {(['genset', 'bess', 'pv'] as const).map(k => (
            <Fragment key={k}>
              <label className="flex items-center gap-1.5 pt-1 text-text">
                <input type="checkbox" data-testid={`site-candidate-${k}`} checked={v.candidates[k].enabled}
                  onChange={e => update(setField(draft, `candidates.${k}.enabled`, e.target.checked))} />
                {CANDIDATE_LABEL[k]}
              </label>
              {v.candidates[k].enabled ? (
                <NumInput {...field(`candidates.${k}.max_mw`)} label={`${CANDIDATE_LABEL[k]}: up to (MW) per stage`}
                  value={v.candidates[k].max_mw}
                  onChange={x => update(setField(draft, `candidates.${k}.max_mw`, x))} />
              ) : <span className="pt-1 text-muted">not added</span>}
            </Fragment>
          ))}
        </div>
      </Group>

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" data-testid="site-create" onClick={submit}
          disabled={create.isPending || disabledReason !== null || nErrors > 0}
          title={disabledReason ?? undefined}
          className="rounded bg-accent px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">
          {create.isPending ? 'Creating…' : 'Create the site'}
        </button>
        {nErrors > 0 && (
          <button type="button" data-testid="site-attention" onClick={focusFirst}
            className="text-[12px] text-warn hover:underline">
            {nErrors === 1 ? '1 field needs attention' : `${nErrors} fields need attention`}
          </button>
        )}
        <button type="button" onClick={onCancel} className="text-[12px] text-muted hover:text-text">Cancel</button>
      </div>
    </div>
  )
}

const CANDIDATE_LABEL = { genset: 'Gensets', bess: 'Battery', pv: 'Solar PV' } as const

const round = (x: number) => Math.round(x * 1000) / 1000

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-[12.5px] font-semibold text-text">{title}</legend>
      {children}
    </fieldset>
  )
}

function Hint({ children }: { children: ReactNode }) {
  return <span className="text-[11px] leading-snug text-muted">{children}</span>
}

function DefaultTag({ on }: { on: boolean }) {
  return on ? <span data-testid="default-tag" className="text-[10.5px] text-muted">default</span> : null
}

function FieldError({ id, error }: { id: string; error?: string }) {
  return error ? <span id={id} role="alert" className="text-[11px] text-danger">{error}</span> : null
}

const errId = (field: string) => `site-err-${field.replace(/[^a-z0-9]/gi, '-')}`

/** A bare number input (table cells): its label is for screen readers. */
function NumInput({ field, label, value, onChange, error, step }: {
  field: string; label: string; value: number; onChange: (x: number) => void
  error?: string; isDefault?: boolean; step?: number
}) {
  return (
    <span className="flex flex-col gap-0.5">
      <input type="number" inputMode="decimal" aria-label={label} data-field={field}
        data-testid={`site-field-${field}`} step={step ?? 'any'}
        value={Number.isFinite(value) ? value : ''}
        aria-invalid={error ? true : undefined} aria-describedby={error ? errId(field) : undefined}
        onChange={e => onChange(e.target.value === '' ? NaN : Number(e.target.value))}
        className={`w-28 rounded border bg-bg px-2 py-1 text-[12px] text-text ${error ? 'border-danger' : 'border-border'}`} />
      <FieldError id={errId(field)} error={error} />
    </span>
  )
}

function NumField({ field, label, value, onChange, error, isDefault, hint, step }: {
  field: string; label: string; value: number; onChange: (x: number) => void
  error?: string; isDefault: boolean; hint?: string; step?: number
}) {
  return (
    <label className="flex flex-col gap-0.5 text-[12px]">
      <span className="flex items-baseline gap-2 text-text">{label} <DefaultTag on={isDefault} /></span>
      <input type="number" inputMode="decimal" data-field={field} data-testid={`site-field-${field}`}
        step={step ?? 'any'} value={Number.isFinite(value) ? value : ''}
        aria-invalid={error ? true : undefined} aria-describedby={error ? errId(field) : undefined}
        onChange={e => onChange(e.target.value === '' ? NaN : Number(e.target.value))}
        className={`w-32 rounded border bg-bg px-2 py-1 text-[12px] text-text ${error ? 'border-danger' : 'border-border'}`} />
      {hint && <Hint>{hint}</Hint>}
      <FieldError id={errId(field)} error={error} />
    </label>
  )
}

function SelectField({ field, label, value, options, onChange, error, isDefault }: {
  field: string; label: string; value: string; options: { id: string; label: string }[]
  onChange: (x: string) => void; error?: string; isDefault: boolean
}) {
  return (
    <label className="flex flex-col gap-0.5 text-[12px]">
      <span className="flex items-baseline gap-2 text-text">{label} <DefaultTag on={isDefault} /></span>
      <select data-field={field} data-testid={`site-field-${field}`} value={value}
        onChange={e => onChange(e.target.value)}
        className="max-w-xs rounded border border-border bg-bg px-2 py-1 text-[12px] text-text">
        {options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      <FieldError id={errId(field)} error={error} />
    </label>
  )
}
