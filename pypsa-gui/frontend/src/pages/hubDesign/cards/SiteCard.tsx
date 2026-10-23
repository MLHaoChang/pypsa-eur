// Step 2 — Site (guided-mode spec §5.3): what the study needs to know about
// the site, in words, read from GET /results/eh_readiness. Every gap has a fix
// button that hands a §5.7 request to the assistant; the one choice is the
// site type (the archetype pack).
//
// Off-grid (P24-BE gate N4): the pack islands the import, so a tagged tie is
// "normally open", not a grid connection with a rating, and its missing
// outage data is not a gap — the island is studied without it.
import { useEffect, useRef } from 'react'
import { useUIStore } from '../../../store/uiStore'
import type { EhArchetype, EhReadiness, EhTemplateMeta } from '../../../api/simulation'
import Kpi from '../../../components/Kpi'
import Explain from '../../../components/Explain'
import { kpiFigure } from '../../../utils/format'
import { useHubDesignStore } from '../hubDesignStore'
import { siteFixText, type SiteFix } from '../delegate'
import { CardShell, DelegateButton } from '../shared/CardShell'
import { LIVE_STUDY_EDIT, useLiveStudyRunning } from '../../../hooks/useLiveStudyRunning'
import { Term, type TermKey } from '../shared/Term'
import { useHubReadiness, useHubStudy, useHubTemplate } from '../useHubData'
import { displayName } from '../../../utils/displayName'
import { GridReadinessScorecard, summaryText, useGridReadiness, useScorecardOpen }
  from '../../../components/gridReadiness/GridReadinessScorecard'

export const SITE_TYPES: { id: EhArchetype; label: string }[] = [
  { id: 'strong_grid', label: 'Strong — import whenever needed' },
  { id: 'weak_flexible', label: 'Weak or limited' },
  { id: 'off_grid', label: 'None — the site runs as an island' },
]

const mw = (v: number) => `${Number(v.toPrecision(6))} MW`

interface Row { value: string; gap: boolean; fix?: string }

/** The four rows in words. Exported for the smoke's expectations. */
export function siteRows(r: EhReadiness, archetype: EhArchetype): Record<SiteFix, Row> {
  const island = archetype === 'off_grid'
  const links = r.import?.links ?? []
  // Plain names in Guided, never the raw ids (UX S6, G1).
  const names = (ids: string[]) => ids.map(displayName).join(', ')
  const grid: Row = island
    ? { value: links.length
      ? `${names(links)} — normally open; the study runs the site as an island`
      : 'none — the study runs the site as an island', gap: false }
    : links.length
      ? { value: `${names(links)}${r.import_p_nom_mw != null ? ` (${mw(r.import_p_nom_mw)}${
          r.import_firm_mw != null && r.import_firm_mw < r.import_p_nom_mw
            ? `; ${mw(r.import_firm_mw)} until fully energized` : ''})` : ''}`, gap: false }
      : { value: 'none tagged', gap: true, fix: siteFixText('grid') }

  const crit = r.critical_buses ?? []
  const critical: Row = crit.length
    ? { value: `${names(crit)} — everything on ${crit.length > 1 ? 'these buses' : 'this bus'} keeps power`, gap: false }
    : { value: 'none tagged', gap: true, fix: siteFixText('critical') }

  const scr = r.scr?.status
  const strength: Row = scr === 'ok' ? { value: 'data present', gap: false }
    : scr === 'not_required' ? { value: 'not needed for this site type', gap: false }
      : { value: `data missing${r.scr?.note ? ` — ${r.scr.note}` : ''}`, gap: true,
        fix: siteFixText('strength') }

  const ou = r.outage_units
  const missing = (ou?.missing ?? []).filter(m =>
    !(island && m.class === 'Link' && links.includes(m.name)))
  const outage: Row = !ou ? { value: 'not reported', gap: false }
    : missing.length
      ? { value: `${ou.count} units — ${missing.length} without outage data`, gap: true,
        fix: siteFixText('outage', missing.length) }
      : { value: `${ou.count} units`, gap: false }

  return { grid, critical, strength, outage }
}

