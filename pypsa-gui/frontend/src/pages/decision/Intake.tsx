// The intake (spec §3 screen 1, plan S8): one thing per page in the shared
// StepShell — site → what exists today → consumption → goal → horizon and
// perspective (read-only in MVP-1, with its source) → check your answers.
//
// Two modes. A NEW study is answered before it exists (`draft`): creation
// builds the study's own base project and its grid-only network, which needs
// the mandatory answers, so "Check your answers" creates it. An existing study
// saves one step at a time (`edit`, spec decision 18: per-step apply).
//
// An uploaded load goes through the backend's parser and time-series QA
// (`POST .../studies/preview`, the same code the pack runs): the page shows
// what was read — hours, unit, yearly MWh, peak — and every QA finding in
// words, before anything is created or run.
import { useEffect, useState } from 'react'
import type { IntakePreview, StudyError, StudyIntake, StudyLibrary } from '../../api/decisionStudies'
import { decisionStudiesApi, studyError } from '../../api/decisionStudies'
import { uploadFile, UploadError } from '../../api/uploads'
import { StepShell } from '../modelHorizon/StepShell'

/** How long an answer must settle before the load preview is posted (F4). */
export const PREVIEW_DEBOUNCE_MS = 400
import {
  BASIS_SENTENCE, INTAKE_NAV_LABEL, INTAKE_STEP_LABELS, UI_LABELS, VOCAB, errorCopy, helpFor, type IntakeStepId,
} from '../../utils/decisionVocabulary'
import { isLeapYear, missingInputs, sameAnswer, valueText } from './decisionModel'
import { Banner, Button, Card, Refusal } from './DecisionUi'

const STEPS: IntakeStepId[] = ['site', 'existing', 'load', 'goal', 'horizon', 'check']
/** The pack's default year when none is given (`packs._snapshots`). */
const DEFAULT_YEAR = 2025
const INPUT = 'px-2 py-1 border border-border rounded text-[12px] w-48'

type Site = NonNullable<StudyIntake['site']>
type Load = NonNullable<StudyIntake['load']>

function Field({ label, unit, technical, children }: { label: string; unit?: string; technical?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span>{label}{unit ? ` (${unit})` : ''}</span>
      {technical && <span className="font-mono text-[10px] text-muted">{technical}</span>}
      {children}
    </label>
  )
}

export function LoadCheck({ preview, error }: { preview: IntakePreview | null; error: StudyError | null }) {
  if (error) return <Refusal error={error} testId="load-check" />
  if (!preview) return null
  const l = preview.load
  if (l.status !== 'ok') {
    const copy = errorCopy(l.error_kind)
    return (
      <Banner tone="warn" testId="load-check" title={copy.title}>
        <span>{copy.action}</span><span className="text-[11px] text-muted">{l.message}</span>
      </Banner>
    )
  }
  return (
    <div data-testid="load-check" className="flex flex-col gap-1.5">
      <p>
        Read {l.hours} hourly values in {l.unit}{l.has_timestamps ? ', timestamps checked' : ', without timestamps'}:
        {' '}yearly consumption <strong className="font-mono">{valueText(l.annual_mwh, 'MWh')}</strong>,
        peak <strong className="font-mono">{valueText(l.peak_mw, 'MW')}</strong>.
      </p>
      {l.peak_exceeds_connection && (
        <Banner tone="warn" testId="load-peak" title={`The peak (${valueText(l.peak_mw, 'MW')}) is above your grid connection (${valueText(l.connection_mw, 'MW')}).`}>
          <span>The grid-only baseline could not serve it. Check the file’s unit (kW or MW) and the connection limit.</span>
        </Banner>
      )}
      {l.warnings.map(w => (
        <Banner key={w.code} tone="warn" title="Please check the file." testId={`load-warning-${w.code}`}>
          <span>{w.message}</span>
        </Banner>
      ))}
      {l.notes.filter(n => !n.startsWith('load_upload_qa_')).map(n => (
        <p key={n} className="text-[11px] text-muted">{helpFor(n).text}</p>
      ))}
    </div>
  )
}

export default function Intake({
  mode, project, initial, library, onSaveStep, onCreate, onDraftChange, saving, error, hasRun,
  initialStep = 'site', names,
}: {
  mode: 'draft' | 'edit'
  /** The project whose uploads the load goes to and the preview reads. */
  project: string
  initial: StudyIntake
  library: StudyLibrary | null
  onSaveStep?: (key: 'site' | 'load', value: unknown) => void
  onCreate?: (intake: StudyIntake, names: { name: string; baseName: string }) => void
  /** Draft mode: every answer change, so the draft survives the panel closing. */
  onDraftChange?: (intake: StudyIntake) => void
  saving: boolean
  error: StudyError | null
  hasRun?: boolean
  initialStep?: IntakeStepId
  names?: { name: string; baseName: string }
}) {
  const [step, setStep] = useState<IntakeStepId>(initialStep)
  // No defaults are written into the answers: an untouched step saves exactly what
  // was there (a re-save must not read as a changed intake and force a re-run).
  const [site, setSite] = useState<Site>(initial.site ?? {})
  const [load, setLoad] = useState<Load>(initial.load ?? {})
  const [name, setName] = useState(names?.name ?? 'Battery at my site')
  const [baseName, setBaseName] = useState(names?.baseName ?? '')
  const [uploadErr, setUploadErr] = useState<StudyError | null>(null)
  const [preview, setPreview] = useState<IntakePreview | null>(null)
  const [previewErr, setPreviewErr] = useState<StudyError | null>(null)
  const [uploading, setUploading] = useState(false)

  const intake: StudyIntake = { ...initial, site, load }
  const missing = missingInputs(intake)
  // Gate S8 [S4]: a draft's answers live in the decision store.
  useEffect(() => {
    if (mode === 'draft') onDraftChange?.({ ...initial, site, load })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, site, load])
  // Gate S8 BC-S8-4: in edit mode, what differs from the STORED answers is unsaved.
  const siteDirty = mode === 'edit' && !sameAnswer(site, initial.site)
  const loadDirty = mode === 'edit' && !sameAnswer(load, initial.load)
  const stepKey = (st: IntakeStepId): 'site' | 'load' | null =>
    st === 'site' || st === 'existing' ? 'site' : st === 'load' ? 'load' : null
  const dirty = (key: 'site' | 'load' | null) => (key === 'site' ? siteDirty : key === 'load' ? loadDirty : false)
  const saveKey = (key: 'site' | 'load') => onSaveStep?.(key, key === 'site' ? site : load)
  const year = Number(site.year ?? DEFAULT_YEAR)
  const leap = Number.isFinite(year) && isLeapYear(year)

  // The load as the pack will read it — re-checked whenever what it depends on changes.
  const loadKey = JSON.stringify([load.source, load.upload_id, load.csv_text?.length, load.filename, load.unit,
    load.profile, load.annual_mwh, site.year, site.connection_mw])
  useEffect(() => {
    const ready = load.source === 'upload' ? !!(load.upload_id || load.csv_text)
      : load.source === 'sector_profile' ? Number(load.annual_mwh) > 0 : false
    if (!ready || leap) { setPreview(null); return }
    let live = true
    // Plan F1-F, F4: debounced. Each change used to post the whole load file
    // (up to the 25 MB cap) per keystroke in the site fields (gate S8
    // [N-v2-1]); only an answer that settles for PREVIEW_DEBOUNCE_MS is sent.
    const timer = setTimeout(() => {
      decisionStudiesApi.preview(project, { site, load })
        .then(p => { if (live) { setPreview(p); setPreviewErr(null) } })
        .catch(e => { if (live) { setPreview(null); setPreviewErr(studyError(e)) } })
    }, PREVIEW_DEBOUNCE_MS)
    return () => { live = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadKey, project])

  const onFile = async (file: File | undefined) => {
    if (!file) return
    setUploading(true); setUploadErr(null)
    try {
      if (mode === 'draft') {
        // Gate S8 BC-S8-5: before the study exists its load file stays in the
        // browser; creation writes it into the study's OWN new project.
        const text = await file.text()
        setLoad({ source: 'upload', csv_text: text, filename: file.name, unit: load.unit })
        return
      }
      const meta = await uploadFile(project, file)
      setLoad({ source: 'upload', upload_id: meta.file_id, filename: meta.filename, unit: load.unit })
    } catch (e) {
      setUploadErr(e instanceof UploadError
        ? { status: e.status, code: e.detail.error_kind, message: e.detail.message }
        : studyError(e))
    } finally {
      setUploading(false)
    }
  }

  const i = STEPS.indexOf(step)
  // Gate S8 BC-S8-4: leaving a step in edit mode (Next or the rail) saves it
  // when it changed; a leap year blocks the move with its reason on screen.
  const go = (to: IntakeStepId) => {
    const key = stepKey(step)
    if (mode === 'edit' && key && dirty(key)) {
      if (key === 'site' && leap) return
      saveKey(key)
    }
    setStep(to)
  }
  const next = () => go(STEPS[Math.min(i + 1, STEPS.length - 1)])
  const saveBar = (key: 'site' | 'load' | null) => (
    <div className="flex gap-2 pt-2">
      {mode === 'edit' && key && (
        <Button kind="primary" disabled={saving || (key === 'site' && leap)}
          onClick={() => saveKey(key)}>Save this step</Button>
      )}
      {step !== 'check' && <Button onClick={next}>Next</Button>}
    </div>
  )
  // The summary shows what is STORED in edit mode (what a run would use), and
  // lists any edit not saved yet; in draft mode nothing is stored yet.
  const shownSite: Site = mode === 'edit' ? (initial.site ?? {}) : site
  const shownLoad: Load = mode === 'edit' ? (initial.load ?? {}) : load
  const siteFields: Array<[keyof Site, string, (v: unknown) => string]> = [
    ['zone', VOCAB.zone.label, v => String(v ?? '—')],
    ['year', VOCAB.year.label, v => String(v ?? DEFAULT_YEAR)],
    ['connection_mw', VOCAB.connection_limit.label, v => valueText((v as number | undefined) ?? null, 'MW')],
    ['latitude', VOCAB.latitude.label, v => (v == null ? '—' : `${v}°`)],
  ]
  const unsaved = [
    ...(siteDirty ? siteFields.filter(([k]) => !sameAnswer({ v: site[k] }, { v: initial.site?.[k] }))
      .map(([k, label, fmt]) => `${label}: ${fmt(site[k])}`) : []),
    ...(loadDirty ? [`Consumption: ${loadText(load)}`] : []),
  ]
  function loadText(l: Load): string {
    return l.source === 'upload' ? `Uploaded: ${l.filename ?? l.upload_id ?? '—'}`
      : l.source === 'sector_profile' ? `${profiles.find(p => p.profile_id === l.profile)?.label ?? l.profile}, ${valueText(l.annual_mwh ?? null, 'MWh')} a year`
      : '—'
  }

  const profiles = library?.load_profiles ?? []
  return (
    <StepShell<IntakeStepId>
      steps={STEPS} current={step} onSelect={go} labels={INTAKE_STEP_LABELS} navLabel={INTAKE_NAV_LABEL}
      title={`Step ${i + 1} of ${STEPS.length} — ${INTAKE_STEP_LABELS[step]}`}>
      <div className="flex flex-col gap-3 text-[12px]">
        {mode === 'edit' && hasRun && (
          <Banner tone="info" title="The study has been run.">
            <span>Changing an answer means running the study again: its findings and report will say they are out of date.</span>
          </Banner>
        )}
        {error && <Refusal error={error} />}

        {step === 'site' && (
          <Card title="Where is the site?">
            <Field label={VOCAB.zone.label} technical={VOCAB.zone.technical}>
              <input className={INPUT} aria-label={VOCAB.zone.label} value={site.zone ?? ''} placeholder="DE"
                onChange={e => setSite({ ...site, zone: e.target.value })} />
            </Field>
            <Field label={VOCAB.year.label} technical={VOCAB.year.technical}>
              <input className={INPUT} type="number" aria-label={VOCAB.year.label} value={site.year ?? ''} placeholder={String(DEFAULT_YEAR)}
                onChange={e => setSite({ ...site, year: e.target.value === '' ? undefined : Number(e.target.value) })} />
            </Field>
            {leap && <p className="text-warn">{errorCopy('leap_year_unsupported').title} {errorCopy('leap_year_unsupported').action}</p>}
            <Field label={VOCAB.latitude.label} unit={VOCAB.latitude.unit} technical={VOCAB.latitude.technical}>
              <input className={INPUT} type="number" aria-label={VOCAB.latitude.label} value={site.latitude ?? ''}
                onChange={e => setSite({ ...site, latitude: e.target.value === '' ? undefined : Number(e.target.value) })} />
            </Field>
            <p className="text-[11px] text-muted">The latitude only shapes the synthetic PV profile; 51° is used when it is empty.</p>
            {saveBar('site')}
          </Card>
        )}

        {step === 'existing' && (
          <Card title="What exists today?">
            <Field label={VOCAB.connection_limit.label} unit={VOCAB.connection_limit.unit} technical={VOCAB.connection_limit.technical}>
              <input className={INPUT} type="number" aria-label={VOCAB.connection_limit.label} value={site.connection_mw ?? ''}
                onChange={e => setSite({ ...site, connection_mw: e.target.value === '' ? undefined : Number(e.target.value) })} />
            </Field>
            <p className="text-[11px] text-muted">
              The most your site can import or export through its grid connection. Existing on-site generation and
              storage are not modelled in this version: the baseline is grid supply on your tariff.
            </p>
            {saveBar('site')}
          </Card>
        )}

        {step === 'load' && (
          <Card title="How much electricity does the site use?">
            <fieldset className="flex flex-col gap-1.5">
              <label className="flex items-center gap-2">
                <input type="radio" name="load-source" checked={load.source === 'upload'}
                  onChange={() => setLoad({ source: 'upload', unit: load.unit })} />
                Upload your metered load (CSV, one value per hour)
              </label>
              <label className="flex items-center gap-2">
                <input type="radio" name="load-source" checked={load.source === 'sector_profile'}
                  onChange={() => setLoad({ source: 'sector_profile', profile: profiles[0]?.profile_id, annual_mwh: load.annual_mwh })} />
                Use a typical profile for your sector
              </label>
            </fieldset>
            {load.source === 'upload' && (
              <div className="flex flex-col gap-2">
                <input type="file" accept=".csv,text/csv,text/plain" aria-label={UI_LABELS.loadFile}
                  onChange={e => void onFile(e.target.files?.[0])} disabled={uploading} />
                {load.filename && <span className="text-muted">File: {load.filename}{mode === 'draft' ? ' (kept in this browser until the study is created)' : ''}</span>}
                <Field label={UI_LABELS.fileUnit}>
                  <select className={INPUT} aria-label={UI_LABELS.fileUnit} value={load.unit ?? ''}
                    onChange={e => setLoad({ ...load, unit: (e.target.value || undefined) as Load['unit'] })}>
                    <option value="">{UI_LABELS.unitFromHeader}</option>
                    <option value="kW">kW</option>
                    <option value="MW">MW</option>
                  </select>
                </Field>
                <p className="text-[11px] text-muted">
                  One column of 8760 hourly values, optionally after a timestamp column. The timestamps, the unit and
                  the shape are checked before the study uses the file. If your meter’s local-time timestamps are
                  refused (daylight saving time adds and drops an hour), remove the timestamp column: the values are
                  then read in order from 1 January.
                </p>
                {uploadErr && <Refusal error={uploadErr} testId="upload-error" />}
              </div>
            )}
            {load.source === 'sector_profile' && (
              <div className="flex flex-col gap-2">
                <Field label={UI_LABELS.sectorProfile}>
                  <select className={INPUT} aria-label={UI_LABELS.sectorProfile} value={load.profile ?? ''}
                    onChange={e => setLoad({ ...load, profile: e.target.value })}>
                    {profiles.map(p => <option key={p.profile_id} value={p.profile_id}>{p.label}</option>)}
                  </select>
                </Field>
                <Field label={VOCAB.annual_mwh.label} unit={VOCAB.annual_mwh.unit} technical={VOCAB.annual_mwh.technical}>
                  <input className={INPUT} type="number" aria-label={VOCAB.annual_mwh.label} value={load.annual_mwh ?? ''}
                    onChange={e => setLoad({ ...load, annual_mwh: e.target.value === '' ? undefined : Number(e.target.value) })} />
                </Field>
                <p className="text-[11px] text-muted">
                  {profiles.find(p => p.profile_id === load.profile)?.note
                    ?? 'A synthetic shape scaled to your yearly consumption; the study stays at screening maturity until you upload a metered load.'}
                </p>
              </div>
            )}
            <LoadCheck preview={preview} error={previewErr} />
            {saveBar('load')}
          </Card>
        )}

        {step === 'goal' && (
          <Card title="What do you want to find out?">
            <p>Whether a battery lowers your electricity bill enough to pay for itself, and what size to build.</p>
            <p className="text-[11px] text-muted">Other goals (hours of backup, CO2) are not in this version.</p>
            {saveBar(null)}
          </Card>
        )}

        {step === 'horizon' && (
          <Card title={INTAKE_STEP_LABELS.horizon}>
            <p>Horizon: the battery storage’s lifetime from the assumptions library, with the inverter replaced at the end of its own lifetime.</p>
            <p>Perspective: the site owner — the savings on your own bill.</p>
            <p>Money: {BASIS_SENTENCE}{library ? `, in EUR of ${library.currency_year}` : ''}.</p>
            <p className="text-[11px] text-muted">
              Fixed in this version. Source: the study’s finance defaults ({library?.library_version ?? 'assumptions library'});
              the discount rate is an assumption you can change later.
            </p>
            {saveBar(null)}
          </Card>
        )}

        {step === 'check' && (
          <Card title={INTAKE_STEP_LABELS.check}>
            <dl data-testid="intake-summary" className="grid grid-cols-[12rem_1fr_auto] gap-x-3 gap-y-1.5">
              <dt className="text-muted">Site</dt><dd>{shownSite.zone || '—'}, {shownSite.year ?? DEFAULT_YEAR}</dd>
              <dd><button type="button" className="text-accent" onClick={() => setStep('site')}>Change</button></dd>
              <dt className="text-muted">{VOCAB.connection_limit.label}</dt><dd>{valueText(shownSite.connection_mw ?? null, 'MW')}</dd>
              <dd><button type="button" className="text-accent" onClick={() => setStep('existing')}>Change</button></dd>
              <dt className="text-muted">Consumption</dt>
              <dd>{loadText(shownLoad)}</dd>
              <dd><button type="button" className="text-accent" onClick={() => setStep('load')}>Change</button></dd>
            </dl>
            {mode === 'edit' && unsaved.length > 0 && (
              <Banner tone="warn" testId="intake-unsaved" title={`${UI_LABELS.unsaved}: the study still uses the answers above.`}>
                <ul className="list-disc pl-5">{unsaved.map(u => <li key={u}>{u}</li>)}</ul>
                <span className="pt-1">
                  <Button kind="primary" disabled={saving || leap}
                    onClick={() => { if (siteDirty) saveKey('site'); if (loadDirty) saveKey('load') }}>
                    {UI_LABELS.saveChanges}
                  </Button>
                </span>
              </Banner>
            )}
            {missing.length > 0 && (
              <Banner tone="warn" testId="intake-missing" title="Some answers are missing.">
                <span>{missing.map(m => m === 'site' ? 'the site' : m === 'connection_limit' ? 'the grid connection limit' : 'the consumption').join(', ')}</span>
              </Banner>
            )}
            {mode === 'draft' && (
              <div className="flex flex-col gap-2">
                <Field label={UI_LABELS.studyName}>
                  <input className={INPUT} aria-label={UI_LABELS.studyName} value={name} onChange={e => setName(e.target.value)} />
                </Field>
                <Field label={UI_LABELS.baseProjectName}>
                  <input className={INPUT} aria-label={UI_LABELS.baseProjectName} value={baseName} placeholder={name}
                    onChange={e => setBaseName(e.target.value)} />
                </Field>
                <p className="text-[11px] text-muted">
                  The study creates its own new project; none of your existing projects is changed. You choose the tariff,
                  the options and the assumptions next.
                </p>
                <Button kind="primary" disabled={saving || missing.length > 0 || leap || !name.trim()}
                  onClick={() => onCreate?.({ ...intake }, { name: name.trim(), baseName: (baseName || name).trim() })}>
                  {saving ? 'Creating…' : 'Create the study'}
                </Button>
              </div>
            )}
          </Card>
        )}
      </div>
    </StepShell>
  )
}