const ROW_META: { k: SiteFix; term: TermKey; label: string }[] = [
  { k: 'grid', term: 'grid_connection', label: 'Connection point' },
  { k: 'critical', term: 'critical_load', label: 'Must stay on' },
  { k: 'strength', term: 'grid_strength', label: 'Grid strength' },
  { k: 'outage', term: 'outage_data', label: 'Outage data' },
]

function SiteRow({ k, term, label, row, liveStudy }: {
  k: SiteFix; term: TermKey; label: string; row: Row; liveStudy: boolean
}) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-[12px]">
      <span data-testid={`hub-site-${k}`} className="flex-1 min-w-[16rem]">
        <span className={`mr-1 ${row.gap ? 'text-warn' : 'text-success'}`}>{row.gap ? '!' : '✓'}</span>
        <Term k={term}>{label}</Term>: <span className="text-text">{row.value}</span>
      </span>
      {row.gap && row.fix && (
        <DelegateButton testId={`hub-site-fix-${k}`} text={row.fix} label="Fix with the assistant"
          disabled={liveStudy} disabledTitle={LIVE_STUDY_EDIT} />
      )}
    </li>
  )
}

/** "3.6 MW" at the tile rounding, without padding zeros. */
const mwText = (x: number) => `${kpiFigure(String(x), 'MW').value.replace(/\.0+$|(\.\d*?)0+$/, '$1')} MW`

/** The generic site's must-stay-on load as entered: "3.6 MW, 30 % of peak". */
export function genericCritical(t: EhTemplateMeta): string | null {
  const p = t.params
  if (!t.site_type || !p) return null
  const last = p.stages[p.stages.length - 1]
  return `${mwText(p.critical_share * last.peak_mw)} in ${last.year}, `
    + `${Math.round(p.critical_share * 100)} % of peak`
}

/** The generic site's load and connection schedule, read-only: everything
 *  the form asked is shown here, never asked again (G2). */
function SiteSchedule({ template }: { template: EhTemplateMeta }) {
  const setSiteSeed = useHubDesignStore(s => s.setSiteSeed)
  const setStep = useHubDesignStore(s => s.setStep)
  const p = template.params
  if (!p) return null
  const avail = new Map((template.energization ?? []).map(e => [e.year, e.available_import_mw]))
  return (
    <section data-testid="hub-site-schedule" aria-label="Load and grid connection"
      className="flex flex-col gap-2">
      <p data-testid="hub-site-kind" className="text-[12px] text-text">
        <span className="text-muted">Site type:</span> {template.site_type_label ?? template.site_type}
      </p>
      <div className="flex flex-wrap items-start gap-3">
        <table className="text-[12px]">
          <thead>
            <tr className="text-left text-muted">
              <th className="pr-4 font-normal">Year</th>
              <th className="pr-4 font-normal">Peak load (MW)</th>
              <th className="font-normal">Available from grid (MW)</th>
            </tr>
          </thead>
          <tbody>
            {p.stages.map(s => (
              <tr key={s.year} data-testid={`hub-site-stage-${s.year}`}>
                <td className="pr-4">{s.year}</td>
                <td className="pr-4">{mwText(s.peak_mw).replace(' MW', '')}</td>
                <td>{mwText(avail.get(s.year) ?? p.connection_mw).replace(' MW', '')}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="w-44">
          <Kpi testId="hub-site-connection" label="Connection limit" value={String(p.connection_mw)}
            unit="MW" basis="once fully energized" />
        </div>
      </div>
      <Explain origin="computed" text={[
        'Synthetic illustrative numbers unless you set them.',
        (template.ledger ?? []).some(r => r.item === 'power factor and load damping')
          ? 'The electrical studies assume power factor 0.98 and no load damping.' : '',
      ].filter(Boolean).join(' ')} />
      <button type="button" data-testid="hub-site-new-from-values"
        onClick={() => {
          setSiteSeed({ params: p, entered: template.entered ?? [] })
          setStep('start', { user: true })
        }}
        className="self-start text-[12px] text-accent hover:underline">
        Start a new project from these values
      </button>
    </section>
  )
}

export function SiteCard() {
  const { running, isPending: studyPending } = useHubStudy()
  const { template, isPending: templatePending } = useHubTemplate()
  const archetype = useHubDesignStore(s => s.archetype)
  const setArchetype = useHubDesignStore(s => s.setArchetype)
  const { readiness, isError } = useHubReadiness(template, running,
    !studyPending && !templatePending)
  const rows = readiness ? siteRows(readiness, archetype) : null
  // A generic site says what was entered, not a bus name (UX G2 note 7).
  const critical = template ? genericCritical(template) : null
  if (rows && critical) rows.critical = { value: critical, gap: false }
  const liveStudy = useLiveStudyRunning()

  return (
    <CardShell step="site" testId="hub-card-site" title="Site" next="goal">
      <label className="flex flex-col gap-1 text-[12px]">
        <span className="font-semibold text-text"><Term k="site_type">Grid connection</Term></span>
        <select data-testid="hub-site-type" value={archetype}
          disabled={running}
          onChange={e => setArchetype(e.target.value as EhArchetype)}
          className="max-w-sm rounded border border-border bg-bg px-2 py-1 text-[12px] text-text">
          {SITE_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      </label>

      {template?.site_type && <SiteSchedule template={template} />}

      <div data-testid="hub-site-readiness" className="flex flex-col gap-2">
        {running ? (
          <p data-testid="hub-site-paused" className="text-[12px] text-muted">
            Readiness is paused while the study runs
          </p>
        ) : rows ? (
          <ul className="flex flex-col gap-2">
            {ROW_META.map(m => <SiteRow key={m.k} {...m} row={rows[m.k]} liveStudy={liveStudy} />)}
          </ul>
        ) : (
          <p className="text-[12px] text-muted">
            {isError ? 'The site check could not be read — ask the assistant for help.'
              : 'Checking the site…'}
          </p>
        )}
      </div>

      {!running && <SiteGridReadiness template={template} />}
    </CardShell>
  )
}

/** The grid-readiness scorecard in Guided (DC 02), collapsed under the Site
 *  rows: plain words, no jump out of Guided, Ask on each row. Paused while
 *  the study runs, like the rows above. */
function SiteGridReadiness({ template }: { template: EhTemplateMeta | null }) {
  const { data } = useGridReadiness()
  const open = useScorecardOpen(s => s.open['guided:card'] ?? false)
  const toggle = useScorecardOpen(s => s.toggle)
  // "Readiness › Grid readiness ›" asks for the scorecard: scroll to it once mounted.
  const gridRequest = useUIStore(s => s.gridReadinessRequest)
  const sectionRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!gridRequest || !data) return
    sectionRef.current?.scrollIntoView({ block: 'start' })
    useUIStore.getState().clearGridReadinessRequest()
  }, [gridRequest, data])
  if (!data) return null
  // The ledger's own labels for the fields a row points at (UX: no item ids).
  const fieldLabels: Record<string, string> = {}
  for (const r of template?.ledger ?? []) {
    if (r.label && r.field) fieldLabels[r.field] = r.label
  }
  return (
    <section ref={sectionRef} data-testid="hub-site-grid-readiness" className="flex flex-col rounded border border-border">
      <button type="button" aria-expanded={open} data-testid="hub-site-grid-readiness-toggle"
        onClick={() => toggle('guided:card', false)}
        className="flex items-center gap-1 px-3 py-2 text-left text-[12px] text-text">
        <span className="font-semibold">Grid readiness:</span> {summaryText(data.summary, 'guided')}
      </button>
      {open && <GridReadinessScorecard data={data} mode="guided" fieldLabels={fieldLabels} />}
    </section>
  )
}
